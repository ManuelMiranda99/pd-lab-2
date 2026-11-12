"""Modelo de referencia: media del mismo día de la semana en las últimas k semanas.

La cantidad sugerida es `ceil(venta_esperada × factor_familia)`, donde el factor es el cuantil
τ = c_f / (c_f + c_s) del ratio real/predicho en una ventana de calibración (newsvendor).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from novamercado.features.baseline import K_SEMANAS, media_mismo_dia

COLUMNAS_SOLICITUD = ["tienda_id", "fecha", "familia_id", "en_promocion"]
COLUMNAS_SALIDA = ["tienda_id", "fecha", "familia_id", "venta_esperada", "cantidad_sugerida"]

# Días hacia atrás que necesita el baseline para una fecha (16 semanas).
VENTANA_PANEL_DIAS = 7 * 16
FACTOR_MIN, FACTOR_MAX = 0.5, 3.0


@dataclass
class BaselineDiaSemana:
    """Baseline entrenable y serializable (se guarda dentro del pyfunc)."""

    k: int = K_SEMANAS
    factores: dict[str, float] = field(default_factory=dict)
    respaldo: dict[str, float] = field(default_factory=dict)  # "tienda|familia" → media
    respaldo_familia: dict[str, float] = field(default_factory=dict)
    nombre: str = "baseline_dia_semana"
    #: Primer día que no vio al entrenar (como `ModeloHGB.fecha_corte`): así, si el baseline es
    #: el vigente, se compara solo en días nuevos. None en modelos guardados antes de este campo.
    fecha_corte: pd.Timestamp | None = None

    def predecir(self, solicitud: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
        """Predice cada fila de `solicitud` usando solo filas de `panel` anteriores a su fecha."""
        sol = solicitud.loc[:, COLUMNAS_SOLICITUD].copy()
        sol["tienda_id"] = sol["tienda_id"].astype("int64")
        sol["familia_id"] = sol["familia_id"].astype(str)
        sol["fecha"] = pd.to_datetime(sol["fecha"])
        partes = []
        for fecha, grupo in sol.groupby("fecha", sort=True):
            tiendas = grupo["tienda_id"].unique()
            sub = panel[panel["tienda_id"].isin(tiendas)]
            feats = media_mismo_dia(sub, fecha, self.k)
            partes.append(grupo.merge(feats, on=["tienda_id", "familia_id"], how="left"))
        if not partes:
            return pd.DataFrame(columns=COLUMNAS_SALIDA)
        df = pd.concat(partes, ignore_index=True)
        clave = df["tienda_id"].astype(str) + "|" + df["familia_id"]
        venta = (
            df["media_dia"]
            .fillna(df["media_reciente"])
            .fillna(clave.map(self.respaldo))
            .fillna(df["familia_id"].map(self.respaldo_familia))
            .fillna(0.0)
            .astype(float)
        )
        venta = venta.where(np.isfinite(venta), 0.0).clip(lower=0.0)
        factor = df["familia_id"].map(self.factores).fillna(1.0).astype(float)
        df["venta_esperada"] = venta.round(2)
        df["cantidad_sugerida"] = [
            max(0, math.ceil(v * f - 1e-9)) for v, f in zip(venta, factor, strict=True)
        ]
        return df[COLUMNAS_SALIDA]


def predicciones_rodantes(
    modelo: BaselineDiaSemana, historia: pd.DataFrame, fechas: list[pd.Timestamp]
) -> pd.DataFrame:
    """Para cada fecha D, predice las filas reales de D usando solo la historia `< D`."""
    partes = []
    for d in fechas:
        reales = historia[historia["fecha"] == d]
        if reales.empty:
            continue
        panel = historia[
            (historia["fecha"] < d)
            & (historia["fecha"] >= d - pd.Timedelta(days=VENTANA_PANEL_DIAS))
        ]
        sol = reales.assign(en_promocion=0)
        pred = modelo.predecir(sol, panel)
        partes.append(
            pred.merge(
                reales[["tienda_id", "fecha", "familia_id", "ventas"]],
                on=["tienda_id", "fecha", "familia_id"],
            )
        )
    if not partes:
        return pd.DataFrame(columns=[*COLUMNAS_SALIDA, "ventas"])
    return pd.concat(partes, ignore_index=True)


def ultimas_fechas(
    historia: pd.DataFrame, dias: int, *, antes_de: pd.Timestamp | None = None
) -> list[pd.Timestamp]:
    """Las últimas `dias` fechas presentes en la historia (opcionalmente `< antes_de`)."""
    fechas = historia["fecha"].drop_duplicates().sort_values()
    if antes_de is not None:
        fechas = fechas[fechas < antes_de]
    return list(fechas.iloc[-dias:])


def ajustar_baseline(
    historia: pd.DataFrame,
    costos: dict[str, tuple[float, float]],
    *,
    k: int = K_SEMANAS,
    dias_calibracion: int = 56,
    antes_de: pd.Timestamp | None = None,
) -> BaselineDiaSemana:
    """Entrena el baseline con la historia (`fecha < antes_de` si se indica).

    - respaldo: media de los últimos 28 días por (tienda, familia) y por familia.
    - factores: cuantil τ_f del ratio real/predicho en los últimos `dias_calibracion` días.
    """
    datos = historia if antes_de is None else historia[historia["fecha"] < antes_de]
    if datos.empty:
        return BaselineDiaSemana(k=k, fecha_corte=antes_de)
    ultimo = datos["fecha"].max()
    corte = pd.Timestamp(ultimo) + pd.Timedelta(days=1)
    reciente = datos[datos["fecha"] > ultimo - pd.Timedelta(days=28)]
    por_par = reciente.groupby(["tienda_id", "familia_id"])["ventas"].mean()
    respaldo = {f"{t}|{f}": float(v) for (t, f), v in por_par.items()}
    respaldo_familia = {
        f: float(v) for f, v in reciente.groupby("familia_id")["ventas"].mean().items()
    }

    sin_factor = BaselineDiaSemana(k=k, respaldo=respaldo, respaldo_familia=respaldo_familia)
    calib = predicciones_rodantes(sin_factor, datos, ultimas_fechas(datos, dias_calibracion))
    factores: dict[str, float] = {}
    if not calib.empty:
        calib = calib[calib["venta_esperada"] > 0]
        for familia, grupo in calib.groupby("familia_id"):
            cf, cs = costos.get(familia, (1.0, 1.0))
            tau = cf / (cf + cs)
            ratio = grupo["ventas"] / grupo["venta_esperada"]
            factores[familia] = float(np.clip(ratio.quantile(tau), FACTOR_MIN, FACTOR_MAX))
    return BaselineDiaSemana(
        k=k,
        factores=factores,
        respaldo=respaldo,
        respaldo_familia=respaldo_familia,
        fecha_corte=corte,
    )
