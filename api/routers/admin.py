"""`POST /admin/reentrenar` y `GET /admin/reentrenar/{id}` (la lógica vive en
`novamercado.servicio.reentreno`).

- El id lo genera la API (`uuid4().hex`) y es también el `dag_run_id` de Airflow.
- Con un reentreno ya en curso se responde 202 con **el id de esa ejecución** (DECISIONES §6.1).
- MLflow, Airflow o la BD caídos, o el DAG pausado → 503 con el motivo (DECISIONES §6.2).
"""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from api.dependencias import ServicioDep, abrir_conexion
from novamercado.contrato import ReentrenoIniciado, ReentrenoStatus
from novamercado.db import repos
from novamercado.servicio import reentreno

router = APIRouter(prefix="/admin", tags=["admin"])

_BD_CAIDA = "Base de datos no disponible"


@router.post("/reentrenar", status_code=202)
def reentrenar(servicio: ServicioDep) -> JSONResponse:
    actual = servicio.gestor.actual
    with abrir_conexion(servicio) as conn:
        try:
            alta = reentreno.iniciar(
                conn,
                servicio.airflow,
                servicio.config.dag_id,
                mlflow_disponible=servicio.monitor.estado.componentes.get("mlflow", False),
                version_vigente=actual.version if actual else None,
            )
        except reentreno.NoSePuedeIniciar as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail=_BD_CAIDA) from exc
    return JSONResponse(
        status_code=202, content=ReentrenoIniciado(id=alta.id).model_dump(mode="json")
    )


@router.post("/reentrenar/", status_code=202, include_in_schema=False)
def reentrenar_con_barra(servicio: ServicioDep) -> JSONResponse:
    """Misma operación con barra final (una ruta GET con barra impide la redirección)."""
    return reentrenar(servicio)


@router.get("/reentrenar/", include_in_schema=False)
def estado_sin_id() -> JSONResponse:
    """Id vacío: no existe esa ejecución (404, en vez de la redirección a la ruta del POST)."""
    return JSONResponse(status_code=404, content={"detail": "No existe ese reentrenamiento"})


@router.get("/reentrenar/{id_}")
def estado_reentreno(id_: str, servicio: ServicioDep) -> JSONResponse:
    actual = servicio.gestor.actual
    if "\x00" in id_:  # Postgres no admite NUL en texto: no puede existir
        raise HTTPException(status_code=404, detail="No existe ese reentrenamiento")
    with abrir_conexion(servicio) as conn:
        try:
            fila = repos.obtener_reentrenamiento(conn, id_)
            if fila is None:
                raise HTTPException(status_code=404, detail=f"No existe el reentrenamiento {id_}")
            cuerpo = reentreno.resolver_estado(
                conn,
                fila,
                servicio.airflow,
                servicio.config.dag_id,
                version_vigente=actual.version if actual else None,
            )
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail=_BD_CAIDA) from exc
    return JSONResponse(status_code=200, content=ReentrenoStatus(**cuerpo).model_dump(mode="json"))
