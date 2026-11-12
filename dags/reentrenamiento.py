"""DAG `reentrenamiento`: solo orquesta; la lógica vive en `novamercado.modelo.etapas`.

- Se dispara desde `POST /admin/reentrenar` (sin schedule); el `run_id` es el id de la API. Un
  disparo manual desde la interfaz de Airflow también funciona: `verificar` crea la fila.
- Orden de escritura (DECISIONES §6.4):
  `verificar` → `entrenar` (registra la versión sin alias + resultado provisional en la BD)
  → `promover` (mueve `champion` solo si gana) → `confirmar` (escribe `exitoso`).
- `cerrar` (trigger `all_done`) deja la ejecución en un estado terminal si alguna etapa falló:
  `fallido` con la etapa y el error, o `exitoso` si el alias ya se había movido. Nunca toca el
  alias. `fin` (vacía, tras `confirmar`) es la otra hoja: si falla una etapa queda
  `upstream_failed` y el dag run termina `failed` aunque `cerrar` haya corrido bien.
- Cada tarea corre en un venv aislado (`requirements-ml.txt` + `novamercado`) con
  `ExternalPythonOperator`: el entorno de Airflow no carga pandas ni mlflow.
"""

from __future__ import annotations

import os
from datetime import timedelta

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.python import ExternalPythonOperator
from airflow.sdk import DAG

PYTHON_ML = os.environ.get("NOVAMERCADO_PYTHON_ML", "/opt/venv-ml/bin/python")


def ejecutar_etapa(etapa: str, id_reentreno: str) -> dict:
    """Corre dentro del venv ML: los imports pesados van aquí adentro."""
    import logging

    logging.basicConfig(level=logging.INFO)
    from novamercado.modelo import etapas

    return getattr(etapas, etapa)(id_reentreno)


def _tarea(etapa: str, **kwargs) -> ExternalPythonOperator:
    return ExternalPythonOperator(
        task_id=etapa,
        python=PYTHON_ML,
        python_callable=ejecutar_etapa,
        op_kwargs={"etapa": etapa, "id_reentreno": "{{ run_id }}"},
        expect_airflow=False,
        **kwargs,
    )


with DAG(
    dag_id="reentrenamiento",
    description="Reentrena el modelo y lo promueve con el alias si supera al vigente",
    schedule=None,
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=9),
    is_paused_upon_creation=False,
    default_args={"retries": 0},
    tags=["novamercado"],
) as dag:
    t_verificar = _tarea("verificar", execution_timeout=timedelta(minutes=1))
    t_entrenar = _tarea("entrenar", execution_timeout=timedelta(minutes=7))
    t_promover = _tarea("promover", execution_timeout=timedelta(minutes=1))
    t_confirmar = _tarea("confirmar", execution_timeout=timedelta(minutes=1))
    t_cerrar = _tarea("cerrar", trigger_rule="all_done", execution_timeout=timedelta(minutes=1))
    t_fin = EmptyOperator(task_id="fin")

    t_verificar >> t_entrenar >> t_promover >> t_confirmar >> t_fin
    [t_verificar, t_entrenar, t_promover, t_confirmar] >> t_cerrar
