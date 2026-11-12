"""Camino de predicción: panel `< D` desde la BD → modelo en memoria → salida saneada.

No llama a MLflow: usa el `ModeloCargado` que recibe.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd
import psycopg

from novamercado.features.panel import leer_panel
from novamercado.modelo.pyfunc import armar_entrada
from novamercado.servicio.cargador import ModeloCargado


@dataclass(frozen=True)
class PrediccionFamilia:
    familia_id: str
    venta_esperada: float
    cantidad_sugerida: int


def sanear(venta: object, cantidad: object) -> tuple[float, int]:
    """Garantiza venta finita ≥ 0 y cantidad entera ≥ 0 (nunca NaN ni negativos)."""
    try:
        v = float(venta)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        v = 0.0
    if not math.isfinite(v) or v < 0:
        v = 0.0
    try:
        c = float(cantidad)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        c = v
    if not math.isfinite(c) or c < 0:
        c = v
    return round(v, 2), max(0, math.ceil(c - 1e-9))


def predecir(
    conn: psycopg.Connection,
    modelo: ModeloCargado,
    tienda_id: int,
    fecha: date,
    familias: list[tuple[str, int]],
) -> list[PrediccionFamilia]:
    panel = leer_panel(conn, fecha, tienda_id=tienda_id)
    solicitud = pd.DataFrame(
        [(tienda_id, pd.Timestamp(fecha), f, p) for f, p in familias],
        columns=["tienda_id", "fecha", "familia_id", "en_promocion"],
    )
    salida = modelo.modelo.predict(armar_entrada(solicitud, panel))
    por_familia = {
        str(f): (v, c)
        for f, v, c in zip(
            salida["familia_id"],
            salida["venta_esperada"],
            salida["cantidad_sugerida"],
            strict=True,
        )
    }
    resultado = []
    for familia, _ in familias:
        v, c = sanear(*por_familia.get(familia, (0.0, 0)))
        resultado.append(PrediccionFamilia(familia, v, c))
    return resultado
