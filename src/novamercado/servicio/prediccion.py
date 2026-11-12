"""Camino de predicción: panel `< D` desde la BD → modelo en memoria → salida saneada.

No llama a MLflow: usa el `ModeloCargado` que recibe.
"""

from __future__ import annotations

import logging

import pandas as pd
import psycopg

from novamercado.contrato import PrediccionFamilia, SolicitudPrediccion, sanear_prediccion
from novamercado.features.panel import leer_panel
from novamercado.modelo.pyfunc import armar_entrada
from novamercado.servicio.cargador import ModeloCargado

log = logging.getLogger(__name__)


class ErrorModelo(RuntimeError):
    """El modelo en memoria no pudo predecir (la API responde 503, nunca 500)."""


def predecir(
    conn: psycopg.Connection, modelo: ModeloCargado, solicitud: SolicitudPrediccion
) -> list[PrediccionFamilia]:
    """Predicciones en el orden de `solicitud.familias`, ya llevadas al contrato.

    El panel se lee con `fecha < solicitud.fecha` en SQL (regla de no fuga).
    """
    panel = leer_panel(conn, solicitud.fecha, tienda_id=solicitud.tienda_id)
    entrada = pd.DataFrame(
        [
            (solicitud.tienda_id, pd.Timestamp(solicitud.fecha), f.familia_id, f.en_promocion)
            for f in solicitud.familias
        ],
        columns=["tienda_id", "fecha", "familia_id", "en_promocion"],
    )
    try:
        salida = modelo.modelo.predict(armar_entrada(entrada, panel))
        por_familia = {
            str(f): (v, c)
            for f, v, c in zip(
                salida["familia_id"],
                salida["venta_esperada"],
                salida["cantidad_sugerida"],
                strict=True,
            )
        }
    except Exception as exc:  # noqa: BLE001 — cualquier fallo del modelo es un 503
        log.exception("El modelo %s v%s falló al predecir", modelo.nombre, modelo.version)
        raise ErrorModelo(f"El modelo vigente no pudo predecir: {type(exc).__name__}") from exc
    return [
        sanear_prediccion(f.familia_id, *por_familia.get(f.familia_id, (0.0, 0.0)))
        for f in solicitud.familias
    ]
