"""Utilidades compartidas por `eda.py` y `backtest_cantidad.py` (solo análisis, no el servicio).

Lee los CSV publicados desde `DATOS_DIR` (por defecto `./data`). Nada de aquí usa el reloj del
sistema: todas las fechas salen de los datos.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ARCHIVOS = ("historico_ventas", "tiendas", "familias", "feriados", "costos")


@dataclass(frozen=True)
class Datos:
    historico: pd.DataFrame
    tiendas: pd.DataFrame
    familias: pd.DataFrame
    feriados: pd.DataFrame
    costos: pd.DataFrame


def dir_datos(arg: str | None = None) -> Path:
    """Carpeta de datos: argumento explícito > `DATOS_DIR` > `./data`."""
    return Path(arg or os.environ.get("DATOS_DIR", "data")).expanduser()


def cargar(carpeta: Path) -> Datos:
    """Carga los cinco CSV; falla con mensaje claro si falta alguno."""
    faltan = [a for a in ARCHIVOS if not (carpeta / f"{a}.csv").is_file()]
    if faltan:
        raise FileNotFoundError(f"Faltan archivos en {carpeta}: {', '.join(faltan)}")
    leer = {a: pd.read_csv(carpeta / f"{a}.csv", encoding="utf-8-sig") for a in ARCHIVOS}
    h = leer["historico_ventas"]
    h["fecha"] = pd.to_datetime(h["fecha"])
    t = leer["tiendas"]
    t["fecha_apertura"] = pd.to_datetime(t["fecha_apertura"])
    f = leer["feriados"]
    f["fecha"] = pd.to_datetime(f["fecha"])
    return Datos(h, t, leer["familias"], f, leer["costos"])


def es_cierre_tienda(h: pd.DataFrame) -> pd.Series:
    """Marca días en que una tienda vendió 0 en todas sus familias (cierre)."""
    tot = h.groupby(["tienda_id", "fecha"])["ventas"].transform("sum")
    return tot == 0


def semana_santa(fer: pd.DataFrame) -> set[pd.Timestamp]:
    nombres = fer["nombre"].str.contains("Santo", case=False) & fer["nombre"].str.contains(
        "Jueves|Viernes|Sábado", case=False
    )
    return set(fer.loc[nombres, "fecha"])


def calendario(fechas: pd.Series, ciudades: pd.Series, fer: pd.DataFrame) -> pd.DataFrame:
    """Variables de calendario para pares (fecha, ciudad). Una sola definición para EDA y backtest.

    El cruce de feriados locales es por ciudad **exacta** (`ambito` == `ciudad`).
    """
    fechas = pd.to_datetime(fechas).reset_index(drop=True)
    ciudades = ciudades.reset_index(drop=True)
    out = pd.DataFrame(
        {
            "dow": fechas.dt.day_of_week,
            "dia_mes": fechas.dt.day,
            "mes": fechas.dt.month,
            "dias_a_fin_mes": fechas.dt.days_in_month - fechas.dt.day,
        }
    )
    out["dia_15"] = (out["dia_mes"] == 15).astype(int)
    out["dia_16"] = (out["dia_mes"] == 16).astype(int)
    out["fin_mes"] = (out["dias_a_fin_mes"] <= 1).astype(int)  # último y penúltimo día
    out["inicio_mes"] = (out["dia_mes"] == 1).astype(int)
    nac = fer[fer["ambito"] == "nacional"]
    loc = fer[fer["ambito"] != "nacional"]
    for tipo in ("feriado", "comercial", "pago", "medio_dia"):
        dias = set(nac.loc[nac["tipo"] == tipo, "fecha"])
        out[f"nac_{tipo}"] = fechas.isin(dias).astype(int)
    pares_loc = set(zip(loc["fecha"], loc["ambito"], strict=True))
    out["feriado_local"] = [int((f, c) in pares_loc) for f, c in zip(fechas, ciudades, strict=True)]
    md = fechas.dt.month * 100 + fechas.dt.day
    out["ev_24dic"] = (md == 1224).astype(int)
    out["ev_25dic"] = (md == 1225).astype(int)
    out["ev_31dic"] = (md == 1231).astype(int)
    out["ev_1ene"] = (md == 101).astype(int)
    out["ev_26_30dic"] = md.between(1226, 1230).astype(int)
    out["ev_31oct"] = (md == 1031).astype(int)
    out["ev_semana_santa"] = fechas.isin(semana_santa(fer)).astype(int)
    return out


def factor_vs_semanas_vecinas(serie: pd.Series, k: int = 4) -> pd.Series:
    """Factor de un día = valor / media del mismo día de semana en ±1..k semanas.

    `serie` indexada por fecha diaria y completa. Sirve para medir efectos de días especiales.
    """
    vecinos = [serie.shift(7 * i) for i in range(1, k + 1)] + [
        serie.shift(-7 * i) for i in range(1, k + 1)
    ]
    base = pd.concat(vecinos, axis=1).median(axis=1)
    return serie / base


def costo(real: np.ndarray, sugerido: np.ndarray, cf: np.ndarray, cs: np.ndarray) -> np.ndarray:
    """Costo del enunciado: c_f·max(real−sug, 0) + c_s·max(sug−real, 0)."""
    return cf * np.maximum(real - sugerido, 0) + cs * np.maximum(sugerido - real, 0)


def tau_por_familia(costos: pd.DataFrame) -> pd.Series:
    """Cuantil óptimo newsvendor τ = c_f / (c_f + c_s) por familia."""
    c = costos.set_index("familia_id")
    return c["costo_faltante"] / (c["costo_faltante"] + c["costo_sobrante"])
