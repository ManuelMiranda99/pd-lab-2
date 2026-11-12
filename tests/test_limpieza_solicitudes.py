"""Limpieza de solicitudes completas: catálogo E01–E10 (sintético) y casos no catalogados.

Todas las pruebas usan el catálogo sintético y solicitudes propias; nada depende de ``data/``,
de la BD ni de MLflow.
"""

from __future__ import annotations

import copy
import json
import random
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from novamercado.contrato import PrediccionResponse, sanear_prediccion
from novamercado.limpieza import (
    SolicitudRechazada,
    limpiar_observaciones,
    limpiar_prediccion,
    procesar_observaciones,
    procesar_prediccion,
)
from novamercado.sintetico import (
    FAMILIAS_SINTETICAS,
    catalogo_sintetico,
    solicitud_observaciones,
    solicitud_prediccion,
)

CAT = catalogo_sintetico()
FIXTURES = Path(__file__).parent / "fixtures" / "solicitudes_propias.json"


def pred(**cambios: Any) -> dict[str, Any]:
    s = solicitud_prediccion()
    s.update(cambios)
    return s


def obs(**cambios: Any) -> dict[str, Any]:
    s = solicitud_observaciones()
    s.update(cambios)
    return s


def codigos(resultado) -> list[str]:
    return [h.codigo for h in resultado.hallazgos]


def rechazo(funcion, datos) -> str:
    with pytest.raises(SolicitudRechazada) as exc:
        funcion(datos, CAT)
    assert exc.value.detail
    return exc.value.codigo


def promo(resultado, familia: str) -> int:
    return {f.familia_id: f.en_promocion for f in resultado.solicitud.familias}[familia]


# =================================================================================================
# Catálogo de errores E01–E10 (con datos propios)
# =================================================================================================


def test_valida_es_ok():
    r = limpiar_prediccion(pred(), CAT)
    assert r.calidad.estado == "ok" and r.calidad.observaciones == []
    assert [f.familia_id for f in r.solicitud.familias] == list(FAMILIAS_SINTETICAS)


@pytest.mark.parametrize("tienda", ["T03", "3", 3.0, "Tienda 3"])
def test_e01_tienda_como_texto(tienda):
    r = limpiar_prediccion(pred(tienda_id=tienda), CAT)
    assert r.solicitud.tienda_id == 3
    assert r.calidad.estado == "corregido"
    assert r.calidad.observaciones[0].campo == "tienda_id"


def test_e02_familia_otra_forma():
    s = pred()
    s["familias"][2]["familia_id"] = "LÁCTEOS"
    s["familias"][5]["familia_id"] = "Frutas y Verduras"
    s["familias"][0]["familia_id"] = "abarrotes "
    r = limpiar_prediccion(s, CAT)
    assert r.calidad.estado == "corregido"
    obs_ = {o.campo: o.detalle for o in r.calidad.observaciones}
    assert obs_["familias[2].familia_id"] == "'LÁCTEOS' se interpretó como 'lacteos'"
    assert "frutas_verduras" in obs_["familias[5].familia_id"]
    assert "familias[0].familia_id" in obs_


def test_e03_promocion_texto_o_decimal_entero():
    s = pred()
    s["familias"][0]["en_promocion"] = "3"
    s["familias"][1]["en_promocion"] = 2.0
    r = limpiar_prediccion(s, CAT)
    assert (promo(r, "abarrotes"), promo(r, "bebidas")) == (3, 2)
    assert r.calidad.estado == "corregido"
    assert codigos(r) == ["NUMERO_FORMATO", "NUMERO_FORMATO"]


@pytest.mark.parametrize(
    "fecha", ["2026-03-10T00:00:00", "2026-03-10 07:15", "20260310", "2026/03/10"]
)
def test_e04_fecha_otro_formato(fecha):
    r = limpiar_prediccion(pred(fecha=fecha), CAT)
    assert r.solicitud.fecha == date(2026, 3, 10)
    assert r.calidad.estado == "corregido"


def test_e04_fecha_ambigua_se_advierte_dd_mm():
    r = limpiar_prediccion(pred(fecha="05/06/2026"), CAT)
    assert r.solicitud.fecha == date(2026, 6, 5)
    assert r.calidad.estado == "advertencia"
    assert codigos(r) == ["FECHA_AMBIGUA"]


def test_e05_faltan_familias_se_predicen_todas_con_promo_0():
    s = pred()
    s["familias"] = [f for f in s["familias"] if f["familia_id"] not in ("bebe", "mascotas")]
    r = limpiar_prediccion(s, CAT)
    assert len(r.solicitud.familias) == 12
    assert r.familias_completadas == ("bebe", "mascotas")
    assert promo(r, "bebe") == promo(r, "mascotas") == 0
    assert r.calidad.estado == "advertencia"
    assert r.calidad.observaciones[0].campo == "familias"


def test_e06_familia_repetida_distinta_usa_la_ultima():
    s = pred()
    s["familias"][0]["en_promocion"] = 0
    s["familias"].append({"familia_id": "abarrotes", "en_promocion": 3})
    r = limpiar_prediccion(s, CAT)
    assert promo(r, "abarrotes") == 3
    assert len(r.solicitud.familias) == 12
    assert r.calidad.estado == "advertencia"
    assert codigos(r) == ["FAMILIA_REPETIDA_PRED"]
    assert r.calidad.observaciones[0].campo == "familias[12]"


def test_e07_campo_renombrado():
    s = pred()
    s["tienda"] = s.pop("tienda_id")
    r = limpiar_prediccion(s, CAT)
    assert r.solicitud.tienda_id == 3
    assert r.calidad.estado == "corregido"
    assert r.calidad.observaciones[0].campo == "tienda"


def test_e08_ventas_negativas_no_se_guardan():
    s = obs()
    s["ventas"][4]["ventas"] = -4
    r = limpiar_observaciones(s, CAT)
    assert len(r.solicitud.ventas) == 11
    assert r.familias_descartadas == (s["ventas"][4]["familia_id"],)
    assert r.calidad.estado == "advertencia"
    assert r.calidad.observaciones[0].campo == "ventas[4].ventas"


def test_e09_reenvio_identico_es_determinista_y_ok():
    s = obs()
    a, b = limpiar_observaciones(s, CAT), limpiar_observaciones(copy.deepcopy(s), CAT)
    assert a.solicitud == b.solicitud
    assert a.calidad.estado == b.calidad.estado == "ok"


def test_e10_numero_con_formato():
    s = obs()
    s["ventas"][0]["ventas"] = "1,292"
    s["ventas"][1]["ventas"] = "50,0"
    s["ventas"][2]["ventas"] = "50,5"
    r = limpiar_observaciones(s, CAT)
    guardadas = {v.familia_id: v.ventas for v in r.solicitud.ventas}
    assert guardadas["abarrotes"] == 1292 and guardadas["bebidas"] == 50
    assert "lacteos" not in guardadas
    assert codigos(r) == ["NUMERO_FORMATO", "NUMERO_FORMATO", "VENTAS_DECIMAL"]
    assert r.calidad.estado == "advertencia"


# =================================================================================================
# Casos no catalogados (reglas generales R1–R9)
# =================================================================================================


def test_nc01_claves_camelcase_y_mayusculas():
    s = {
        "TiendaId": 3,
        "FECHA": "2026-03-10",
        "Familias": [{"FamiliaID": "abarrotes", "EnPromocion": 1}],
    }
    r = limpiar_prediccion(s, CAT)
    assert r.solicitud.tienda_id == 3 and promo(r, "abarrotes") == 1
    assert codigos(r).count("CLAVE_RENOMBRADA") == 5


def test_nc02_alias_de_items():
    s = pred(familias=[{"familia": "bebidas", "promo": 2}])
    r = limpiar_prediccion(s, CAT)
    assert promo(r, "bebidas") == 2


def test_nc03_campos_extra_se_ignoran_sin_observacion():
    s = pred(canal="web", version_pos="1.2")
    s["familias"][0]["comentario"] = "x"
    r = limpiar_prediccion(s, CAT)
    assert r.calidad.estado == "ok"
    assert set(r.claves_ignoradas) == {"canal", "version_pos"}


def test_nc04_clave_identidad_repetida_igual_se_repara():
    r = procesar_prediccion(
        '{"tienda_id": 3, "tienda_id": "T03", "fecha": "2026-03-10", '
        '"familias": [{"familia_id": "abarrotes", "en_promocion": 0}]}',
        CAT,
    )
    assert r.solicitud.tienda_id == 3
    assert "CLAVE_DUPLICADA_IGUAL" in codigos(r)


def test_nc05_clave_identidad_en_conflicto_se_rechaza():
    with pytest.raises(SolicitudRechazada) as exc:
        procesar_prediccion(
            '{"tienda_id": 3, "tienda_id": 4, "fecha": "2026-03-10", '
            '"familias": [{"familia_id": "abarrotes", "en_promocion": 0}]}',
            CAT,
        )
    assert exc.value.codigo == "CLAVE_DUPLICADA_IDENTIDAD"


def test_nc06_canonica_y_alias_en_conflicto_se_rechaza():
    assert rechazo(limpiar_prediccion, pred(tienda=4)) == "CLAVE_DUPLICADA_IDENTIDAD"


def test_nc07_clave_de_item_duplicada_en_conflicto_usa_la_ultima():
    r = procesar_prediccion(
        '{"tienda_id": 3, "fecha": "2026-03-10", "familias": '
        '[{"familia_id": "abarrotes", "en_promocion": 1, "en_promocion": 4}]}',
        CAT,
    )
    assert promo(r, "abarrotes") == 4
    assert "CLAVE_DUPLICADA_ITEM" in codigos(r)


def test_nc08_clave_de_item_duplicada_en_observacion_no_se_guarda():
    r = procesar_observaciones(
        '{"tienda_id": 3, "fecha": "2026-03-10", "ventas": '
        '[{"familia_id": "abarrotes", "ventas": 1, "ventas": 4},'
        ' {"familia_id": "bebidas", "ventas": 9}]}',
        CAT,
    )
    assert [v.familia_id for v in r.solicitud.ventas] == ["bebidas"]
    assert "CLAVE_DUPLICADA_ITEM" in codigos(r)


@pytest.mark.parametrize("valor", [True, False, [1], {"n": 1}, "muchas"])
def test_nc09_promocion_invalida_usa_0_y_advierte(valor):
    s = pred()
    s["familias"][0]["en_promocion"] = valor
    r = limpiar_prediccion(s, CAT)
    assert promo(r, "abarrotes") == 0
    assert codigos(r) == ["PROMOCION_INVALIDA"]


def test_nc10_promocion_nan_infinity_y_gigante():
    r = procesar_prediccion(
        '{"tienda_id": 3, "fecha": "2026-03-10", "familias": ['
        '{"familia_id": "abarrotes", "en_promocion": NaN},'
        '{"familia_id": "bebidas", "en_promocion": Infinity},'
        '{"familia_id": "lacteos", "en_promocion": 99999999999999999999999}]}',
        CAT,
    )
    assert {promo(r, f) for f in ("abarrotes", "bebidas", "lacteos")} == {0}
    assert codigos(r).count("PROMOCION_INVALIDA") == 3


def test_nc11_promocion_negativa_usa_0():
    s = pred()
    s["familias"][0]["en_promocion"] = -2
    r = limpiar_prediccion(s, CAT)
    assert promo(r, "abarrotes") == 0 and r.calidad.estado == "advertencia"


@pytest.mark.parametrize(("valor", "esperado"), [(2.5, 3), (2.4, 2), ("1,5", 2), (0.5, 1)])
def test_nc12_promocion_decimal_se_redondea_y_advierte(valor, esperado):
    s = pred()
    s["familias"][0]["en_promocion"] = valor
    r = limpiar_prediccion(s, CAT)
    assert promo(r, "abarrotes") == esperado
    assert codigos(r) == ["PROMOCION_DECIMAL"]


def test_nc13_promocion_ausente_o_nula():
    s = pred()
    del s["familias"][0]["en_promocion"]
    s["familias"][1]["en_promocion"] = None
    r = limpiar_prediccion(s, CAT)
    assert codigos(r) == ["PROMOCION_FALTANTE", "PROMOCION_FALTANTE"]
    assert r.calidad.observaciones[0].campo == "familias[0].en_promocion"


def test_nc14_familia_aproximada_se_advierte():
    s = pred()
    s["familias"][2]["familia_id"] = "lacteo"
    r = limpiar_prediccion(s, CAT)
    assert promo(r, "lacteos") == s["familias"][2]["en_promocion"]
    assert codigos(r) == ["FAMILIA_APROXIMADA"]


def test_nc15_familia_desconocida_se_descarta_y_se_completa():
    s = pred()
    s["familias"][0]["familia_id"] = "juguetes"
    r = limpiar_prediccion(s, CAT)
    assert codigos(r) == ["FAMILIA_DESCONOCIDA", "FAMILIAS_FALTANTES_PRED"]
    assert r.familias_completadas == ("abarrotes",)


@pytest.mark.parametrize("item", ["abarrotes", None, 5, ["abarrotes", 1]])
def test_nc16_item_que_no_es_objeto(item):
    s = pred()
    s["familias"].append(item)
    r = limpiar_prediccion(s, CAT)
    assert codigos(r) == ["ITEM_INVALIDO"]
    assert r.calidad.observaciones[0].campo == "familias[12]"


def test_nc17_item_sin_familia():
    s = pred()
    s["familias"].append({"en_promocion": 3})
    r = limpiar_prediccion(s, CAT)
    assert codigos(r) == ["FAMILIA_DESCONOCIDA"]


def test_nc18_familia_repetida_igual_se_repara():
    s = pred()
    s["familias"].append(
        {"familia_id": "ABARROTES", "en_promocion": str(s["familias"][0]["en_promocion"])}
    )
    r = limpiar_prediccion(s, CAT)
    assert "FAMILIA_REPETIDA_IGUAL" in codigos(r)
    assert r.calidad.estado == "corregido"


@pytest.mark.parametrize(
    ("familias", "codigo"),
    [
        ([], "SIN_FAMILIAS"),
        (None, "SIN_FAMILIAS"),
        ("abarrotes", "SIN_FAMILIAS"),
        ([{"familia_id": "juguetes", "en_promocion": 1}], "SIN_FAMILIAS"),
        ({}, "SIN_FAMILIAS"),
    ],
)
def test_nc19_sin_familias_validas_se_rechaza(familias, codigo):
    assert rechazo(limpiar_prediccion, pred(familias=familias)) == codigo


def test_nc20_lista_ausente_se_rechaza():
    s = pred()
    del s["familias"]
    assert rechazo(limpiar_prediccion, s) == "SIN_FAMILIAS"


def test_nc21_familias_como_objeto():
    r = limpiar_prediccion(pred(familias={"abarrotes": 2, "bebidas": "1"}), CAT)
    assert (promo(r, "abarrotes"), promo(r, "bebidas")) == (2, 1)
    assert codigos(r)[:2] == ["LISTA_COMO_OBJETO", "NUMERO_FORMATO"]


@pytest.mark.parametrize(
    ("cambios", "codigo"),
    [
        ({"tienda_id": None}, "TIENDA_FALTANTE"),
        ({"tienda_id": True}, "TIENDA_ILEGIBLE"),
        ({"tienda_id": 3.5}, "TIENDA_ILEGIBLE"),
        ({"tienda_id": 77}, "TIENDA_INEXISTENTE"),
        ({"fecha": None}, "FECHA_FALTANTE"),
        ({"fecha": "2026-02-30"}, "FECHA_ILEGIBLE"),
        ({"fecha": "mañana"}, "FECHA_ILEGIBLE"),
        ({"fecha": "10/03/26"}, "FECHA_ILEGIBLE"),
        ({"fecha": "2023-12-31"}, "FECHA_ANTES_HISTORICO"),
        ({"tienda_id": 10, "fecha": "2025-01-15"}, "FECHA_ANTES_APERTURA"),
    ],
)
def test_nc22_identidad_irreparable_se_rechaza(cambios, codigo):
    assert rechazo(limpiar_prediccion, pred(**cambios)) == codigo


def test_nc23_falta_tienda_o_fecha():
    s = pred()
    del s["tienda_id"]
    assert rechazo(limpiar_prediccion, s) == "TIENDA_FALTANTE"
    s = pred()
    del s["fecha"]
    assert rechazo(limpiar_prediccion, s) == "FECHA_FALTANTE"


def test_nc24_fecha_posterior_a_feriados_se_advierte():
    r = limpiar_prediccion(pred(fecha="2027-01-05"), CAT)
    assert codigos(r) == ["FECHA_FUERA_COBERTURA"]
    assert r.calidad.estado == "advertencia"


def test_nc25_fecha_pasada_del_historico_es_valida():
    r = limpiar_prediccion(pred(fecha="2024-07-01"), CAT)
    assert r.calidad.estado == "ok"


def test_nc26_raiz_no_objeto():
    assert rechazo(limpiar_prediccion, [pred()]) == "RAIZ_NO_OBJETO"


def test_nc27_observacion_duplicada_igual_se_repara():
    s = obs()
    s["ventas"].append(dict(s["ventas"][0]))
    r = limpiar_observaciones(s, CAT)
    assert len(r.solicitud.ventas) == 12
    assert codigos(r) == ["FAMILIA_REPETIDA_IGUAL"]
    assert r.calidad.estado == "corregido"


def test_nc28_observacion_duplicada_distinta_no_se_guarda():
    s = obs()
    s["ventas"].append({"familia_id": s["ventas"][0]["familia_id"], "ventas": 1})
    r = limpiar_observaciones(s, CAT)
    assert len(r.solicitud.ventas) == 11
    assert codigos(r) == ["FAMILIA_REPETIDA_OBS"]


def test_nc29_observacion_faltan_familias_se_guardan_las_recibidas():
    s = obs()
    s["ventas"] = s["ventas"][:5]
    r = limpiar_observaciones(s, CAT)
    assert len(r.solicitud.ventas) == 5
    assert len(r.familias_faltantes) == 7
    assert codigos(r) == ["FAMILIAS_FALTANTES_OBS"]


@pytest.mark.parametrize("valor", [None, True, "muchas", [3], 2.5, "NaN"])
def test_nc30_ventas_invalidas_no_se_guardan(valor):
    s = obs()
    s["ventas"][0]["ventas"] = valor
    r = limpiar_observaciones(s, CAT)
    assert len(r.solicitud.ventas) == 11
    assert r.calidad.estado == "advertencia"


def test_nc31_observacion_con_alias_de_lista_y_de_item():
    s = {
        "tienda": 3,
        "fecha": "2026-03-10",
        "observaciones": [{"familia": "Abarrotes", "unidades": "1.234.567"}],
    }
    r = limpiar_observaciones(s, CAT)
    assert r.solicitud.ventas[0].ventas == 1234567


def test_nc32_observacion_ventas_cero_es_valida():
    s = obs()
    s["ventas"][0]["ventas"] = 0
    assert limpiar_observaciones(s, CAT).calidad.estado == "ok"


def test_nc33_cuerpo_crudo_con_bom_y_formato():
    cuerpo = "﻿" + json.dumps(pred(tienda_id="T03"))
    r = procesar_prediccion(cuerpo.encode("utf-8"), CAT)
    assert r.solicitud.tienda_id == 3 and r.calidad.estado == "corregido"


def test_nc34_observaciones_con_todas_invalidas_responde_sin_guardar():
    s = obs()
    for v in s["ventas"]:
        v["ventas"] = -1
    r = limpiar_observaciones(s, CAT)
    assert r.solicitud.ventas == [] and r.calidad.estado == "advertencia"


# =================================================================================================
# Propiedades del contrato sobre la salida de la limpieza
# =================================================================================================

ENTRADAS_SUCIAS = [
    pred(),
    pred(tienda_id="T03", fecha="10/03/2026"),
    pred(fecha="05/06/2026", familias=[{"familia_id": "lacteo", "en_promocion": "2,5"}]),
    pred(familias=[{"familia_id": "abarrotes", "en_promocion": True}, "x", None]),
]


@pytest.mark.parametrize("entrada", ENTRADAS_SUCIAS)
def test_propiedades_de_la_respuesta(entrada):
    r = limpiar_prediccion(entrada, CAT)
    respuesta = PrediccionResponse(
        tienda_id=r.solicitud.tienda_id,
        fecha=r.solicitud.fecha,
        version_modelo=7,
        predicciones=[sanear_prediccion(f.familia_id, 10.3, 11.2) for f in r.solicitud.familias],
        calidad=r.calidad,
    )
    datos = json.loads(respuesta.model_dump_json())
    assert isinstance(datos["version_modelo"], str)
    assert len(datos["predicciones"]) == 12
    for p in datos["predicciones"]:
        assert isinstance(p["cantidad_sugerida"], int) and p["cantidad_sugerida"] >= 0
    acciones = {o["accion"] for o in datos["calidad"]["observaciones"]}
    assert acciones <= {"reparar", "advertir"}
    if datos["calidad"]["estado"] == "ok":
        assert datos["calidad"]["observaciones"] == []
    assert ("advertir" in acciones) == (datos["calidad"]["estado"] == "advertencia")
    for f in r.solicitud.familias:
        assert isinstance(f.en_promocion, int) and not isinstance(f.en_promocion, bool)
        assert f.en_promocion >= 0


@pytest.mark.parametrize(
    "cuerpo",
    [
        b"",
        b"{",
        b"[]",
        b"null",
        b'{"tienda_id": {}}',
        b'{"tienda_id": 3, "fecha": []}',
        b'{"tienda_id": 3, "fecha": "2026-03-10", "familias": [[], {}, 1]}',
        b"\xff\xfe\x00",
        b'{"tienda_id": 1e999, "fecha": 1}',
        b'{"tienda_id": 3, "fecha": 20261399}',
    ],
)
def test_ninguna_entrada_lanza_otra_excepcion(cuerpo):
    """Cualquier cuerpo termina en resultado o en ``SolicitudRechazada`` (422), nunca en 500."""
    for funcion in (procesar_prediccion, procesar_observaciones):
        try:
            funcion(cuerpo, CAT)
        except SolicitudRechazada as exc:
            assert exc.detail


# =================================================================================================
# Fixtures propias
# =================================================================================================


def _casos() -> list[dict[str, Any]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.mark.parametrize("caso", _casos(), ids=lambda c: c["nombre"])
def test_fixtures_propias(caso):
    funcion = procesar_prediccion if caso["endpoint"] == "prediccion" else procesar_observaciones
    esperado = caso["esperado"]
    cuerpo = json.dumps(caso["cuerpo"], ensure_ascii=False).encode("utf-8")
    if esperado["codigo"] == 422:
        with pytest.raises(SolicitudRechazada) as exc:
            funcion(cuerpo, CAT)
        assert exc.value.codigo == esperado["politica"]
        return
    r = funcion(cuerpo, CAT)
    assert r.calidad.estado == esperado["estado"]
    if "guardables" in esperado:
        assert len(r.solicitud.ventas) == esperado["guardables"]


# =================================================================================================
# Mutaciones aleatorias deterministas: nada termina en otra excepción (500)
# =================================================================================================

_BASURA: list[Any] = [None, True, -1, 0, 2.5, 1e308, "", "x", "1,5", "T05", [], {}, [1], {"a": 1}]


def _mutar(valor: Any, rng: random.Random) -> Any:
    if isinstance(valor, dict) and valor and rng.random() < 0.7:
        clave = rng.choice(list(valor))
        accion = rng.random()
        if accion < 0.3:
            del valor[clave]
        elif accion < 0.6:
            valor[clave] = rng.choice(_BASURA)
        else:
            _mutar(valor[clave], rng)
    elif isinstance(valor, list) and valor:
        i = rng.randrange(len(valor))
        if rng.random() < 0.5:
            valor[i] = rng.choice(_BASURA)
        else:
            _mutar(valor[i], rng)
    return valor


@pytest.mark.parametrize("semilla", range(300))
def test_fuzz_mutaciones_terminan_en_resultado_o_422(semilla):
    rng = random.Random(semilla)
    for base, funcion in ((pred(), limpiar_prediccion), (obs(), limpiar_observaciones)):
        datos = base
        for _ in range(rng.randint(1, 4)):
            datos = _mutar(datos, rng)
        try:
            r = funcion(datos, CAT)
        except SolicitudRechazada as exc:
            assert exc.detail
            continue
        assert r.calidad.estado in ("ok", "corregido", "advertencia")
