"""`/admin/reentrenar` con Postgres y un Airflow falso: alta única, estados y cierres."""

from __future__ import annotations

import threading

import httpx
import psycopg
import pytest

from ayudas_esqueleto import bd as _fixture_bd  # noqa: F401 — fixture
from ayudas_esqueleto import bd_url as _fixture_bd_url  # noqa: F401 — fixture
from novamercado.contrato import ReentrenoStatus
from novamercado.db import repos
from novamercado.servicio.airflow import AirflowClient
from test_api_esqueleto import TODO_OK, _cliente, _modelo

pytestmark = pytest.mark.integracion


class AirflowFalso:
    """Airflow con estado configurable; cuenta los disparos."""

    def __init__(self) -> None:
        self.estado_run: str | None = "running"
        self.pausado = False
        self.caido = False
        self.disparos: list[str] = []

    def manejar(self, request: httpx.Request) -> httpx.Response:
        if self.caido:
            raise httpx.ConnectError("sin Airflow")
        ruta = request.url.path
        if ruta == "/auth/token":
            return httpx.Response(201, json={"access_token": "t"})
        if request.method == "POST":
            self.disparos.append(request.read().decode())
            return httpx.Response(200, json={})
        if ruta == "/api/v2/dags/reentrenamiento":
            return httpx.Response(200, json={"is_paused": self.pausado})
        if self.estado_run is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"state": self.estado_run})

    def cliente(self) -> AirflowClient:
        return AirflowClient(
            "http://airflow", "u", "p", transporte=httpx.MockTransport(self.manejar)
        )


@pytest.fixture
def entorno(bd, bd_url):
    falso = AirflowFalso()

    def armar(salud=TODO_OK):
        cliente, _ = _cliente(
            modelo=_modelo(),
            salud=salud,
            conectar=lambda: psycopg.connect(bd_url),
            airflow=falso.cliente(),
        )
        return cliente

    return armar, falso, bd_url


def _envejecer(bd_url: str, id_: str, minutos: int) -> None:
    with psycopg.connect(bd_url) as conn:
        conn.execute(
            "UPDATE reentrenamientos SET creado_en = creado_en - make_interval(mins => %s)"
            " WHERE id = %s",
            (minutos, id_),
        )


def _detalle(bd_url: str, id_: str, detalle: dict) -> None:
    with psycopg.connect(bd_url) as conn:
        repos.actualizar_detalle(conn, id_, detalle)


def test_segundo_post_con_uno_en_curso_devuelve_el_mismo_id(entorno):
    armar, falso, _ = entorno
    with armar() as cliente:
        a = cliente.post("/admin/reentrenar")
        b = cliente.post("/admin/reentrenar")
    assert a.status_code == b.status_code == 202
    assert a.json()["id"] == b.json()["id"]
    assert len(falso.disparos) == 1


def test_posts_simultaneos_crean_una_sola_ejecucion(entorno):
    armar, falso, bd_url = entorno
    ids: list[str] = []
    with armar() as cliente:

        def pedir() -> None:
            ids.append(cliente.post("/admin/reentrenar").json()["id"])

        hilos = [threading.Thread(target=pedir) for _ in range(6)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
    assert len(set(ids)) == 1 and len(ids) == 6
    with psycopg.connect(bd_url) as conn:
        assert conn.execute("SELECT count(*) FROM reentrenamientos").fetchone()[0] == 1


def test_en_curso_que_fallo_en_airflow_se_cierra_y_se_dispara_otro(entorno):
    armar, falso, bd_url = entorno
    with armar() as cliente:
        primero = cliente.post("/admin/reentrenar").json()["id"]
        _detalle(bd_url, primero, {"etapa": "entrenar", "error": "sin memoria"})
        falso.estado_run = "failed"
        segundo = cliente.post("/admin/reentrenar").json()["id"]
        assert segundo != primero
        cuerpo = cliente.get(f"/admin/reentrenar/{primero}").json()
    assert cuerpo["estado"] == "fallido" and cuerpo["promovido"] is False
    assert cuerpo["version"] == "3"  # la del modelo vigente
    assert "etapa 'entrenar'" in cuerpo["motivo"] and "sin memoria" in cuerpo["motivo"]


def test_mlflow_caido_503_salvo_que_ya_haya_uno_en_curso(entorno):
    armar, falso, _ = entorno
    with armar({**TODO_OK, "mlflow": False}) as cliente:
        r = cliente.post("/admin/reentrenar")
        assert r.status_code == 503 and "MLflow" in r.json()["detail"]
    with armar() as cliente:
        id_ = cliente.post("/admin/reentrenar").json()["id"]
    with armar({**TODO_OK, "mlflow": False}) as cliente:
        r = cliente.post("/admin/reentrenar")
    assert r.status_code == 202 and r.json()["id"] == id_


def test_dag_pausado_o_airflow_caido_es_503_sin_crear_fila(entorno):
    armar, falso, bd_url = entorno
    falso.pausado = True
    with armar() as cliente:
        r = cliente.post("/admin/reentrenar")
        assert r.status_code == 503 and "pausado" in r.json()["detail"]
        falso.pausado, falso.caido = False, True
        r = cliente.post("/admin/reentrenar")
        assert r.status_code == 503 and "Airflow" in r.json()["detail"]
    with psycopg.connect(bd_url) as conn:
        assert conn.execute("SELECT count(*) FROM reentrenamientos").fetchone()[0] == 0


def test_airflow_caido_es_en_curso_hasta_el_limite_y_luego_fallido(entorno):
    armar, falso, bd_url = entorno
    with armar() as cliente:
        id_ = cliente.post("/admin/reentrenar").json()["id"]
        falso.caido = True
        assert cliente.get(f"/admin/reentrenar/{id_}").json() == {"estado": "en_curso"}
        _envejecer(bd_url, id_, 12)
        cuerpo = cliente.get(f"/admin/reentrenar/{id_}").json()
    assert cuerpo["estado"] == "fallido" and "Airflow no responde" in cuerpo["motivo"]
    ReentrenoStatus(**cuerpo)


def test_run_vencido_con_alias_ya_movido_es_exitoso(entorno):
    armar, falso, bd_url = entorno
    with armar() as cliente:
        id_ = cliente.post("/admin/reentrenar").json()["id"]
        _detalle(
            bd_url,
            id_,
            {
                "etapa": "confirmar",
                "alias_movido": True,
                "version_registrada": "4",
                "motivo_provisional": "Costo 100 vs 120: promovido",
            },
        )
        falso.estado_run = "failed"  # p. ej. `dagrun_timeout` antes de confirmar
        cuerpo = cliente.get(f"/admin/reentrenar/{id_}").json()
    assert cuerpo == {
        "estado": "exitoso",
        "promovido": True,
        "version": "4",
        "motivo": cuerpo["motivo"],
    }
    assert cuerpo["motivo"].startswith("Costo 100 vs 120: promovido")


def test_success_sin_resultado_es_fallido_y_terminal_se_devuelve_tal_cual(entorno):
    armar, falso, bd_url = entorno
    with armar() as cliente:
        id_ = cliente.post("/admin/reentrenar").json()["id"]
        falso.estado_run = "success"
        cuerpo = cliente.get(f"/admin/reentrenar/{id_}").json()
        assert cuerpo["estado"] == "fallido" and "sin registrar" in cuerpo["motivo"]
        falso.caido = True  # una fila terminal ya no consulta Airflow
        assert cliente.get(f"/admin/reentrenar/{id_}").json() == cuerpo


def test_id_con_caracteres_raros_es_404(entorno):
    armar, _, _ = entorno
    with armar() as cliente:
        assert cliente.get("/admin/reentrenar/a%00b").status_code == 404
        assert cliente.get("/admin/reentrenar/%F0%9F%98%80").status_code == 404
