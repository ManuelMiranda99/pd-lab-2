"""`POST /v1/prediccion` y `POST /v1/observaciones`: routers delgados.

El cuerpo se lee crudo (cualquier `Content-Type`) y toda la limpieza, la predicción y la
persistencia viven en `novamercado.servicio.solicitudes`.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.dependencias import CuerpoCrudo, ServicioDep
from novamercado.contrato import (
    ErrorRespuesta,
    ObservacionesResponse,
    PrediccionResponse,
    SolicitudObservaciones,
    SolicitudPrediccion,
)
from novamercado.servicio.solicitudes import (
    ErrorAtencion,
    atender_observaciones,
    atender_prediccion,
)

router = APIRouter(prefix="/v1", tags=["prediccion"])

_ERRORES = {
    422: {"model": ErrorRespuesta, "description": "Solicitud irreparable (`detail` legible)"},
    503: {"model": ErrorRespuesta, "description": "Sin modelo o base de datos no disponible"},
}


def _cuerpo_documentado(modelo: type) -> dict:
    """Esquema del cuerpo para OpenAPI (la lectura real es tolerante y no usa este modelo)."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": modelo.model_json_schema()}},
        }
    }


def _error(exc: ErrorAtencion) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@router.post(
    "/prediccion",
    response_model=PrediccionResponse,
    responses=_ERRORES,
    openapi_extra=_cuerpo_documentado(SolicitudPrediccion),
)
def prediccion(cuerpo: CuerpoCrudo, servicio: ServicioDep) -> JSONResponse:
    try:
        respuesta = atender_prediccion(
            cuerpo,
            catalogo=servicio.catalogo,
            conectar=servicio.conectar,
            modelo=servicio.gestor.actual,  # una sola referencia para toda la solicitud
        )
    except ErrorAtencion as exc:
        return _error(exc)
    return JSONResponse(status_code=200, content=respuesta.model_dump(mode="json"))


@router.post(
    "/observaciones",
    response_model=ObservacionesResponse,
    responses=_ERRORES,
    openapi_extra=_cuerpo_documentado(SolicitudObservaciones),
)
def observaciones(cuerpo: CuerpoCrudo, servicio: ServicioDep) -> JSONResponse:
    try:
        respuesta = atender_observaciones(
            cuerpo, catalogo=servicio.catalogo, conectar=servicio.conectar
        )
    except ErrorAtencion as exc:
        return _error(exc)
    return JSONResponse(status_code=200, content=respuesta.model_dump(mode="json"))
