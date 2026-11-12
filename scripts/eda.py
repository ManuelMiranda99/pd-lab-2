"""EDA reproducible del histórico de NovaMercado.

Confirma con números los "Hechos verificados de los datos" del PLAN, imprime un resumen y guarda
gráficas **solo de agregados** en `evidencia/eda/` (más `resumen_eda.json`).

Uso:
    DATOS_DIR=/ruta/a/data python scripts/eda.py [--salida evidencia/eda] [--sin-graficas]

No usa el reloj del sistema: todas las fechas de referencia salen de los datos.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from comun_datos import (
    Datos,
    cargar,
    dir_datos,
    es_cierre_tienda,
    factor_vs_semanas_vecinas,
    semana_santa,
    tau_por_familia,
)

RAIZ = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- cálculos (puros, sin gráficas)
def estructura(d: Datos) -> dict:
    h = d.historico
    ini, fin = h["fecha"].min(), h["fecha"].max()
    dias = (fin - ini).days + 1
    dup = int(h.duplicated(["fecha", "tienda_id", "familia_id"]).sum())
    huecos = 0
    filas_tienda = {}
    for t in d.tiendas.itertuples():
        sub = h[h["tienda_id"] == t.tienda_id]
        desde = max(ini, t.fecha_apertura)
        esperadas = ((fin - desde).days + 1) * len(d.familias)
        filas_tienda[int(t.tienda_id)] = int(len(sub))
        huecos += esperadas - sub[["fecha", "familia_id"]].drop_duplicates().shape[0]
    fam_por_tienda = h.groupby("tienda_id")["familia_id"].nunique()
    return {
        "inicio": ini.date().isoformat(),
        "fin": fin.date().isoformat(),
        "dias": dias,
        "filas": len(h),
        "duplicados": dup,
        "huecos": int(huecos),
        "familias_en_todas_las_tiendas": bool((fam_por_tienda == len(d.familias)).all()),
        "en_promocion_min": int(h["en_promocion"].min()),
        "en_promocion_max": int(h["en_promocion"].max()),
        "ventas_negativas": int((h["ventas"] < 0).sum()),
        "filas_por_tienda": filas_tienda,
    }


def total_diario(h: pd.DataFrame, tiendas: list[int] | None = None) -> pd.Series:
    sub = h if tiendas is None else h[h["tienda_id"].isin(tiendas)]
    return sub.groupby("fecha")["ventas"].sum().asfreq("D")


def cierres(h: pd.DataFrame) -> dict:
    c = h.loc[es_cierre_tienda(h), ["tienda_id", "fecha"]].drop_duplicates()
    out = {}
    for t, g in c.groupby("tienda_id"):
        out[int(t)] = {
            "desde": g["fecha"].min().date().isoformat(),
            "hasta": g["fecha"].max().date().isoformat(),
            "dias": int(len(g)),
        }
    return out


def tienda_10(d: Datos) -> dict:
    h = d.historico
    t10 = d.tiendas.set_index("tienda_id").loc[10]
    s = total_diario(h, [10]).dropna()
    otras_express = d.tiendas.loc[
        (d.tiendas["tipo"] == "express") & (d.tiendas["tienda_id"] != 10), "tienda_id"
    ].tolist()
    exp = h[h["tienda_id"].isin(otras_express)].groupby(["fecha", "tienda_id"])["ventas"].sum()
    exp = exp.groupby("fecha").mean()
    jul = s[(s.index.month == 7) & (s.index.year == 2025)].mean()
    ultimos = s.iloc[-28:].mean()
    return {
        "apertura": t10.fecha_apertura.date().isoformat(),
        "primer_dato": s.index.min().date().isoformat(),
        "dias": int(len(s)),
        "media_primera_semana": round(float(s.iloc[:7].mean()), 1),
        "media_julio_2025": round(float(jul), 1),
        "media_ultimas_4_semanas": round(float(ultimos), 1),
        "media_otras_express_ultimas_4_semanas": round(float(exp.iloc[-28:].mean()), 1),
    }


def baseline_wape_por_tienda(h: pd.DataFrame) -> dict:
    """WAPE del baseline (media mismo día de semana, 4 semanas previas) en el último año."""
    p = h.pivot_table(index="fecha", columns=["tienda_id", "familia_id"], values="ventas")
    p = p.mask(p.T.groupby(level=0).transform("sum").T == 0)  # cierres → NaN
    pred = sum(p.shift(7 * i) for i in range(1, 5)) / 4
    ini = h["fecha"].max() - pd.Timedelta(days=364)
    out = {}
    for t in sorted(h["tienda_id"].unique()):
        r, f = p.loc[ini:, t].to_numpy(), pred.loc[ini:, t].to_numpy()
        m = ~np.isnan(r) & ~np.isnan(f)
        out[int(t)] = round(float(np.abs(r[m] - f[m]).sum() / r[m].sum()), 3)
    return out


def ceros(d: Datos) -> dict:
    h = d.historico[~es_cierre_tienda(d.historico)]
    tasa = h.assign(cero=h["ventas"] == 0).pivot_table(
        index="tienda_id", columns="familia_id", values="cero", aggfunc="mean"
    )
    pares = tasa.stack()
    top = pares[pares > 0].sort_values(ascending=False).head(10)
    return {
        "filas_cero_sin_cierres": int((h["ventas"] == 0).sum()),
        "top_tasa_ceros": {f"{t}/{f}": round(float(v), 3) for (t, f), v in top.items()},
        "_tabla": tasa,
    }


def factores_especiales(d: Datos) -> dict:
    """Factor (día / mediana del mismo día de semana ±4 semanas) en tiendas maduras 1–9."""
    h = d.historico
    maduras = [t for t in d.tiendas["tienda_id"] if t != 10]
    s = total_diario(h[~es_cierre_tienda(h)], maduras)
    fac = factor_vs_semanas_vecinas(s)
    fechas = s.index
    md = fechas.month * 100 + fechas.day

    def media(mask) -> float:
        return round(float(fac[mask].mean()), 3)

    ss = sorted(semana_santa(d.feriados))
    out = {
        "1_ene": media(md == 101),
        "24_dic": media(md == 1224),
        "25_dic": media(md == 1225),
        "31_dic": media(md == 1231),
        "26_30_dic": media((md >= 1226) & (md <= 1230)),
        "31_oct": media(md == 1031),
        "semana_santa_jue_vie_sab": [round(float(fac[x]), 3) for x in ss if x in fac.index],
    }
    # Por familia en 24 y 31 de diciembre.
    fam = (
        h[h["tienda_id"].isin(maduras) & ~es_cierre_tienda(h)]
        .groupby(["fecha", "familia_id"])["ventas"]
        .sum()
        .unstack()
        .asfreq("D")
    )
    fam_fac = fam.apply(factor_vs_semanas_vecinas)
    for clave, m in (("24_dic", 1224), ("31_dic", 1231)):
        v = fam_fac[md == m].mean().sort_values(ascending=False)
        out[f"{clave}_por_familia"] = {k: round(float(x), 2) for k, x in v.items()}
    # Otros feriados/fechas nacionales del CSV dentro del histórico.
    nac = d.feriados[(d.feriados["ambito"] == "nacional") & d.feriados["fecha"].isin(fechas)]
    por_nombre = (
        nac.assign(factor=nac["fecha"].map(fac)).groupby(["nombre", "tipo"])["factor"].mean()
    )
    out["nacionales"] = {f"{n} ({t})": round(float(v), 3) for (n, t), v in por_nombre.items()}
    out["_serie_factor"] = fac
    return out


def dia_de_mes(d: Datos, fac: pd.Series) -> dict:
    """Efecto del día del mes (días de pago) excluyendo fechas del CSV de feriados y 24–31 dic."""
    especiales = set(d.feriados["fecha"])
    idx = fac.index
    md = idx.month * 100 + idx.day
    limpio = fac[~idx.isin(especiales) & ~((md >= 1224) | (md == 101))].dropna()
    por_dia = limpio.groupby(limpio.index.day).mean()
    dias_fin = limpio.index.days_in_month - limpio.index.day
    return {
        "dia_15": round(float(por_dia.get(15, np.nan)), 3),
        "dia_16": round(float(por_dia.get(16, np.nan)), 3),
        "ultimo_dia": round(float(limpio[dias_fin == 0].mean()), 3),
        "penultimo_dia": round(float(limpio[dias_fin == 1].mean()), 3),
        "dia_1": round(float(por_dia.get(1, np.nan)), 3),
        "resto_mediana": round(float(por_dia.drop([1, 15, 16, 28, 29, 30, 31]).median()), 3),
        "_por_dia": por_dia,
    }


def efecto_mes_y_tendencia(d: Datos) -> dict:
    """log(total) ~ tendencia lineal + día de semana; residuo medio por mes. Tiendas 1–9."""
    h = d.historico
    maduras = [t for t in d.tiendas["tienda_id"] if t != 10]
    s = total_diario(h[~es_cierre_tienda(h)], maduras)
    especiales = set(d.feriados["fecha"])
    df = pd.DataFrame({"y": np.log(s)})
    df["t"] = (df.index - df.index[0]).days / 365.25
    dow = pd.get_dummies(df.index.day_of_week, prefix="d", drop_first=True).astype(float)
    dow.index = df.index
    x = pd.concat([pd.Series(1.0, index=df.index, name="c"), df["t"], dow], axis=1)
    ok = ~df.index.isin(especiales)
    beta, *_ = np.linalg.lstsq(x[ok].to_numpy(), df.loc[ok, "y"].to_numpy(), rcond=None)
    resid = df["y"] - x.to_numpy() @ beta
    por_mes = np.exp(resid[ok].groupby(df.index[ok].month).mean())
    # Interanual: mar-2025..feb-2026 vs mar-2024..feb-2025 (tiendas 1–9, sin la tienda 7).
    sin7 = [t for t in maduras if t != 7]
    s2 = total_diario(h, sin7)
    corte = s2.index.min() + pd.DateOffset(years=1)
    a1, a2 = s2[s2.index < corte].sum(), s2[s2.index >= corte].sum()
    por_tipo = {}
    for tipo, g in d.tiendas[d.tiendas["tienda_id"].isin(sin7)].groupby("tipo"):
        st = total_diario(h, g["tienda_id"].tolist())
        por_tipo[tipo] = round(float(st[st.index >= corte].sum() / st[st.index < corte].sum()), 3)
    return {
        "tendencia_anual_log": round(float(beta[1]), 4),
        "crecimiento_interanual": round(float(a2 / a1), 3),
        "crecimiento_interanual_por_tipo": por_tipo,
        "factor_mes": {int(k): round(float(v), 3) for k, v in por_mes.items()},
    }


def feriados_locales(d: Datos) -> dict:
    """Factor de las tiendas de la ciudad del feriado vs el resto ese mismo día."""
    h = d.historico[~es_cierre_tienda(d.historico)]
    loc = d.feriados[(d.feriados["ambito"] != "nacional") & d.feriados["fecha"].isin(h["fecha"])]
    por_tienda = h.groupby(["tienda_id", "fecha"])["ventas"].sum().unstack(0).asfreq("D")
    fac = por_tienda.apply(factor_vs_semanas_vecinas)
    ciudad = d.tiendas.set_index("tienda_id")["ciudad"]
    out = {}
    for (nombre, ambito), g in loc.groupby(["nombre", "ambito"]):
        tiendas_c = [t for t in fac.columns if ciudad[t] == ambito]
        otras = [t for t in fac.columns if ciudad[t] != ambito]
        fc = fac.loc[g["fecha"], tiendas_c].mean().mean()
        fo = fac.loc[g["fecha"], otras].mean().mean()
        out[f"{nombre} ({ambito}, tiendas {tiendas_c})"] = {
            "factor_ciudad": round(float(fc), 3),
            "factor_resto": round(float(fo), 3),
        }
    return out


def efecto_promocion(d: Datos) -> dict:
    """Pendiente de log1p(ventas) en en_promocion dentro de (tienda, familia, año-mes, dow)."""
    h = d.historico[~es_cierre_tienda(d.historico)].copy()
    h["y"] = np.log1p(h["ventas"])
    h["g"] = (
        h["tienda_id"].astype(str)
        + h["familia_id"]
        + h["fecha"].dt.to_period("M").astype(str)
        + h["fecha"].dt.day_of_week.astype(str)
    )
    for c in ("y", "en_promocion"):
        h[c + "_c"] = h[c] - h.groupby("g")[c].transform("mean")
    pend = {}
    for fam, g in h.groupby("familia_id"):
        xx = (g["en_promocion_c"] ** 2).sum()
        pend[fam] = round(float((g["en_promocion_c"] * g["y_c"]).sum() / xx), 4)
    # Monotonía: residuo medio por tramo de promoción (todas las familias).
    tramos = pd.cut(
        h["en_promocion"], [-1, 0, 2, 5, 9, 19], labels=["0", "1-2", "3-5", "6-9", "10+"]
    )
    mono = h.groupby(tramos, observed=True)["y_c"].mean()
    dist = h["en_promocion"].describe()
    return {
        "pendiente_log_por_producto": dict(sorted(pend.items(), key=lambda kv: kv[1])),
        "residuo_log_por_tramo": {str(k): round(float(v), 4) for k, v in mono.items()},
        "filas_con_promocion": round(float((h["en_promocion"] > 0).mean()), 3),
        "en_promocion_p50_p90": [float(dist["50%"]), float(h["en_promocion"].quantile(0.9))],
    }


def feriados_cobertura(d: Datos) -> dict:
    f = d.feriados
    hist_fin = d.historico["fecha"].max()
    return {
        "inicio": f["fecha"].min().date().isoformat(),
        "fin": f["fecha"].max().date().isoformat(),
        "filas": len(f),
        "filas_posteriores_al_historico": int((f["fecha"] > hist_fin).sum()),
        "tipos": f["tipo"].value_counts().to_dict(),
        "ambitos_locales": sorted(f.loc[f["ambito"] != "nacional", "ambito"].unique().tolist()),
        "ciudades_tiendas": sorted(d.tiendas["ciudad"].unique().tolist()),
    }


def analizar(d: Datos) -> dict:
    esp = factores_especiales(d)
    dm = dia_de_mes(d, esp["_serie_factor"])
    cz = ceros(d)
    res = {
        "estructura": estructura(d),
        "cierres": cierres(d.historico),
        "tienda_10": tienda_10(d),
        "baseline_wape_por_tienda_ultimo_anio": baseline_wape_por_tienda(d.historico),
        "ceros": {k: v for k, v in cz.items() if not k.startswith("_")},
        "feriados": feriados_cobertura(d),
        "dias_especiales": {k: v for k, v in esp.items() if not k.startswith("_")},
        "dia_de_mes": {k: v for k, v in dm.items() if not k.startswith("_")},
        "mes_y_tendencia": efecto_mes_y_tendencia(d),
        "feriados_locales": feriados_locales(d),
        "promocion": efecto_promocion(d),
        "tau_newsvendor": {k: round(float(v), 3) for k, v in tau_por_familia(d.costos).items()},
    }
    res["_privado"] = {"ceros": cz["_tabla"], "factor": esp["_serie_factor"], "dia": dm["_por_dia"]}
    return res


# ---------------------------------------------------------------------------------- gráficas
def graficar(d: Datos, res: dict, salida: Path) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    salida.mkdir(parents=True, exist_ok=True)
    rutas: list[Path] = []

    def guardar(fig, nombre: str) -> None:
        ruta = salida / nombre
        fig.tight_layout()
        fig.savefig(ruta, dpi=110)
        plt.close(fig)
        rutas.append(ruta)

    h = d.historico
    # 1. Ventas totales de la cadena (media móvil semanal y de 28 días).
    s = total_diario(h)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(s.rolling(7).mean(), lw=1, label="media móvil 7 días")
    ax.plot(s.rolling(28).mean(), lw=2, label="media móvil 28 días")
    ax.set_title("Ventas diarias totales de la cadena (unidades)")
    ax.legend()
    ax.grid(alpha=0.3)
    guardar(fig, "01_ventas_cadena.png")

    # 2. Rampa de la tienda 10 vs otras express (media semanal del total diario).
    t10 = total_diario(h, [10]).dropna().resample("W").mean()
    otras = (
        h[h["tienda_id"].isin([4, 9]) & (h["fecha"] >= t10.index.min() - pd.Timedelta(days=6))]
        .groupby(["fecha", "tienda_id"])["ventas"]
        .sum()
        .groupby("fecha")
        .mean()
        .resample("W")
        .mean()
    )
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(t10, label="tienda 10 (abre 2025-03-15)")
    ax.plot(otras, label="media tiendas express 4 y 9", ls="--")
    ax.set_title("Rampa de apertura: total diario promedio por semana")
    ax.legend()
    ax.grid(alpha=0.3)
    guardar(fig, "02_rampa_tienda10.png")

    # 3. Factores de días especiales.
    esp = res["dias_especiales"]
    etiquetas = ["1_ene", "24_dic", "25_dic", "26_30_dic", "31_dic", "31_oct"]
    valores = [esp[e] for e in etiquetas]
    for nombre, v in esp["nacionales"].items():
        if "Navidad" in nombre or "Año Nuevo" in nombre or "medio día" in nombre:
            continue
        etiquetas.append(nombre.split(" (")[0])
        valores.append(v)
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.barh(etiquetas, valores)
    ax.axvline(1, color="k", lw=1)
    ax.set_title("Factor vs mismo día de semana (±4 semanas), tiendas 1–9")
    guardar(fig, "03_dias_especiales.png")

    # 4. Factor de 24 y 31 de diciembre por familia.
    f24 = pd.Series(esp["24_dic_por_familia"])
    f31 = pd.Series(esp["31_dic_por_familia"]).reindex(f24.index)
    fig, ax = plt.subplots(figsize=(11, 4))
    x = np.arange(len(f24))
    ax.bar(x - 0.2, f24.values, 0.4, label="24-dic")
    ax.bar(x + 0.2, f31.values, 0.4, label="31-dic")
    ax.set_xticks(x, f24.index, rotation=45, ha="right")
    ax.axhline(1, color="k", lw=1)
    ax.set_title("Medio día de diciembre: factor por familia")
    ax.legend()
    guardar(fig, "04_medio_dia_por_familia.png")

    # 5. Día del mes (días de pago).
    pdia = res["_privado"]["dia"]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.bar(pdia.index, pdia.values)
    ax.axhline(1, color="k", lw=1)
    ax.set_ylim(0.9, max(1.2, pdia.max() + 0.02))
    ax.set_title("Factor medio por día del mes (sin feriados ni 24-dic..1-ene)")
    ax.set_xlabel("día del mes")
    guardar(fig, "05_dia_del_mes.png")

    # 6. Efecto mes.
    fm = pd.Series(res["mes_y_tendencia"]["factor_mes"])
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(fm.index, fm.values)
    ax.axhline(1, color="k", lw=1)
    ax.set_xticks(range(1, 13))
    ax.set_title("Efecto del mes (log-ventas sin tendencia ni día de semana)")
    guardar(fig, "06_efecto_mes.png")

    # 7. Promoción.
    pend = pd.Series(res["promocion"]["pendiente_log_por_producto"])
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(pend.index, pend.values * 100)
    ax.set_title("Efecto de cada producto en promoción (% en log-ventas)")
    guardar(fig, "07_efecto_promocion.png")

    # 8. Tasa de ceros por tienda y familia.
    tz = res["_privado"]["ceros"]
    fig, ax = plt.subplots(figsize=(11, 4.5))
    im = ax.imshow(tz.values, aspect="auto", cmap="Blues")
    ax.set_xticks(range(tz.shape[1]), tz.columns, rotation=45, ha="right")
    ax.set_yticks(range(tz.shape[0]), [f"tienda {t}" for t in tz.index])
    fig.colorbar(im, ax=ax, label="proporción de días con 0")
    ax.set_title("Proporción de días con venta 0 (sin cierres)")
    guardar(fig, "08_ceros.png")

    # 9. Cuantil newsvendor por familia.
    tau = pd.Series(res["tau_newsvendor"]).sort_values()
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.barh(tau.index, tau.values)
    ax.axvline(0.5, color="k", lw=1)
    ax.set_title("Cuantil óptimo τ = c_f / (c_f + c_s)")
    guardar(fig, "09_tau_newsvendor.png")
    return rutas


# ------------------------------------------------------------------------------------ salida
def imprimir(res: dict) -> None:
    e = res["estructura"]
    print("== Estructura")
    print(
        f"  {e['inicio']} → {e['fin']}: {e['dias']} días, {e['filas']} filas, "
        f"duplicados={e['duplicados']}, huecos={e['huecos']}, "
        f"12 familias en todas={e['familias_en_todas_las_tiendas']}, "
        f"en_promocion {e['en_promocion_min']}–{e['en_promocion_max']}, "
        f"negativas={e['ventas_negativas']}"
    )
    print("== Cierres (todas las familias en 0)", res["cierres"])
    print("== Tienda 10", res["tienda_10"])
    print("== WAPE baseline por tienda (último año)", res["baseline_wape_por_tienda_ultimo_anio"])
    print("== Ceros", res["ceros"])
    print("== Feriados", res["feriados"])
    print("== Días especiales")
    for k, v in res["dias_especiales"].items():
        print(f"  {k}: {v}")
    print("== Día del mes", res["dia_de_mes"])
    print("== Mes y tendencia", res["mes_y_tendencia"])
    print("== Feriados locales")
    for k, v in res["feriados_locales"].items():
        print(f"  {k}: {v}")
    print("== Promoción", res["promocion"])
    print("== τ newsvendor", res["tau_newsvendor"])


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--datos", default=None, help="carpeta de datos (por defecto DATOS_DIR o ./data)"
    )
    ap.add_argument("--salida", default=str(RAIZ / "evidencia" / "eda"))
    ap.add_argument("--sin-graficas", action="store_true")
    args = ap.parse_args(argv)
    d = cargar(dir_datos(args.datos))
    res = analizar(d)
    imprimir(res)
    salida = Path(args.salida)
    if not args.sin_graficas:
        for r in graficar(d, res, salida):
            print("  gráfica:", r)
    publico = {k: v for k, v in res.items() if not k.startswith("_")}
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "resumen_eda.json").write_text(json.dumps(publico, indent=2, ensure_ascii=False))
    return publico


if __name__ == "__main__":
    main()
