"""Piezas puras de las pruebas de punta a punta (sin stack): datos sintéticos en CSV, corte del
histórico, validación del contrato y errores inyectados por el simulador."""

from __future__ import annotations

import contextlib
import json
import random
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ayudas_esqueleto import bd as _fixture_bd  # noqa: F401 — fixture
from ayudas_esqueleto import bd_url as _fixture_bd_url  # noqa: F401 — fixture
from novamercado.config import Config
from novamercado.db.carga import cargar_datos, huella, localizar_archivos
from novamercado.limpieza import SolicitudRechazada, procesar_observaciones, procesar_prediccion
from novamercado.sintetico import (
    APERTURA_TIENDA_NUEVA,
    FAMILIAS_SINTETICAS,
    TIENDA_NUEVA,
    catalogo_sintetico,
    escribir_datos,
    solicitud_observaciones,
    solicitud_prediccion,
)

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

import comun_e2e as e2e  # noqa: E402


@pytest.fixture(scope="module")
def datos_sinteticos(tmp_path_factory) -> Path:
    carpeta = tmp_path_factory.mktemp("sint")
    escribir_datos(carpeta, date(2025, 5, 1), date(2025, 7, 31))
    return carpeta


# --- datos sintéticos --------------------------------------------------------------------------


def test_datos_sinteticos_tienen_el_formato_publicado(datos_sinteticos):
    assert set(localizar_archivos(datos_sinteticos)) >= {"historico_ventas.csv", "tiendas.csv"}
    h = pd.read_csv(datos_sinteticos / "historico_ventas.csv")
    assert list(h.columns) == ["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"]
    assert set(h["familia_id"]) == set(FAMILIAS_SINTETICAS)
    assert (h["ventas"] >= 0).all() and h.duplicated(
        ["fecha", "tienda_id", "familia_id"]
    ).sum() == 0
    nueva = h[h["tienda_id"] == TIENDA_NUEVA]
    assert nueva["fecha"].min() == APERTURA_TIENDA_NUEVA.isoformat()
    f = pd.read_csv(datos_sinteticos / "feriados.csv")
    assert f["fecha"].max() == "2026-12-31"  # cobertura para las fechas del smoke


def test_datos_sinteticos_son_deterministas(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    escribir_datos(a, date(2025, 6, 1), date(2025, 6, 10))
    escribir_datos(b, date(2025, 6, 1), date(2025, 6, 10))
    assert (a / "historico_ventas.csv").read_bytes() == (b / "historico_ventas.csv").read_bytes()


# --- corte del histórico (HISTORICO_HASTA) -----------------------------------------------------


def test_config_lee_historico_hasta(monkeypatch):
    monkeypatch.setenv("HISTORICO_HASTA", "2025-10-31")
    assert Config.desde_entorno().historico_hasta == date(2025, 10, 31)
    monkeypatch.setenv("HISTORICO_HASTA", "")
    assert Config.desde_entorno().historico_hasta is None
    monkeypatch.setenv("HISTORICO_HASTA", "31/10/2025")
    with pytest.raises(ValueError, match="HISTORICO_HASTA"):
        Config.desde_entorno()


def test_el_corte_cambia_la_huella(datos_sinteticos):
    archivos = localizar_archivos(datos_sinteticos)
    assert huella(archivos) != huella(archivos, date(2025, 6, 30))
    assert huella(archivos, date(2025, 6, 30)) == huella(archivos, date(2025, 6, 30))


@pytest.mark.integracion
def test_carga_con_corte_solo_trae_hasta_la_fecha(bd, datos_sinteticos):
    res = cargar_datos(bd, datos_sinteticos, hasta=date(2025, 6, 30))
    assert res.cargado
    maximo = bd.execute("SELECT max(fecha) FROM historico_ventas").fetchone()[0]
    bd.commit()
    assert maximo == date(2025, 6, 30)


@pytest.mark.integracion
def test_corte_que_vacia_el_historico_falla(bd, datos_sinteticos):
    with pytest.raises(ValueError, match="vacío"):
        cargar_datos(bd, datos_sinteticos, hasta=date(2020, 1, 1))


# --- validación del contrato ------------------------------------------------------------------


def _pred_ok(**cambios) -> str:
    cuerpo = {
        "tienda_id": 3,
        "fecha": "2026-03-10",
        "version_modelo": "4",
        "predicciones": [
            {"familia_id": "abarrotes", "venta_esperada": 10.5, "cantidad_sugerida": 12}
        ],
        "calidad": {"estado": "ok", "observaciones": []},
    }
    cuerpo.update(cambios)
    return json.dumps(cuerpo)


def test_validar_respuesta_acepta_el_contrato():
    assert e2e.validar_respuesta("prediccion", 200, _pred_ok()) == []
    assert e2e.validar_respuesta("prediccion", 422, '{"detail": "tienda inexistente"}') == []
    assert e2e.validar_respuesta("health", 503, '{"estado":"degradado","motivo":"x"}') == []
    assert e2e.validar_respuesta("estado_reentreno", 200, '{"estado":"en_curso"}') == []


@pytest.mark.parametrize(
    ("endpoint", "codigo", "texto"),
    [
        ("prediccion", 500, '{"detail": "boom"}'),
        ("prediccion", 200, _pred_ok(version_modelo=4)),
        ("prediccion", 200, _pred_ok(calidad={"estado": "ok", "observaciones": [{"x": 1}]})),
        (
            "prediccion",
            200,
            _pred_ok(
                predicciones=[
                    {"familia_id": "abarrotes", "venta_esperada": 1.0, "cantidad_sugerida": 1.5}
                ]
            ),
        ),
        ("prediccion", 200, "no es json"),
        ("observaciones", 200, '{"registros_guardados": -1, "calidad": {"estado": "ok"}}'),
        ("estado_reentreno", 200, '{"estado": "exitoso"}'),
    ],
)
def test_validar_respuesta_detecta_violaciones(endpoint, codigo, texto):
    assert e2e.validar_respuesta(endpoint, codigo, texto)


# --- errores inyectados ------------------------------------------------------------------------


def _clave_pred(limpia) -> tuple:
    s = limpia.solicitud
    return (s.tienda_id, s.fecha, sorted((f.familia_id, f.en_promocion) for f in s.familias))


@pytest.mark.parametrize("nombre", sorted(e2e.EQUIVALENTES_PREDICCION))
@pytest.mark.parametrize("semilla", range(8))
def test_equivalentes_de_prediccion_limpian_igual(nombre, semilla):
    """Lo que el simulador llama "equivalente" debe dar la misma solicitud limpia."""
    cat = catalogo_sintetico()
    limpia = solicitud_prediccion(tienda_id=5, fecha="2026-03-17", semilla=semilla)
    sucia = e2e.EQUIVALENTES_PREDICCION[nombre](limpia, random.Random(semilla))
    a = procesar_prediccion(json.dumps(limpia), cat)
    b = procesar_prediccion(json.dumps(sucia), cat)
    assert _clave_pred(a) == _clave_pred(b)
    assert b.calidad.estado in e2e.estado_esperado(nombre)


@pytest.mark.parametrize("nombre", sorted(e2e.EQUIVALENTES_OBSERVACION))
@pytest.mark.parametrize("semilla", range(8))
def test_equivalentes_de_observacion_guardan_lo_mismo(nombre, semilla):
    cat = catalogo_sintetico()
    limpia = solicitud_observaciones(tienda_id=5, fecha="2026-03-17", semilla=semilla)
    sucia = e2e.EQUIVALENTES_OBSERVACION[nombre](limpia, random.Random(semilla))

    def clave(o):
        s = o.solicitud
        return (s.tienda_id, s.fecha, sorted((v.familia_id, v.ventas) for v in s.ventas))

    a = procesar_observaciones(json.dumps(limpia), cat)
    b = procesar_observaciones(json.dumps(sucia), cat)
    assert clave(a) == clave(b)


@pytest.mark.parametrize("nombre", sorted(e2e.RECHAZOS))
@pytest.mark.parametrize("semilla", range(6))
def test_rechazos_son_irreparables(nombre, semilla):
    limpia = solicitud_prediccion(tienda_id=5, fecha="2026-03-17")
    mala = e2e.RECHAZOS[nombre](limpia, random.Random(semilla))
    with pytest.raises(SolicitudRechazada):
        procesar_prediccion(json.dumps(mala), catalogo_sintetico())


@pytest.mark.parametrize("nombre", sorted(e2e.SUCIAS_PREDICCION))
@pytest.mark.parametrize("semilla", range(6))
def test_sucias_no_rompen_la_limpieza(nombre, semilla):
    limpia = solicitud_prediccion(tienda_id=5, fecha="2026-03-17")
    sucia = e2e.SUCIAS_PREDICCION[nombre](limpia, random.Random(semilla))
    with contextlib.suppress(SolicitudRechazada):
        procesar_prediccion(json.dumps(sucia), catalogo_sintetico())


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("tienda_id", v) for v in e2e.TIENDAS_INEXISTENTES]
    + [("fecha", v) for v in e2e.FECHAS_IMPOSIBLES],
)
def test_cada_valor_de_rechazo_da_422(campo, valor):
    mala = dict(solicitud_prediccion(tienda_id=5, fecha="2026-03-17"), **{campo: valor})
    with pytest.raises(SolicitudRechazada):
        procesar_prediccion(json.dumps(mala), catalogo_sintetico())


def test_compose_nunca_borra_volumenes():
    with pytest.raises(ValueError):
        e2e.Compose("x")("down", "-v")
