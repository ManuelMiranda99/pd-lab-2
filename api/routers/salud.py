"""`GET /health` y `GET /modelo` (desde el modelo en memoria, nunca desde el Registry).

`/health` solo lee el último resultado del monitor de fondo (BD, MLflow, Airflow con el DAG
activo); nunca espera a una dependencia.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.dependencias import ServicioDep
from novamercado.contrato import ErrorRespuesta, Health, ModeloInfo
from novamercado.servicio.salud import resumir

router = APIRouter(tags=["salud"])


# `async def`: solo leen memoria, así que corren en el event loop y responden aunque el pool de
# hilos esté ocupado con predicciones (carga alta).
@router.get("/health", response_model=Health, responses={503: {"model": Health}})
async def health(servicio: ServicioDep) -> JSONResponse:
    modelo = servicio.gestor.actual  # una sola lectura: coherente con el cuerpo
    codigo, cuerpo = resumir(
        servicio.monitor.estado, modelo is not None, servicio.gestor.version_pendiente
    )
    if modelo is not None:
        cuerpo["version_modelo"] = modelo.version
    return JSONResponse(status_code=codigo, content=Health(**cuerpo).model_dump(mode="json"))


@router.get("/modelo", response_model=ModeloInfo, responses={503: {"model": ErrorRespuesta}})
async def modelo(servicio: ServicioDep) -> JSONResponse:
    actual = servicio.gestor.actual
    if actual is None:
        return JSONResponse(status_code=503, content={"detail": "Sin modelo cargado todavía"})
    info = ModeloInfo(nombre=actual.nombre, alias=actual.alias, version=actual.version)
    return JSONResponse(status_code=200, content=info.model_dump(mode="json"))
