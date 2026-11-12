"""Estado de un reentrenamiento: fila de la BD + estado del dag run en Airflow."""

from __future__ import annotations

from typing import Any

import psycopg

from novamercado.db import repos
from novamercado.servicio.airflow import AirflowClient, AirflowNoDisponible

ESTADOS_EN_CURSO = {None, "queued", "running", "up_for_retry", "scheduled", "restarting"}


def cuerpo_terminal(
    fila: repos.Reentrenamiento, version_vigente: str | None = None
) -> dict[str, Any]:
    """Cuerpo de una ejecución terminada. Si el DAG no pudo leer la versión vigente (p. ej.
    MLflow caído al fallar), se informa la del modelo en memoria."""
    return {
        "estado": fila.estado,
        "promovido": bool(fila.promovido),
        "version": fila.version if fila.version is not None else version_vigente,
        "motivo": fila.motivo or "",
    }


def resolver_estado(
    conn: psycopg.Connection,
    fila: repos.Reentrenamiento,
    airflow: AirflowClient,
    dag_id: str,
    *,
    version_vigente: str | None,
) -> dict[str, Any]:
    """Devuelve el cuerpo de `GET /admin/reentrenar/{id}`.

    - Fila terminal → se devuelve tal cual.
    - En curso → se consulta Airflow: `failed` o `success` sin resultado → `fallido` (y se
      persiste); Airflow caído o run aún activo → `en_curso` (sin promovido/version/motivo).
    """
    if fila.estado in repos.ESTADOS_TERMINALES:
        return cuerpo_terminal(fila, version_vigente)
    try:
        estado = airflow.estado_run(dag_id, fila.id)
    except AirflowNoDisponible:
        return {"estado": "en_curso"}
    if estado in ESTADOS_EN_CURSO:
        return {"estado": "en_curso"}
    if estado == "success":
        # El DAG escribe el resultado antes de terminar: si terminó y no está, releer por si
        # la escritura llegó justo ahora.
        nueva = repos.obtener_reentrenamiento(conn, fila.id)
        if nueva is not None and nueva.estado in repos.ESTADOS_TERMINALES:
            return cuerpo_terminal(nueva, version_vigente)
        motivo = "El DAG terminó sin registrar resultado"
    else:
        motivo = f"El DAG terminó en estado '{estado}'"
    repos.terminar_reentrenamiento(
        conn, fila.id, estado="fallido", promovido=False, version=version_vigente, motivo=motivo
    )
    final = repos.obtener_reentrenamiento(conn, fila.id)
    return cuerpo_terminal(final, version_vigente) if final else {"estado": "fallido"}
