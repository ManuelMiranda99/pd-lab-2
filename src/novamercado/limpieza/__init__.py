"""Normalizadores y tabla declarativa de políticas de datos (reparar / advertir / rechazar).

Uso típico desde la API (el router solo llama)::

    try:
        limpia = procesar_prediccion(await request.body(), catalogo)
    except SolicitudRechazada as exc:
        return JSONResponse(status_code=422, content={"detail": exc.detail})
"""

from novamercado.limpieza.calidad import Hallazgo, SolicitudRechazada, construir_calidad
from novamercado.limpieza.catalogo import Catalogo, Tienda, cargar_catalogo
from novamercado.limpieza.lectura import LIMITE_BYTES, CuerpoLeido, leer_cuerpo
from novamercado.limpieza.politicas import POLITICAS, Accion, Politica, tabla_markdown
from novamercado.limpieza.solicitudes import (
    ObservacionesLimpias,
    PrediccionLimpia,
    limpiar_observaciones,
    limpiar_prediccion,
    procesar_observaciones,
    procesar_prediccion,
)

__all__ = [
    "LIMITE_BYTES",
    "POLITICAS",
    "Accion",
    "Catalogo",
    "CuerpoLeido",
    "Hallazgo",
    "ObservacionesLimpias",
    "Politica",
    "PrediccionLimpia",
    "SolicitudRechazada",
    "Tienda",
    "cargar_catalogo",
    "construir_calidad",
    "leer_cuerpo",
    "limpiar_observaciones",
    "limpiar_prediccion",
    "procesar_observaciones",
    "procesar_prediccion",
    "tabla_markdown",
]
