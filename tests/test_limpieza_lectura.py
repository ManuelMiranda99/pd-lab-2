"""Lectura tolerante del cuerpo crudo (antes de cualquier validación)."""

from __future__ import annotations

import json

import pytest

from novamercado.limpieza import LIMITE_BYTES, SolicitudRechazada, leer_cuerpo
from novamercado.limpieza.lectura import NoRepresentable, parejas


def test_objeto_simple():
    leido = leer_cuerpo(b'{"tienda_id": 5}')
    assert leido.datos == {"tienda_id": 5}
    assert leido.hallazgos == ()


def test_bom_utf8_se_ignora_sin_observacion():
    leido = leer_cuerpo(b"\xef\xbb\xbf" + b'{"fecha": "2025-11-17"}')
    assert leido.datos == {"fecha": "2025-11-17"}
    assert leido.hallazgos == ()


def test_utf16_y_latin1_se_reparan():
    leido = leer_cuerpo('{"familia_id": "LÁCTEOS"}'.encode("utf-16"))
    assert leido.datos["familia_id"] == "LÁCTEOS"
    assert [h.codigo for h in leido.hallazgos] == ["CODIFICACION"]
    leido = leer_cuerpo('{"familia_id": "LÁCTEOS"}'.encode("latin-1"))
    assert leido.datos["familia_id"] == "LÁCTEOS"
    assert [h.codigo for h in leido.hallazgos] == ["CODIFICACION"]


def test_claves_duplicadas_se_conservan_todas():
    leido = leer_cuerpo(b'{"tienda_id": 5, "tienda_id": 6}')
    assert leido.datos["tienda_id"] == 6
    assert parejas(leido.datos) == [("tienda_id", 5), ("tienda_id", 6)]


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400"])
def test_no_finitos_no_representables(literal):
    leido = leer_cuerpo(f'{{"v": {literal}}}')
    assert isinstance(leido.datos["v"], NoRepresentable)


def test_entero_gigante_no_rompe_el_parser():
    leido = leer_cuerpo('{"v": ' + "9" * 5000 + "}")
    assert isinstance(leido.datos["v"], NoRepresentable)


def test_json_doblemente_codificado():
    interno = json.dumps({"tienda_id": 5})
    leido = leer_cuerpo(json.dumps(interno))
    assert leido.datos == {"tienda_id": 5}
    assert [h.codigo for h in leido.hallazgos] == ["JSON_ANIDADO"]


@pytest.mark.parametrize(
    ("cuerpo", "codigo"),
    [
        (b"", "CUERPO_VACIO"),
        (b"   \n", "CUERPO_VACIO"),
        (b"\xef\xbb\xbf", "CUERPO_VACIO"),
        (b"{tienda_id: 5}", "JSON_INVALIDO"),
        (b'{"tienda_id": 5,}', "JSON_INVALIDO"),
        (b"tienda_id=5&fecha=2025-11-17", "JSON_INVALIDO"),
        (b"[" * 100_000 + b"]" * 100_000, "JSON_INVALIDO"),
        (b"[]", "RAIZ_NO_OBJETO"),
        (b'[{"tienda_id": 5}]', "RAIZ_NO_OBJETO"),
        (b"null", "RAIZ_NO_OBJETO"),
        (b"5", "RAIZ_NO_OBJETO"),
        (b'"hola"', "RAIZ_NO_OBJETO"),
        (b"\x00\x01\x02", "JSON_INVALIDO"),
        (b" " * (LIMITE_BYTES + 1), "CUERPO_GRANDE"),
    ],
)
def test_rechazos_de_lectura(cuerpo, codigo):
    with pytest.raises(SolicitudRechazada) as exc:
        leer_cuerpo(cuerpo)
    assert exc.value.codigo == codigo
    assert exc.value.detail
