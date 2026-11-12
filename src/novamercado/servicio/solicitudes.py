"""Atención de `POST /v1/prediccion` y `POST /v1/observaciones`, sin FastAPI.

Orden de cada solicitud:

1. Lectura tolerante del cuerpo crudo (JSON inválido, vacío o sin objeto raíz → 422, aunque la
   BD esté caída: no hace falta el catálogo para saberlo).
2. Catálogo (de la BD, con caché) → limpieza con la tabla de políticas → 422 si es irreparable.
   Todo rechazo se registra en `solicitudes` con su `detail` (si la BD responde).
3. Predicción con el modelo en memoria y panel `< D` filtrado en SQL; o upsert de observaciones.
4. Se guarda todo en **una** transacción antes de responder.

Los errores salen como :class:`ErrorAtencion` con el código HTTP del contrato (422 o 503).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import psycopg

from novamercado.contrato import ObservacionesResponse, PrediccionResponse
from novamercado.db import repos
from novamercado.limpieza import (
    Catalogo,
    Hallazgo,
    SolicitudRechazada,
    leer_cuerpo,
    limpiar_observaciones,
    limpiar_prediccion,
)
from novamercado.servicio.cargador import ModeloCargado
from novamercado.servicio.catalogo import CatalogoNoDisponible
from novamercado.servicio.prediccion import ErrorModelo, predecir

log = logging.getLogger(__name__)

BD_CAIDA = "Base de datos no disponible"
SIN_MODELO = "Sin modelo cargado todavía"


class ErrorAtencion(Exception):  # noqa: N818 — se traduce a una respuesta HTTP
    """Respuesta de error del contrato: 422 (solicitud irreparable) o 503 (dependencia)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


Conectar = Callable[[], psycopg.Connection]


def a_correcciones(hallazgos: tuple[Hallazgo, ...]) -> list[repos.Correccion]:
    """Hallazgos de la limpieza → filas de `correcciones` (incluye los ignorados)."""
    return [repos.Correccion(h.codigo, h.campo, h.accion.value, h.detalle) for h in hallazgos]


def _registrar_rechazo(
    conectar: Conectar, tipo: str, cuerpo: bytes, exc: SolicitudRechazada
) -> None:
    """Guarda el rechazo si la BD responde; si no, solo lo deja en el log (sigue siendo 422)."""
    try:
        with conectar() as conn:
            repos.registrar_rechazo(
                conn, tipo=tipo, cuerpo=cuerpo, codigo=exc.codigo, detail=exc.detail
            )
    except psycopg.Error:
        log.warning("No se pudo registrar el rechazo (%s): %s", tipo, exc.detail)


def _limpiar(
    tipo: str,
    cuerpo: bytes,
    catalogo: Callable[[], Catalogo],
    conectar: Conectar,
    limpiar: Callable[[Any, Catalogo, tuple[Hallazgo, ...]], Any],
) -> Any:
    """Lectura + catálogo + limpieza; los rechazos se registran y salen como 422."""
    try:
        leido = leer_cuerpo(cuerpo)
    except SolicitudRechazada as exc:
        _registrar_rechazo(conectar, tipo, cuerpo, exc)
        raise ErrorAtencion(422, exc.detail) from exc
    try:
        cat = catalogo()
    except CatalogoNoDisponible as exc:
        raise ErrorAtencion(503, BD_CAIDA) from exc
    try:
        return limpiar(leido.datos, cat, leido.hallazgos)
    except SolicitudRechazada as exc:
        _registrar_rechazo(conectar, tipo, cuerpo, exc)
        raise ErrorAtencion(422, exc.detail) from exc


def atender_prediccion(
    cuerpo: bytes,
    *,
    catalogo: Callable[[], Catalogo],
    conectar: Conectar,
    modelo: ModeloCargado | None,
) -> PrediccionResponse:
    """Limpia, predice con `modelo` (una sola referencia para toda la solicitud) y registra."""
    limpia = _limpiar("prediccion", cuerpo, catalogo, conectar, limpiar_prediccion)
    if modelo is None:
        raise ErrorAtencion(503, SIN_MODELO)
    solicitud = limpia.solicitud
    completadas = set(limpia.familias_completadas)
    try:
        with conectar() as conn:
            predicciones = predecir(conn, modelo, solicitud)
            repos.registrar_prediccion(
                conn,
                cuerpo=cuerpo,
                normalizado=solicitud.model_dump(mode="json"),
                tienda_id=solicitud.tienda_id,
                fecha=solicitud.fecha,
                calidad_estado=limpia.calidad.estado,
                claves_ignoradas=limpia.claves_ignoradas,
                version_modelo=modelo.version,
                correcciones=a_correcciones(limpia.hallazgos),
                filas=[
                    repos.FilaPrediccion(
                        familia_id=p.familia_id,
                        en_promocion=f.en_promocion,
                        promocion_explicita=f.familia_id not in completadas,
                        venta_esperada=p.venta_esperada,
                        cantidad_sugerida=p.cantidad_sugerida,
                    )
                    for f, p in zip(solicitud.familias, predicciones, strict=True)
                ],
            )
    except psycopg.Error as exc:
        log.warning("BD no disponible al predecir: %s", exc)
        raise ErrorAtencion(503, BD_CAIDA) from exc
    except ErrorModelo as exc:
        raise ErrorAtencion(503, str(exc)) from exc
    return PrediccionResponse(
        tienda_id=solicitud.tienda_id,
        fecha=solicitud.fecha,
        version_modelo=modelo.version,
        predicciones=predicciones,
        calidad=limpia.calidad,
    )


def atender_observaciones(
    cuerpo: bytes, *, catalogo: Callable[[], Catalogo], conectar: Conectar
) -> ObservacionesResponse:
    """Limpia y guarda las ventas reales con upsert idempotente."""
    limpia = _limpiar("observaciones", cuerpo, catalogo, conectar, limpiar_observaciones)
    solicitud = limpia.solicitud
    try:
        with conectar() as conn:
            guardados = repos.registrar_observaciones(
                conn,
                cuerpo=cuerpo,
                normalizado=solicitud.model_dump(mode="json"),
                tienda_id=solicitud.tienda_id,
                fecha=solicitud.fecha,
                calidad_estado=limpia.calidad.estado,
                claves_ignoradas=limpia.claves_ignoradas,
                correcciones=a_correcciones(limpia.hallazgos),
                ventas=[(v.familia_id, v.ventas) for v in solicitud.ventas],
            )
    except psycopg.Error as exc:
        log.warning("BD no disponible al guardar observaciones: %s", exc)
        raise ErrorAtencion(503, BD_CAIDA) from exc
    return ObservacionesResponse(registros_guardados=guardados, calidad=limpia.calidad)
