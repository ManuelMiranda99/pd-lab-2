"""`GET /health` y `GET /modelo` (desde el modelo en memoria, nunca desde el Registry)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.dependencias import ServicioDep
from api.esquemas_provisionales import ModeloInfo
from novamercado.servicio.salud import resumir

router = APIRouter(tags=["salud"])


@router.get("/health")
def health(servicio: ServicioDep) -> JSONResponse:
    codigo, cuerpo = resumir(servicio.monitor.estado, servicio.gestor.actual is not None)
    modelo = servicio.gestor.actual
    if modelo is not None:
        cuerpo["version_modelo"] = modelo.version
    return JSONResponse(status_code=codigo, content=cuerpo)


@router.get("/modelo", response_model=ModeloInfo)
def modelo(servicio: ServicioDep) -> ModeloInfo | JSONResponse:
    actual = servicio.gestor.actual
    if actual is None:
        return JSONResponse(status_code=503, content={"detail": "Sin modelo cargado todavía"})
    return ModeloInfo(nombre=actual.nombre, alias=actual.alias, version=actual.version)
