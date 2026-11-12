"""Función única de variables del modelo (DECISIONES §2.1 y §2.2): la misma para entrenar y servir.

Todo se calcula sobre una grilla completa (serie × día), donde una serie es un par (tienda,
familia) y un día sin dato vale NaN. La variable de la fecha `D` solo mira columnas anteriores a
`D` (desplazamientos ≥ 1 día), así que la venta del propio día nunca entra. El filtro principal
`fecha < D` ya se hizo en SQL (`features.panel`); esto es una segunda defensa.

Robustez a huecos: los lags se toman **a la fecha del último dato disponible** (último valor,
último valor del mismo día de la semana, media de los últimos 4 del mismo día de la semana) con
`dias_desde_ultimo_dato`, y las medias móviles promedian solo los días con dato. Al entrenar, una
parte de las filas usa variables calculadas sobre una copia del panel con huecos aleatorios por
tienda, y otra parte no tiene ninguna variable de nivel, para que el modelo aprenda a predecir
cuando faltan observaciones (o no hay ninguna).

Cierres: un día en que una tienda vendió 0 en todas sus familias (tienda 7, 2024-09-09 → 15) es
un dato faltante, no una venta 0: queda fuera del objetivo y de los lags.

Todo lo que mira hacia atrás está acotado a `VENTANA_DIAS`, el mismo rango que lee el panel al
predecir, así que entrenar y servir producen exactamente los mismos valores.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from novamercado.features.calendario import COLUMNAS_CALENDARIO, variables_calendario

#: Días hacia atrás que puede mirar cualquier variable; el panel lee exactamente estos días al
#: predecir (`features.panel.DIAS_PANEL`).
VENTANA_DIAS = 7 * 16
#: Semanas del mismo día de la semana que se revisan para hallar las 4 más recientes con dato.
SEMANAS_MISMO_DIA = VENTANA_DIAS // 7
K_MISMO_DIA = 4
LAGS = (1, 2, 7, 14, 21, 28)
MEDIAS_MOVILES = (7, 28, 91)
DIAS_PROMO_MEDIA = 28
TOPE_DIAS_APERTURA = 400

#: Enmascarado al entrenar: fracción de días en hueco por tienda, largo máximo del hueco y
#: fracción de filas que toman sus variables de la copia con huecos.
FRACCION_DIAS_HUECO = 0.2
LARGO_MAX_HUECO = 14
FRACCION_FILAS_ENMASCARADAS = 0.3
#: Fracción de filas sin ninguna variable de nivel (como una serie sin historia): el modelo
#: aprende un respaldo de tienda × familia × calendario (F5.6).
FRACCION_FILAS_SIN_HISTORIA = 0.05

CLAVES = ["tienda_id", "familia_id", "fecha"]

VARIABLES_NIVEL = [
    *[f"lag_{k}" for k in LAGS],
    "ultimo_dato",
    "dias_desde_ultimo_dato",
    "ultimo_mismo_dia",
    "media_mismo_dia_4",
    *[f"media_{w}" for w in MEDIAS_MOVILES],
    "razon_7_28",
]
VARIABLES_PROMO = ["en_promocion", "promo_desconocida", "promo_media_28"]
VARIABLES_FIJAS = ["tienda", "familia", "tipo_tienda", "dias_desde_apertura"]
FEATURES = [*VARIABLES_NIVEL, *VARIABLES_PROMO, *COLUMNAS_CALENDARIO, *VARIABLES_FIJAS]
CATEGORICAS = ["tienda", "familia", "tipo_tienda", "dow"]


@dataclass(frozen=True)
class Contexto:
    """Datos de referencia que necesitan las variables (se guardan dentro del modelo)."""

    tiendas: pd.DataFrame  # tienda_id, ciudad, tipo, fecha_apertura
    feriados: pd.DataFrame  # fecha, nombre, tipo, ambito
    familias: tuple[str, ...]

    @classmethod
    def desde_tablas(
        cls, tiendas: pd.DataFrame, feriados: pd.DataFrame, familias: list[str] | tuple[str, ...]
    ) -> Contexto:
        t = tiendas.loc[:, ["tienda_id", "ciudad", "tipo", "fecha_apertura"]].copy()
        t["tienda_id"] = t["tienda_id"].astype("int64")
        t["ciudad"] = t["ciudad"].astype(str)
        t["tipo"] = t["tipo"].astype(str)
        t["fecha_apertura"] = pd.to_datetime(t["fecha_apertura"])
        f = feriados.loc[:, ["fecha", "nombre", "tipo", "ambito"]].copy()
        f["fecha"] = pd.to_datetime(f["fecha"])
        return cls(
            tiendas=t.sort_values("tienda_id").reset_index(drop=True),
            feriados=f.reset_index(drop=True),
            familias=tuple(sorted(str(x) for x in familias)),
        )


# ----------------------------------------------------------------------------- utilidades


def marcar_cierres(panel: pd.DataFrame) -> pd.DataFrame:
    """Pone NaN en `ventas` los días en que una tienda vendió 0 en todas (≥ 2) sus familias."""
    v = panel["ventas"].astype(float)
    grupo = [panel["tienda_id"], panel["fecha"]]
    total = v.groupby(grupo).transform("sum")
    n = v.notna().groupby(grupo).transform("sum")
    cierre = (total == 0) & (n >= 2)
    return panel.assign(ventas=v.where(~cierre))


def _desplazar(m: np.ndarray, k: int) -> np.ndarray:
    """Columna t ← columna t−k (NaN al inicio)."""
    out = np.full_like(m, np.nan)
    if k < m.shape[1]:
        out[:, k:] = m[:, : m.shape[1] - k]
    return out


def _media_ventana(m: np.ndarray, w: int) -> np.ndarray:
    """Media de los días con dato en [t−w, t−1] (NaN si ninguno)."""
    valido = ~np.isnan(m)
    s = np.concatenate([np.zeros((m.shape[0], 1)), np.cumsum(np.where(valido, m, 0.0), 1)], 1)
    c = np.concatenate([np.zeros((m.shape[0], 1)), np.cumsum(valido, 1)], 1)
    t = np.arange(m.shape[1])
    fin, ini = t, np.maximum(t - w, 0)  # columnas acumuladas: [ini, fin) = días t−w … t−1
    suma = s[:, fin] - s[:, ini]
    cuenta = c[:, fin] - c[:, ini]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cuenta > 0, suma / np.maximum(cuenta, 1), np.nan)


def _ultimo_dato(m: np.ndarray, ventana: int) -> tuple[np.ndarray, np.ndarray]:
    """Último valor con dato en [t−ventana, t−1] y cuántos días han pasado desde él."""
    filas, cols = m.shape
    idx = np.where(~np.isnan(m), np.arange(cols), -1)
    idx = np.maximum.accumulate(idx, axis=1)
    previo = np.full((filas, cols), -1)
    previo[:, 1:] = idx[:, :-1]
    dias = np.arange(cols) - previo
    ok = (previo >= 0) & (dias <= ventana)
    valor = np.take_along_axis(m, np.maximum(previo, 0), axis=1)
    return np.where(ok, valor, np.nan), np.where(ok, dias, np.nan).astype(float)


def _mismo_dia(m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Último valor del mismo día de la semana y media de los `K_MISMO_DIA` más recientes."""
    pila = np.stack([_desplazar(m, 7 * k) for k in range(1, SEMANAS_MISMO_DIA + 1)])
    valido = ~np.isnan(pila)
    orden = np.cumsum(valido, axis=0)
    usar = valido & (orden <= K_MISMO_DIA)
    cuenta = usar.sum(0)
    with np.errstate(invalid="ignore", divide="ignore"):
        media = np.where(
            cuenta > 0, np.where(usar, pila, 0.0).sum(0) / np.maximum(cuenta, 1), np.nan
        )
    primero = valido & (orden == 1)
    ultimo = np.where(primero.any(0), np.where(primero, pila, 0.0).sum(0), np.nan)
    return ultimo, media


def _grilla(datos: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Grilla completa serie × día; devuelve las claves (orden serie, fecha) y las matrices."""
    series = (
        datos[["tienda_id", "familia_id"]]
        .drop_duplicates()
        .sort_values(["tienda_id", "familia_id"])
    )
    fechas = pd.date_range(datos["fecha"].min(), datos["fecha"].max(), freq="D")
    n_s, n_t = len(series), len(fechas)
    claves = pd.DataFrame(
        {
            "tienda_id": np.repeat(series["tienda_id"].to_numpy(), n_t),
            "familia_id": np.repeat(series["familia_id"].to_numpy(), n_t),
            "fecha": np.tile(fechas.to_numpy(), n_s),
        }
    )
    llena = claves.merge(datos, on=CLAVES, how="left")
    ventas = llena["ventas"].to_numpy(dtype=float).reshape(n_s, n_t)
    promo = llena["en_promocion"].to_numpy(dtype=float).reshape(n_s, n_t)
    return claves, ventas, promo


def _calcular(
    claves: pd.DataFrame, ventas: np.ndarray, promo: np.ndarray, ctx: Contexto
) -> pd.DataFrame:
    """Variables para cada celda de la grilla (orden de `claves`)."""
    col: dict[str, np.ndarray] = {}
    for k in LAGS:
        col[f"lag_{k}"] = _desplazar(ventas, k)
    col["ultimo_dato"], col["dias_desde_ultimo_dato"] = _ultimo_dato(ventas, VENTANA_DIAS)
    col["ultimo_mismo_dia"], col["media_mismo_dia_4"] = _mismo_dia(ventas)
    for w in MEDIAS_MOVILES:
        col[f"media_{w}"] = _media_ventana(ventas, w)
    with np.errstate(invalid="ignore", divide="ignore"):
        col["razon_7_28"] = np.where(
            col["media_28"] > 0,
            col["media_7"] / np.where(col["media_28"] > 0, col["media_28"], 1),
            np.nan,
        )
    col["en_promocion"] = promo
    col["promo_desconocida"] = np.isnan(promo).astype(float)
    col["promo_media_28"] = _media_ventana(promo, DIAS_PROMO_MEDIA)

    out = claves.copy()
    for nombre, m in col.items():
        out[nombre] = m.reshape(-1)

    tiendas = ctx.tiendas.set_index("tienda_id")
    ciudad = out["tienda_id"].map(tiendas["ciudad"]).fillna("")
    cal = variables_calendario(out["fecha"], ciudad, ctx.feriados)
    out = pd.concat([out, cal], axis=1)
    apertura = out["tienda_id"].map(tiendas["fecha_apertura"])
    out["dias_desde_apertura"] = (
        (out["fecha"] - apertura).dt.days.astype(float).clip(upper=TOPE_DIAS_APERTURA)
    )
    out["tienda"] = _categoria(out["tienda_id"], list(tiendas.index))
    out["familia"] = _categoria(out["familia_id"], list(ctx.familias))
    tipos = sorted(tiendas["tipo"].unique())
    out["tipo_tienda"] = _categoria(out["tienda_id"].map(tiendas["tipo"]), tipos)
    out["dow"] = _categoria(out["dow"], list(range(7)))
    return out


def _categoria(valores: pd.Series, categorias: list) -> pd.Categorical:
    """Categórica con categorías fijas (estables entre entrenar y servir); desconocida → NaN."""
    return pd.Categorical(valores.where(valores.isin(categorias)), categories=categorias)


def _normalizar(datos: pd.DataFrame) -> pd.DataFrame:
    d = datos.copy()
    d["tienda_id"] = d["tienda_id"].astype("int64")
    d["familia_id"] = d["familia_id"].astype(str)
    d["fecha"] = pd.to_datetime(d["fecha"])
    d["ventas"] = pd.to_numeric(d["ventas"], errors="coerce").astype(float)
    if "en_promocion" not in d:
        d["en_promocion"] = np.nan
    d["en_promocion"] = pd.to_numeric(d["en_promocion"], errors="coerce").astype(float)
    return d[[*CLAVES, "ventas", "en_promocion"]]


def huecos_aleatorios(
    tiendas: np.ndarray, n_dias: int, rng: np.random.Generator
) -> dict[int, np.ndarray]:
    """Máscara de días en hueco por tienda: bloques de 1–`LARGO_MAX_HUECO` días."""
    mascaras: dict[int, np.ndarray] = {}
    largo_medio = (1 + LARGO_MAX_HUECO) / 2
    n_bloques = max(1, round(FRACCION_DIAS_HUECO * n_dias / largo_medio))
    for t in tiendas:
        m = np.zeros(n_dias, dtype=bool)
        inicios = rng.integers(0, n_dias, size=n_bloques)
        largos = rng.integers(1, LARGO_MAX_HUECO + 1, size=n_bloques)
        for i, largo in zip(inicios, largos, strict=True):
            m[i : i + largo] = True
        mascaras[int(t)] = m
    return mascaras


# ------------------------------------------------------------------------------ interfaz


def variables_entrenamiento(
    historia: pd.DataFrame,
    ctx: Contexto,
    *,
    enmascarar: bool = True,
    semilla: int = 0,
) -> pd.DataFrame:
    """Filas de entrenamiento: claves, `ventas` (objetivo) y `FEATURES`.

    `historia` trae `tienda_id, fecha, familia_id, ventas` y, si se conoce, `en_promocion`
    (NaN = promoción desconocida, DECISIONES §6.3). Se excluyen los días de cierre y los días
    anteriores a la apertura de la tienda.
    """
    datos = marcar_cierres(_normalizar(historia))
    if datos.empty:
        return pd.DataFrame(columns=[*CLAVES, "ventas", *FEATURES])
    claves, ventas, promo = _grilla(datos)
    base = _calcular(claves, ventas, promo, ctx)
    if enmascarar:
        rng = np.random.default_rng(semilla)
        n_s, n_t = ventas.shape
        tienda_fila = claves["tienda_id"].to_numpy()[::n_t]
        mascaras = huecos_aleatorios(np.unique(tienda_fila), n_t, rng)
        hueco = np.stack([mascaras[int(t)] for t in tienda_fila])
        con_huecos = _calcular(claves, np.where(hueco, np.nan, ventas), promo, ctx)
        sorteo = rng.random(len(base))
        elegir = sorteo < FRACCION_FILAS_ENMASCARADAS
        base.loc[elegir, VARIABLES_NIVEL] = con_huecos.loc[elegir, VARIABLES_NIVEL].to_numpy()
        sin_historia = sorteo >= 1.0 - FRACCION_FILAS_SIN_HISTORIA
        base.loc[sin_historia, VARIABLES_NIVEL] = np.nan
    base["ventas"] = ventas.reshape(-1)
    abierta = base["dias_desde_apertura"].fillna(0) >= 0
    out = base[base["ventas"].notna() & abierta]
    return out[[*CLAVES, "ventas", *FEATURES]].reset_index(drop=True)


def variables_prediccion(
    solicitud: pd.DataFrame, panel: pd.DataFrame, ctx: Contexto
) -> pd.DataFrame:
    """Variables para cada fila de `solicitud` (mismo orden), usando solo el panel anterior.

    `solicitud`: `tienda_id, fecha, familia_id, en_promocion`. `panel`: ventas con
    `fecha < D` (y `en_promocion` si se conoce). Para cada fecha objetivo solo se usan días
    anteriores; si el panel trae la misma clave que la solicitud, su venta se ignora para esa
    fecha (y sí sirve para fechas objetivo posteriores).
    """
    sol = _normalizar(solicitud.assign(ventas=np.nan))
    if sol.empty:
        return pd.DataFrame(columns=[*CLAVES, *FEATURES])
    pan = _normalizar(panel) if len(panel) else sol.iloc[:0]
    pan = pan[pan["fecha"] < sol["fecha"].max()]
    pan = marcar_cierres(pan)
    # La promoción de la fecha objetivo viene de la solicitud; la venta, del panel si existe.
    unida = sol.drop(columns="ventas").merge(pan[[*CLAVES, "ventas"]], on=CLAVES, how="left")
    resto = pan.merge(sol[CLAVES], on=CLAVES, how="left", indicator=True)
    resto = resto[resto["_merge"] == "left_only"].drop(columns="_merge")
    datos = pd.concat([resto, unida], ignore_index=True)
    claves, ventas, promo = _grilla(datos)
    todas = _calcular(claves, ventas, promo, ctx)
    out = sol[CLAVES].merge(todas, on=CLAVES, how="left")
    return out[[*CLAVES, *FEATURES]].reset_index(drop=True)
