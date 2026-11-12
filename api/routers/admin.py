"""`POST /admin/reentrenar` y `GET /admin/reentrenar/{id}`.

- El id lo genera la API (`uuid4().hex`) y es también el `dag_run_id` de Airflow.
- La fila se crea en `reentrenamientos` **antes** de disparar; si Airflow rechaza o no responde,
  la fila queda `fallido` y se responde 503.
- Con un reentreno ya en curso se responde 202 y el run queda en cola (`max_active_runs=1`).
- Con MLflow caído se responde 503 (el entrenamiento fallaría).
"""

from __future__ import annotations

import logging
import uuid

import psycopg
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api.dependencias import ServicioDep, abrir_conexion
from novamercado.db import repos
from novamercado.servicio.airflow import AirflowNoDisponible
from novamercado.servicio.reentreno import resolver_estado

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"])

_BD_CAIDA = "Base de datos no disponible"


@router.post("/reentrenar", status_code=202)
def reentrenar(servicio: ServicioDep) -> JSONResponse:
    if not servicio.monitor.estado.componentes.get("mlflow", False):
        raise HTTPException(status_code=503, detail="MLflow no disponible: no se puede reentrenar")
    id_ = uuid.uuid4().hex
    with abrir_conexion(servicio) as conn:
        try:
            repos.crear_reentrenamiento(conn, id_)
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail=_BD_CAIDA) from exc
        try:
            servicio.airflow.disparar(servicio.config.dag_id, id_)
        except AirflowNoDisponible as exc:
            log.warning("No se pudo disparar el DAG: %s", exc)
            actual = servicio.gestor.actual
            repos.terminar_reentrenamiento(
                conn,
                id_,
                estado="fallido",
                promovido=False,
                version=actual.version if actual else None,
                motivo=f"No se pudo iniciar en Airflow: {exc}",
            )
            raise HTTPException(status_code=503, detail="Airflow no disponible") from exc
    return JSONResponse(status_code=202, content={"id": id_})


@router.get("/reentrenar/{id_}")
def estado_reentreno(id_: str, servicio: ServicioDep) -> JSONResponse:
    actual = servicio.gestor.actual
    with abrir_conexion(servicio) as conn:
        try:
            fila = repos.obtener_reentrenamiento(conn, id_)
            if fila is None:
                raise HTTPException(status_code=404, detail=f"No existe el reentrenamiento {id_}")
            cuerpo = resolver_estado(
                conn,
                fila,
                servicio.airflow,
                servicio.config.dag_id,
                version_vigente=actual.version if actual else None,
            )
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail=_BD_CAIDA) from exc
    return JSONResponse(status_code=200, content=cuerpo)
