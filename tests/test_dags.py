"""El DAG `reentrenamiento` carga sin errores y respeta la orquestación de DECISIONES §6.4.

Necesita Airflow instalado: corre en el job `dags` de la CI (fuera de él se omite).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("airflow", reason="Airflow no instalado: la prueba corre en el job 'dags'")

DIR_DAGS = Path(__file__).resolve().parents[1] / "dags"


@pytest.fixture(scope="module")
def bolsa():
    from airflow.dag_processing.dagbag import DagBag

    return DagBag(dag_folder=str(DIR_DAGS))


@pytest.fixture(scope="module")
def dag(bolsa):
    return bolsa.dags["reentrenamiento"]  # get_dag consultaría la BD de Airflow


def test_sin_errores_de_importacion(bolsa):
    assert bolsa.import_errors == {}
    assert sorted(bolsa.dag_ids) == ["reentrenamiento"]


def test_sin_schedule_una_ejecucion_a_la_vez_y_limite_de_tiempo(dag):
    assert dag.schedule is None
    assert type(dag.timetable).__name__ == "NullTimetable"
    assert dag.max_active_runs == 1
    assert not dag.catchup
    assert dag.dagrun_timeout is not None and dag.dagrun_timeout.total_seconds() <= 10 * 60


def test_orden_de_las_etapas(dag):
    assert set(dag.task_ids) == {"verificar", "entrenar", "promover", "confirmar", "cerrar", "fin"}
    cadena = ["verificar", "entrenar", "promover", "confirmar"]
    for antes, despues in zip(cadena, cadena[1:], strict=False):
        assert dag.get_task(antes).downstream_task_ids == {despues, "cerrar"}
    assert dag.get_task("confirmar").downstream_task_ids == {"fin", "cerrar"}
    cerrar = dag.get_task("cerrar")
    assert cerrar.upstream_task_ids == set(cadena)
    assert cerrar.trigger_rule == "all_done"
    # `fin` es hoja: si una etapa falla queda `upstream_failed` y el dag run termina `failed`.
    assert {t.task_id for t in dag.leaves} == {"fin", "cerrar"}
    assert dag.get_task("fin").trigger_rule == "all_success"


def test_cada_tarea_llama_a_su_etapa_con_el_run_id(dag):
    # Se lee el módulo sin importarlo: el entorno de Airflow no tiene las dependencias del ML.
    modulo = DIR_DAGS.parent / "src" / "novamercado" / "modelo" / "etapas.py"
    funciones = {
        n.name for n in ast.parse(modulo.read_text()).body if isinstance(n, ast.FunctionDef)
    }
    for tarea in dag.tasks:
        if tarea.task_id == "fin":
            continue
        assert tarea.op_kwargs == {"etapa": tarea.task_id, "id_reentreno": "{{ run_id }}"}
        assert tarea.task_id in funciones
        assert tarea.retries == 0
