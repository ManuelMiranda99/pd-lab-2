"""Alta y estado de un reentrenamiento: fila de la BD + dag run en Airflow (DECISIONES §6.1–§6.4).

El tiempo transcurrido se mide con `now()` de Postgres (`Reentrenamiento.segundos`), nunca con el
reloj de la API, y solo sirve para detectar ejecuciones que ya no van a terminar.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg

from novamercado.db import repos
from novamercado.servicio.airflow import AirflowClient, AirflowNoDisponible

log = logging.getLogger(__name__)

ESTADOS_EN_CURSO = {"queued", "running", "up_for_retry", "scheduled", "restarting"}

#: `dagrun_timeout` del DAG (9 min) + margen para que Airflow lo marque `failed`. Pasado este
#: tiempo, una ejecución sin resultado y sin dag run activo se declara `fallido`.
LIMITE_SEGUNDOS = 11 * 60


class NoSePuedeIniciar(RuntimeError):
    """El reentrenamiento no puede iniciarse (→ 503 con este texto como `detail`)."""


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


def _cerrar_desde_api(
    conn: psycopg.Connection,
    fila: repos.Reentrenamiento,
    motivo: str,
    version_vigente: str | None,
) -> dict[str, Any]:
    """El DAG ya no va a escribir el resultado: se escribe aquí, coherente con el alias.

    Si la etapa `promover` alcanzó a mover el alias, el modelo nuevo está en uso: `exitoso`.
    """
    d = fila.detalle
    if d.get("alias_movido"):
        repos.terminar_reentrenamiento(
            conn,
            fila.id,
            estado="exitoso",
            promovido=True,
            version=str(d.get("version_registrada")),
            motivo=f"{d.get('motivo_provisional', '')} ({motivo})".strip(),
        )
    else:
        repos.terminar_reentrenamiento(
            conn,
            fila.id,
            estado="fallido",
            promovido=False,
            version=version_vigente or d.get("version_previa"),
            motivo=f"{motivo}. El modelo vigente no cambió",
        )
    final = repos.obtener_reentrenamiento(conn, fila.id)
    return cuerpo_terminal(final, version_vigente) if final else {"estado": "fallido"}


def resolver_estado(
    conn: psycopg.Connection,
    fila: repos.Reentrenamiento,
    airflow: AirflowClient,
    dag_id: str,
    *,
    version_vigente: str | None,
) -> dict[str, Any]:
    """Devuelve el cuerpo de `GET /admin/reentrenar/{id}` (y persiste los cierres que deduce).

    - Fila terminal → tal cual.
    - En curso → se consulta Airflow: run activo → `en_curso`; `failed`, o `success` sin
      resultado → se cierra. Airflow caído o run inexistente → `en_curso` hasta
      `LIMITE_SEGUNDOS`; después → se cierra como vencido.
    """
    if fila.estado in repos.ESTADOS_TERMINALES:
        return cuerpo_terminal(fila, version_vigente)
    try:
        estado = airflow.estado_run(dag_id, fila.id)
    except AirflowNoDisponible:
        estado = "sin_respuesta"
    if estado in ESTADOS_EN_CURSO:
        return {"estado": "en_curso"}
    if estado in ("sin_respuesta", None):
        if fila.segundos <= LIMITE_SEGUNDOS:
            return {"estado": "en_curso"}
        causa = "Airflow no responde" if estado else "Airflow no tiene el dag run"
        return _cerrar_desde_api(
            conn,
            fila,
            f"Sin resultado tras {int(fila.segundos)} s ({causa})",
            version_vigente,
        )
    # Terminó en Airflow: releer por si la escritura del DAG llegó justo ahora.
    nueva = repos.obtener_reentrenamiento(conn, fila.id)
    if nueva is not None and nueva.estado in repos.ESTADOS_TERMINALES:
        return cuerpo_terminal(nueva, version_vigente)
    nueva = nueva or fila
    etapa = nueva.detalle.get("etapa", "desconocida")
    if estado == "success":
        motivo = "El DAG terminó sin registrar resultado"
    else:
        motivo = f"El DAG terminó en estado '{estado}' (etapa '{etapa}')"
        if nueva.detalle.get("error"):
            motivo += f": {nueva.detalle['error']}"
    return _cerrar_desde_api(conn, nueva, motivo, version_vigente)


@dataclass(frozen=True)
class Alta:
    id: str
    nuevo: bool  # False: se devolvió el id de una ejecución que ya estaba en curso


def iniciar(
    conn: psycopg.Connection,
    airflow: AirflowClient,
    dag_id: str,
    *,
    mlflow_disponible: bool,
    version_vigente: str | None,
) -> Alta:
    """`POST /admin/reentrenar` (DECISIONES §6.1 y §6.2).

    Con el bloqueo consultivo tomado: si hay una ejecución en curso (confirmada con Airflow), se
    devuelve su id; si no, se comprueban MLflow y el DAG, se crea la fila y se dispara. Si el
    disparo falla, la fila queda `fallido` (auditoría) y se lanza `NoSePuedeIniciar`.
    """
    with repos.bloqueo_reentrenos(conn):
        actual = repos.reentrenamiento_en_curso(conn)
        if actual is not None:
            cuerpo = resolver_estado(conn, actual, airflow, dag_id, version_vigente=version_vigente)
            if cuerpo["estado"] == "en_curso":
                return Alta(actual.id, nuevo=False)
        if not mlflow_disponible:
            raise NoSePuedeIniciar("MLflow no disponible: no se puede reentrenar")
        # Mismo criterio que /health: sin scheduler el dag run quedaría en cola sin avanzar.
        if not airflow.salud():
            raise NoSePuedeIniciar("Airflow no disponible (metadatabase o scheduler caídos)")
        try:
            activo = airflow.dag_activo(dag_id)
        except AirflowNoDisponible as exc:
            raise NoSePuedeIniciar("Airflow no disponible") from exc
        if not activo:
            raise NoSePuedeIniciar(f"El DAG '{dag_id}' no existe o está pausado en Airflow")
        id_ = uuid.uuid4().hex
        repos.crear_reentrenamiento(conn, id_)
        try:
            airflow.disparar(dag_id, id_)
        except AirflowNoDisponible as exc:
            log.warning("No se pudo disparar el DAG: %s", exc)
            repos.terminar_reentrenamiento(
                conn,
                id_,
                estado="fallido",
                promovido=False,
                version=version_vigente,
                motivo=f"No se pudo iniciar en Airflow: {exc}",
            )
            raise NoSePuedeIniciar("Airflow no disponible") from exc
    return Alta(id_, nuevo=True)
