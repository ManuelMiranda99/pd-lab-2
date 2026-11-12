"""DAG `reentrenamiento`: solo orquesta; la lógica vive en `novamercado.modelo.ciclo`.

- Se dispara desde `POST /admin/reentrenar` (sin schedule); el `run_id` es el id de la API.
- `entrenar` corre en un venv aislado (`requirements-ml.txt` + `novamercado`) con
  `ExternalPythonOperator`: el entorno de Airflow no carga pandas/mlflow. Escribe `exitoso` en
  `reentrenamientos` y mueve el alias solo si el retador gana.
- `registrar_fallo` (trigger `one_failed`) escribe `fallido` sin tocar el alias.
- `fin` deja el dag run en `failed` cuando `entrenar` falla (es la hoja que depende de él).
"""

from __future__ import annotations

import os
from datetime import timedelta

from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.python import ExternalPythonOperator
from airflow.sdk import DAG

PYTHON_ML = os.environ.get("NOVAMERCADO_PYTHON_ML", "/opt/venv-ml/bin/python")


def entrenar(id_reentreno: str) -> dict:
    """Corre dentro del venv ML: imports pesados aquí adentro."""
    import logging

    logging.basicConfig(level=logging.INFO)
    from novamercado.modelo.ciclo import ejecutar_reentrenamiento

    return ejecutar_reentrenamiento(id_reentreno)


def registrar_fallo(id_reentreno: str) -> None:
    from novamercado.modelo.ciclo import marcar_fallido

    marcar_fallido(id_reentreno, "El entrenamiento falló; el modelo vigente no cambió")


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
    t_entrenar = ExternalPythonOperator(
        task_id="entrenar",
        python=PYTHON_ML,
        python_callable=entrenar,
        op_kwargs={"id_reentreno": "{{ run_id }}"},
        expect_airflow=False,
        execution_timeout=timedelta(minutes=8),
    )
    t_fallo = ExternalPythonOperator(
        task_id="registrar_fallo",
        python=PYTHON_ML,
        python_callable=registrar_fallo,
        op_kwargs={"id_reentreno": "{{ run_id }}"},
        expect_airflow=False,
        trigger_rule="one_failed",
    )
    t_fin = EmptyOperator(task_id="fin")

    t_entrenar >> [t_fallo, t_fin]
