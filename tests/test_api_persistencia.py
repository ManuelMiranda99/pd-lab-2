"""API completa: persistencia de solicitudes, predicciones, correcciones y observaciones.

Las pruebas marcadas `integracion` usan Postgres (`TEST_DATABASE_URL`); ninguna usa MLflow.
"""

from __future__ import annotations

import inspect
import json
from datetime import date

import psycopg
import pytest
from fastapi.routing import APIRoute

from ayudas_esqueleto import FAMILIAS, escribir_csvs
from ayudas_esqueleto import bd as _fixture_bd  # noqa: F401 — fixture
from ayudas_esqueleto import bd_url as _fixture_bd_url  # noqa: F401 — fixture
from novamercado.contrato import ObservacionesResponse, PrediccionResponse
from novamercado.db.carga import cargar_datos
from test_api_esqueleto import TODO_OK, _cliente, _modelo, catalogo_sintetico

D = "2025-01-27"  # lunes; el histórico sintético va de 2025-01-06 a 2025-02-02


def _pred(**cambios):
    cuerpo = {
        "tienda_id": 1,
        "fecha": D,
        "familias": [{"familia_id": f, "en_promocion": 0} for f in FAMILIAS],
    }
    return cuerpo | cambios


def _obs(fecha: str, ventas: dict[str, object], tienda=1):
    return {
        "tienda_id": tienda,
        "fecha": fecha,
        "ventas": [{"familia_id": f, "ventas": v} for f, v in ventas.items()],
    }


# --- sin BD ----------------------------------------------------------------------------------


def test_endpoints_son_sincronos():
    """Los endpoints que usan la BD son `def`: FastAPI los corre en su pool de hilos y la BD
    síncrona no bloquea el event loop. `/health` y `/modelo` solo leen memoria: son `async`
    para responder aunque el pool esté ocupado con predicciones."""
    from api.routers import admin, prediccion, salud

    rutas = [r for m in (admin, prediccion, salud) for r in m.router.routes]
    assert {r.path for r in rutas} >= {
        "/v1/prediccion",
        "/v1/observaciones",
        "/admin/reentrenar",
        "/admin/reentrenar/{id_}",
        "/health",
        "/modelo",
    }
    for ruta in rutas:
        assert isinstance(ruta, APIRoute)
        es_async = inspect.iscoroutinefunction(ruta.endpoint)
        assert es_async == (ruta.path in {"/health", "/modelo"}), ruta.path


@pytest.mark.parametrize(
    ("cuerpo", "content_type"),
    [
        (b"", "application/json"),
        (b"   ", "application/json"),
        (b"{no es json", "application/json"),
        (b"[1, 2, 3]", "application/json"),
        (b'"texto"', "application/json"),
        (b"null", "application/json"),
        (b"\xff\xfe\x00basura", "application/octet-stream"),
        (b'{"tienda_id": 1}', "text/plain"),
        (b'{"tienda_id": 1, "fecha": "2025-13-45", "familias": []}', "application/json"),
        (b"{" * 5000, "application/json"),
    ],
)
@pytest.mark.parametrize("ruta", ["/v1/prediccion", "/v1/observaciones"])
def test_cuerpos_irreparables_son_422_con_detail_de_texto(ruta, cuerpo, content_type):
    cliente, _ = _cliente(modelo=_modelo(), catalogo=catalogo_sintetico)
    with cliente:
        r = cliente.post(ruta, content=cuerpo, headers={"Content-Type": content_type})
    assert r.status_code == 422, r.text
    assert isinstance(r.json()["detail"], str) and r.json()["detail"]


def test_json_invalido_es_422_aunque_la_bd_este_caida():
    cliente, _ = _cliente(modelo=_modelo())  # sin catálogo inyectado y sin BD
    with cliente:
        r = cliente.post("/v1/observaciones", content=b"{roto")
        assert r.status_code == 422
        # Un cuerpo legible necesita el catálogo: sin BD es 503, nunca 500.
        assert cliente.post("/v1/observaciones", json=_obs(D, {"abarrotes": 1})).status_code == 503


def test_observaciones_con_bd_caida_es_503():
    cliente, _ = _cliente(catalogo=catalogo_sintetico)
    with cliente:
        r = cliente.post("/v1/observaciones", json=_obs(D, {"abarrotes": 10}))
    assert r.status_code == 503 and r.json() == {"detail": "Base de datos no disponible"}


# --- con Postgres ----------------------------------------------------------------------------


@pytest.fixture
def url(bd, bd_url, tmp_path):
    escribir_csvs(tmp_path, dias=28)
    cargar_datos(bd, tmp_path)
    return bd_url


@pytest.fixture
def cliente(url):
    cli, _ = _cliente(modelo=_modelo(), salud=TODO_OK, conectar=lambda: psycopg.connect(url))
    with cli:
        yield cli


def _consulta(url: str, sql: str, *params):
    with psycopg.connect(url) as conn:
        return conn.execute(sql, params).fetchall()


@pytest.mark.integracion
def test_prediccion_se_guarda_completa_con_correcciones(cliente, url):
    cuerpo = {
        "tienda": "T01",  # alias de clave + id con prefijo → reparar
        "fecha": D,
        "familias": [
            {"familia_id": "ABARROTES", "en_promocion": "2"},
            {"familia_id": "lacteos", "en_promocion": 1},
        ],  # falta panaderia → se completa con 0 y se advierte
        "comentario": "clave desconocida",
    }
    r = cliente.post("/v1/prediccion", json=cuerpo)
    assert r.status_code == 200, r.text
    datos = PrediccionResponse(**r.json())
    assert datos.calidad.estado == "advertencia"
    assert [p.familia_id for p in datos.predicciones] == ["abarrotes", "lacteos", "panaderia"]

    (sol,) = _consulta(
        url,
        "SELECT id, tipo, estado, tienda_id, fecha, calidad_estado, version_modelo,"
        " claves_ignoradas, normalizado, cuerpo_crudo FROM solicitudes",
    )
    id_, tipo, estado, tienda, fecha, cal, version, ignoradas, normalizado, crudo = sol
    assert (tipo, estado, tienda, fecha, cal, version) == (
        "prediccion",
        "aceptada",
        1,
        date(2025, 1, 27),
        "advertencia",
        "3",
    )
    assert ignoradas == ["comentario"]
    assert normalizado["familias"][0] == {"familia_id": "abarrotes", "en_promocion": 2}
    assert json.loads(crudo)["tienda"] == "T01"

    preds = _consulta(
        url,
        "SELECT familia_id, en_promocion, promocion_explicita, venta_esperada,"
        " cantidad_sugerida, version_modelo FROM predicciones WHERE solicitud_id = %s"
        " ORDER BY familia_id",
        id_,
    )
    assert [(f, p, e) for f, p, e, *_ in preds] == [
        ("abarrotes", 2, True),
        ("lacteos", 1, True),
        ("panaderia", 0, False),
    ]
    por_familia = {p.familia_id: p for p in datos.predicciones}
    for f, _, _, venta, cantidad, version in preds:
        assert venta == por_familia[f].venta_esperada
        assert cantidad == por_familia[f].cantidad_sugerida and version == "3"

    acciones = {(c, a) for c, a in _consulta(url, "SELECT codigo, accion FROM correcciones")}
    assert {a for _, a in acciones} == {"reparar", "advertir"}  # las claves ignoradas van aparte
    assert ("FAMILIAS_FALTANTES_PRED", "advertir") in acciones


@pytest.mark.integracion
def test_varias_solicitudes_de_la_misma_fecha_quedan_todas(cliente, url):
    assert cliente.post("/v1/prediccion", json=_pred()).status_code == 200
    promo = [{"familia_id": f, "en_promocion": 5} for f in FAMILIAS]
    assert cliente.post("/v1/prediccion", json=_pred(familias=promo)).status_code == 200
    filas = _consulta(
        url,
        "SELECT s.id, count(p.*) FROM solicitudes s JOIN predicciones p ON p.solicitud_id = s.id"
        " GROUP BY s.id ORDER BY s.id",
    )
    assert [n for _, n in filas] == [3, 3] and filas[0][0] < filas[1][0]


@pytest.mark.integracion
def test_rechazos_se_registran_con_detail(cliente, url):
    r1 = cliente.post("/v1/prediccion", json=_pred(tienda_id=99))
    r2 = cliente.post("/v1/observaciones", content=b"{roto")
    assert r1.status_code == r2.status_code == 422
    filas = _consulta(
        url, "SELECT tipo, estado, codigo_rechazo, detail FROM solicitudes ORDER BY id"
    )
    assert [(t, e) for t, e, _, _ in filas] == [
        ("prediccion", "rechazada"),
        ("observaciones", "rechazada"),
    ]
    assert filas[0][3] == r1.json()["detail"] and filas[1][3] == r2.json()["detail"]
    assert _consulta(url, "SELECT count(*) FROM predicciones") == [(0,)]


@pytest.mark.integracion
def test_cuerpo_con_nul_se_registra_sin_romper_la_bd(cliente, url):
    r = cliente.post("/v1/prediccion", content=b'{"tienda_id": 1, "fecha": "\x00"}')
    assert r.status_code == 422
    (crudo,) = _consulta(url, "SELECT cuerpo_crudo FROM solicitudes")[0]
    assert "\x00" not in crudo


@pytest.mark.integracion
def test_observaciones_idempotentes_e_historial(cliente, url):
    primera = cliente.post("/v1/observaciones", json=_obs(D, {f: 100 for f in FAMILIAS}))
    assert primera.status_code == 200, primera.text
    assert ObservacionesResponse(**primera.json()).registros_guardados == 3

    # E09: reenvío idéntico → 0. D está dentro del histórico sintético: la única observación es el
    # aviso de que la observación reemplaza al dato publicado (fuera del histórico sería `ok`).
    repetida = cliente.post("/v1/observaciones", json=_obs(D, {f: 100 for f in FAMILIAS}))
    assert repetida.json()["registros_guardados"] == 0
    assert [o["campo"] for o in repetida.json()["calidad"]["observaciones"]] == ["fecha"]
    assert "histórico publicado" in repetida.json()["calidad"]["observaciones"][0]["detalle"]

    # Corrección de una familia: solo esa cuenta y queda el valor anterior en el historial.
    cambio = cliente.post("/v1/observaciones", json=_obs(D, {"abarrotes": 120}))
    assert cambio.json()["registros_guardados"] == 1
    assert cambio.json()["calidad"]["estado"] == "advertencia"  # faltan 2 familias (se avisa)

    assert _consulta(url, "SELECT ventas FROM observaciones WHERE familia_id = 'abarrotes'") == [
        (120,)
    ]
    historial = _consulta(
        url,
        "SELECT familia_id, ventas_anterior, ventas_nueva FROM observaciones_historial ORDER BY id",
    )
    assert len(historial) == 4 and historial[-1] == ("abarrotes", 100, 120)
    assert _consulta(url, "SELECT count(*) FROM solicitudes WHERE tipo = 'observaciones'") == [(3,)]


@pytest.mark.integracion
def test_observaciones_invalidas_no_se_guardan(cliente, url):
    r = cliente.post(
        "/v1/observaciones",
        json=_obs(D, {"abarrotes": -5, "lacteos": "50,5", "panaderia": "1,292"}),
    )
    assert r.status_code == 200
    datos = r.json()
    assert datos["registros_guardados"] == 1 and datos["calidad"]["estado"] == "advertencia"
    assert _consulta(url, "SELECT familia_id, ventas FROM observaciones") == [("panaderia", 1292)]


@pytest.mark.integracion
def test_observaciones_alimentan_la_prediccion_sin_fuga(cliente):
    base = cliente.post("/v1/prediccion", json=_pred()).json()

    # Valores altos pero plausibles (3× la predicción, bajo el límite de ventas atípicas).
    altas = {p["familia_id"]: int(p["venta_esperada"] * 3) + 1 for p in base["predicciones"]}
    # La venta del propio día D no cambia la predicción de D…
    cliente.post("/v1/observaciones", json=_obs(D, altas))
    assert cliente.post("/v1/prediccion", json=_pred()).json() == base

    # …pero una observación anterior (D-7, mismo día de la semana) sí, y gana al histórico.
    r = cliente.post("/v1/observaciones", json=_obs("2025-01-20", altas))
    assert r.json()["registros_guardados"] == len(altas)
    despues = cliente.post("/v1/prediccion", json=_pred()).json()
    for antes, ahora in zip(base["predicciones"], despues["predicciones"], strict=True):
        assert ahora["venta_esperada"] > antes["venta_esperada"]


@pytest.mark.integracion
def test_fecha_fuera_de_cobertura_advierte_y_antes_del_historico_rechaza(cliente):
    r = cliente.post("/v1/prediccion", json=_pred(fecha="2026-03-02"))
    assert r.status_code == 200 and r.json()["calidad"]["estado"] == "advertencia"
    assert cliente.post("/v1/prediccion", json=_pred(fecha="2020-01-01")).status_code == 422


@pytest.mark.integracion
def test_modelo_que_falla_es_503_y_no_guarda_predicciones(url):
    class Roto:
        def predict(self, df):
            raise ValueError("explota")

    from novamercado.servicio.cargador import ModeloCargado

    modelo = ModeloCargado(Roto(), "novamercado", "champion", "4", "mlflow")
    cli, _ = _cliente(modelo=modelo, conectar=lambda: psycopg.connect(url))
    with cli:
        r = cli.post("/v1/prediccion", json=_pred())
    assert r.status_code == 503 and "no pudo predecir" in r.json()["detail"]
    assert _consulta(url, "SELECT count(*) FROM predicciones") == [(0,)]
