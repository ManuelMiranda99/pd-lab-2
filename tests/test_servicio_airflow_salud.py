"""Cliente de Airflow (transporte simulado), salud y estado de reentrenos (sin servicios reales)."""

from __future__ import annotations

import json

import httpx
import pytest

from novamercado.db import repos
from novamercado.servicio import reentreno
from novamercado.servicio.airflow import AirflowClient, AirflowNoDisponible
from novamercado.servicio.salud import EstadoSalud, MonitorSalud, resumir


class AirflowSimulado:
    def __init__(self) -> None:
        self.tokens = 0
        self.vencer_una_vez = False
        self.peticiones: list[httpx.Request] = []
        self.estado = "running"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.peticiones.append(request)
        ruta = request.url.path
        if ruta == "/auth/token":
            self.tokens += 1
            return httpx.Response(201, json={"access_token": f"t{self.tokens}"})
        if ruta == "/api/v2/monitor/health":
            return httpx.Response(
                200,
                json={"metadatabase": {"status": "healthy"}, "scheduler": {"status": "healthy"}},
            )
        if self.vencer_una_vez:
            self.vencer_una_vez = False
            return httpx.Response(401)
        if ruta.endswith("/dagRuns") and request.method == "POST":
            return httpx.Response(
                200, json={"dag_run_id": json.loads(request.content)["dag_run_id"]}
            )
        if ruta.endswith("/dagRuns/desconocido"):
            return httpx.Response(404)
        if "/dagRuns/" in ruta:
            return httpx.Response(200, json={"state": self.estado})
        if ruta == "/api/v2/dags/reentrenamiento":
            return httpx.Response(200, json={"is_paused": False})
        return httpx.Response(404)


@pytest.fixture
def airflow():
    sim = AirflowSimulado()
    return AirflowClient("http://airflow", "u", "p", transporte=httpx.MockTransport(sim)), sim


def test_disparo_usa_token_y_run_id(airflow):
    cliente, sim = airflow
    cliente.disparar("reentrenamiento", "abc123")
    post = sim.peticiones[-1]
    assert post.headers["Authorization"] == "Bearer t1"
    assert json.loads(post.content) == {"dag_run_id": "abc123", "logical_date": None, "conf": {}}


def test_token_vencido_se_renueva_una_vez(airflow):
    cliente, sim = airflow
    cliente.estado_run("reentrenamiento", "x")
    sim.vencer_una_vez = True
    assert cliente.estado_run("reentrenamiento", "x") == "running"
    assert sim.tokens == 2


def test_estado_run_desconocido_y_salud(airflow):
    cliente, _ = airflow
    assert cliente.estado_run("reentrenamiento", "desconocido") is None
    assert cliente.salud() is True
    assert cliente.dag_activo("reentrenamiento") is True


def test_airflow_caido_lanza_error_controlado():
    def caido(request):
        raise httpx.ConnectError("sin conexión")

    cliente = AirflowClient("http://airflow", "u", "p", transporte=httpx.MockTransport(caido))
    with pytest.raises(AirflowNoDisponible):
        cliente.disparar("reentrenamiento", "x")
    assert cliente.salud() is False


# --- salud -----------------------------------------------------------------------------------


def test_health_sin_modelo_es_503():
    codigo, cuerpo = resumir(EstadoSalud({"mlflow": True}), hay_modelo=False)
    assert codigo == 503 and cuerpo["estado"] == "degradado"


def test_health_ok_y_degradado():
    assert resumir(EstadoSalud({"mlflow": True, "airflow": True}), True)[1]["estado"] == "ok"
    codigo, cuerpo = resumir(EstadoSalud({"mlflow": False, "airflow": True}), True)
    assert codigo == 200 and cuerpo["estado"] == "degradado" and "mlflow" in cuerpo["motivo"]


def test_monitor_cuenta_excepciones_como_caidas():
    def explota():
        raise RuntimeError("x")

    monitor = MonitorSalud({"bd": lambda: True, "mlflow": explota})
    assert monitor.estado.caidos == ["bd", "mlflow"]  # antes del primer chequeo, nada está ok
    assert monitor.revisar().componentes == {"bd": True, "mlflow": False}


# --- estado de un reentreno ------------------------------------------------------------------


class ReposFalsos:
    def __init__(self, fila: repos.Reentrenamiento) -> None:
        self.fila = fila

    def obtener(self, conn, id_):
        return self.fila

    def terminar(self, conn, id_, **kw):
        self.fila = repos.Reentrenamiento(
            id_, kw["estado"], kw["promovido"], kw["version"], kw["motivo"]
        )
        return True


@pytest.fixture
def repos_falsos(monkeypatch):
    falso = ReposFalsos(repos.Reentrenamiento("r1", "en_curso", None, None, None))
    monkeypatch.setattr(repos, "obtener_reentrenamiento", falso.obtener)
    monkeypatch.setattr(repos, "terminar_reentrenamiento", falso.terminar)
    return falso


def test_reentreno_en_curso_omite_campos(airflow, repos_falsos):
    cliente, sim = airflow
    sim.estado = "queued"
    cuerpo = reentreno.resolver_estado(
        None, repos_falsos.fila, cliente, "reentrenamiento", version_vigente="1"
    )
    assert cuerpo == {"estado": "en_curso"}


def test_reentreno_fallido_en_airflow_se_persiste_con_version_vigente(airflow, repos_falsos):
    cliente, sim = airflow
    sim.estado = "failed"
    cuerpo = reentreno.resolver_estado(
        None, repos_falsos.fila, cliente, "reentrenamiento", version_vigente="4"
    )
    assert cuerpo["estado"] == "fallido" and cuerpo["promovido"] is False
    assert cuerpo["version"] == "4" and cuerpo["motivo"]


def test_reentreno_terminal_se_devuelve_tal_cual(airflow, repos_falsos):
    cliente, _ = airflow
    fila = repos.Reentrenamiento("r1", "exitoso", True, "5", "Costo 1 vs 2 del vigente")
    cuerpo = reentreno.resolver_estado(None, fila, cliente, "reentrenamiento", version_vigente="4")
    assert cuerpo == {
        "estado": "exitoso",
        "promovido": True,
        "version": "5",
        "motivo": "Costo 1 vs 2 del vigente",
    }


def test_airflow_caido_mantiene_en_curso(repos_falsos):
    def caido(request):
        raise httpx.ConnectError("sin conexión")

    cliente = AirflowClient("http://a", "u", "p", transporte=httpx.MockTransport(caido))
    cuerpo = reentreno.resolver_estado(
        None, repos_falsos.fila, cliente, "reentrenamiento", version_vigente="1"
    )
    assert cuerpo == {"estado": "en_curso"}


def test_reentreno_fallido_sin_version_usa_la_del_modelo_en_memoria(airflow, repos_falsos):
    cliente, _ = airflow
    fila = repos.Reentrenamiento("r1", "fallido", False, None, "MLflow caído")
    cuerpo = reentreno.resolver_estado(None, fila, cliente, "reentrenamiento", version_vigente="2")
    assert cuerpo["version"] == "2" and cuerpo["promovido"] is False
