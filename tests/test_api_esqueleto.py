"""API del esqueleto con TestClient y dependencias falsas (sin MLflow ni Airflow reales)."""

from __future__ import annotations

from datetime import date

import httpx
import psycopg
import pytest
from api.dependencias import Servicio
from api.main import crear_app
from fastapi.testclient import TestClient

from ayudas_esqueleto import FAMILIAS, escribir_csvs
from ayudas_esqueleto import bd as _fixture_bd  # noqa: F401 — fixture
from ayudas_esqueleto import bd_url as _fixture_bd_url  # noqa: F401 — fixture
from novamercado.config import Config
from novamercado.db.carga import cargar_datos
from novamercado.modelo.pyfunc import ModeloNovaMercado
from novamercado.servicio.airflow import AirflowClient
from novamercado.servicio.cargador import ModeloCargado
from novamercado.servicio.salud import MonitorSalud


class GestorFalso:
    def __init__(self, actual: ModeloCargado | None = None) -> None:
        self.actual = actual
        self.version_pendiente: str | None = None


def _airflow(estado_disparo: int = 200) -> AirflowClient:
    def manejar(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/token":
            return httpx.Response(201, json={"access_token": "t"})
        if request.url.path == "/api/v2/monitor/health":
            sano = {"status": "healthy"}
            return httpx.Response(200, json={"metadatabase": sano, "scheduler": sano})
        if request.method == "POST":
            return httpx.Response(estado_disparo, json={})
        if request.url.path == "/api/v2/dags/reentrenamiento":
            return httpx.Response(200, json={"is_paused": False})
        return httpx.Response(200, json={"state": "running"})

    return AirflowClient("http://airflow", "u", "p", transporte=httpx.MockTransport(manejar))


def _sin_bd() -> psycopg.Connection:
    raise psycopg.OperationalError("sin BD")


class PyfuncFalso:
    """Imita `PyFuncModel.predict(df)` sobre el pyfunc real, sin pasar por MLflow."""

    def __init__(self) -> None:
        from novamercado.modelo.baseline import BaselineDiaSemana

        self._interno = ModeloNovaMercado(BaselineDiaSemana(factores={"abarrotes": 1.2}))

    def predict(self, df):
        return self._interno.predict(None, df)


def _modelo() -> ModeloCargado:
    return ModeloCargado(
        PyfuncFalso(),
        "novamercado",
        "champion",
        "3",
        "mlflow",
    )


def catalogo_sintetico():
    """Catálogo equivalente al de `escribir_csvs` (para pruebas sin BD)."""
    from novamercado.limpieza import Catalogo, Tienda

    return Catalogo(
        tiendas={t: Tienda(t, f"Tienda {t}", date(2019, 1, 1)) for t in (1, 2)},
        familias={f: f.capitalize() for f in FAMILIAS},
        fecha_inicio=date(2025, 1, 6),
        fecha_fin_cobertura=date(2025, 12, 31),
    )


def _cliente(
    *, modelo=None, salud=None, conectar=_sin_bd, airflow=None, catalogo=None
) -> tuple[TestClient, MonitorSalud]:
    monitor = MonitorSalud({})
    if salud is not None:
        from novamercado.servicio.salud import EstadoSalud

        monitor._estado = EstadoSalud(salud)
    svc = Servicio(
        Config(), GestorFalso(modelo), monitor, airflow or _airflow(), conectar, catalogo
    )
    return TestClient(crear_app(svc, arrancar_hilos=False)), monitor


TODO_OK = {"base_de_datos": True, "mlflow": True, "airflow": True}


def test_health_503_sin_modelo():
    cliente, _ = _cliente(salud=TODO_OK)
    with cliente:
        r = cliente.get("/health")
    assert r.status_code == 503 and r.json()["estado"] == "degradado"


def test_health_ok_y_degradado_con_mlflow_caido():
    cliente, monitor = _cliente(modelo=_modelo(), salud=TODO_OK)
    with cliente:
        assert cliente.get("/health").json()["estado"] == "ok"
        from novamercado.servicio.salud import EstadoSalud

        monitor._estado = EstadoSalud({**TODO_OK, "mlflow": False})
        r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json()["estado"] == "degradado" and "mlflow" in r.json()["motivo"]


def test_modelo_en_memoria_con_campos_de_texto():
    cliente, _ = _cliente(modelo=_modelo())
    with cliente:
        r = cliente.get("/modelo")
    assert r.json() == {"nombre": "novamercado", "alias": "champion", "version": "3"}
    cliente, _ = _cliente()
    with cliente:
        assert cliente.get("/modelo").status_code == 503


def test_prediccion_sin_modelo_es_503_y_cuerpo_invalido_es_422():
    cliente, _ = _cliente(catalogo=catalogo_sintetico)
    cuerpo = {
        "tienda_id": 1,
        "fecha": "2025-01-20",
        "familias": [{"familia_id": "abarrotes", "en_promocion": 0}],
    }
    with cliente:
        assert cliente.post("/v1/prediccion", json=cuerpo).status_code == 503
        r = cliente.post("/v1/prediccion", json={"tienda_id": 1})
    assert r.status_code == 422 and "detail" in r.json()


def test_prediccion_con_bd_caida_es_503_no_500():
    cliente, _ = _cliente(modelo=_modelo(), catalogo=catalogo_sintetico)
    cuerpo = {
        "tienda_id": 1,
        "fecha": "2025-01-20",
        "familias": [{"familia_id": "abarrotes", "en_promocion": 0}],
    }
    with cliente:
        assert cliente.post("/v1/prediccion", json=cuerpo).status_code == 503


def test_reentrenar_con_mlflow_caido_es_503():
    cliente, _ = _cliente(modelo=_modelo(), salud={**TODO_OK, "mlflow": False})
    with cliente:
        assert cliente.post("/admin/reentrenar").status_code == 503


# --- con Postgres ----------------------------------------------------------------------------


@pytest.fixture
def con_datos(bd, bd_url, tmp_path):
    escribir_csvs(tmp_path, dias=28)  # 2025-01-06 … 2025-02-02
    cargar_datos(bd, tmp_path)
    return bd_url


@pytest.mark.integracion
def test_prediccion_contrato_y_sin_fuga(con_datos):
    def conectar():
        return psycopg.connect(con_datos)

    cliente, _ = _cliente(modelo=_modelo(), conectar=conectar)
    cuerpo = {
        "tienda_id": 1,
        "fecha": "2025-01-27",
        "familias": [{"familia_id": f, "en_promocion": 0} for f in FAMILIAS],
    }
    with cliente:
        r = cliente.post("/v1/prediccion", json=cuerpo)
        assert r.status_code == 200, r.text
        datos = r.json()
        assert datos["version_modelo"] == "3" and datos["fecha"] == "2025-01-27"
        assert datos["calidad"] == {"estado": "ok", "observaciones": []}
        assert [p["familia_id"] for p in datos["predicciones"]] == FAMILIAS
        for p in datos["predicciones"]:
            assert isinstance(p["cantidad_sugerida"], int) and p["cantidad_sugerida"] >= 0
        # Alterar la venta del mismo día D no cambia la predicción de D.
        with psycopg.connect(con_datos) as conn:
            conn.execute(
                "UPDATE historico_ventas SET ventas = 99999 WHERE fecha = %s", (date(2025, 1, 27),)
            )
        assert cliente.post("/v1/prediccion", json=cuerpo).json() == datos
        # Tienda o familia fuera del catálogo: 422 con detail.
        malo = {**cuerpo, "tienda_id": 99}
        assert cliente.post("/v1/prediccion", json=malo).status_code == 422
        malo = {**cuerpo, "familias": [{"familia_id": "inventada", "en_promocion": 0}]}
        assert cliente.post("/v1/prediccion", json=malo).status_code == 422


@pytest.mark.integracion
def test_reentrenar_202_estado_en_curso_y_404(con_datos):
    def conectar():
        return psycopg.connect(con_datos)

    cliente, _ = _cliente(modelo=_modelo(), salud=TODO_OK, conectar=conectar)
    with cliente:
        r = cliente.post("/admin/reentrenar")
        assert r.status_code == 202
        id_ = r.json()["id"]
        assert isinstance(id_, str) and len(id_) == 32
        assert cliente.get(f"/admin/reentrenar/{id_}").json() == {"estado": "en_curso"}
        assert cliente.get("/admin/reentrenar/inventado").status_code == 404


@pytest.mark.integracion
def test_reentrenar_con_airflow_rechazando_es_503_y_queda_fallido(con_datos):
    def conectar():
        return psycopg.connect(con_datos)

    cliente, _ = _cliente(
        modelo=_modelo(), salud=TODO_OK, conectar=conectar, airflow=_airflow(estado_disparo=500)
    )
    with cliente:
        assert cliente.post("/admin/reentrenar").status_code == 503
    with psycopg.connect(con_datos) as conn:
        estados = [e for (e,) in conn.execute("SELECT estado FROM reentrenamientos")]
    assert estados == ["fallido"]
