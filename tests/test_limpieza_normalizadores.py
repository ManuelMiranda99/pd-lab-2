"""Normalizadores puros: números, tienda, fecha, familia y claves."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from novamercado.limpieza import SolicitudRechazada, cargar_catalogo
from novamercado.limpieza.lectura import NoRepresentable
from novamercado.limpieza.normalizadores import (
    ALIAS_PREDICCION,
    forma_canonica,
    interpretar_fecha,
    interpretar_numero,
    normalizar_familia,
    normalizar_fecha,
    normalizar_tienda,
    resolver_claves,
)
from novamercado.sintetico import catalogo_sintetico

CAT = catalogo_sintetico()

# --- números ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "estado", "esperado"),
    [
        (3, "ok", 3),
        ("3", "reparado", 3),
        (" 3 ", "reparado", 3),
        (2.0, "reparado", 2),
        ("1,234", "reparado", 1234),
        ("1,234,567", "reparado", 1234567),
        ("812,0", "reparado", 812),
        ("1.234.567", "reparado", 1234567),
        ("1 234", "reparado", 1234),
        ("1.234,0", "reparado", 1234),
        ("1,234.00", "reparado", 1234),
        ("1e3", "reparado", 1000),
        ("８", "reparado", 8),
        ("-4", "reparado", -4),
        (-4, "ok", -4),
        (2.5, "decimal", Decimal("2.5")),
        ("50,5", "decimal", Decimal("50.5")),
        ("1.234", "decimal", Decimal("1.234")),
    ],
)
def test_interpretar_numero_valido(valor, estado, esperado):
    n = interpretar_numero(valor)
    assert (n.estado, n.valor) == (estado, esperado)


@pytest.mark.parametrize(
    ("valor", "estado"),
    [
        (True, "invalido"),
        (False, "invalido"),
        (None, "faltante"),
        ("", "faltante"),
        ("abc", "invalido"),
        ("NaN", "invalido"),
        ("Infinity", "invalido"),
        (float("nan"), "invalido"),
        (float("inf"), "invalido"),
        (NoRepresentable("NaN"), "invalido"),
        (10**40, "invalido"),
        (2**31, "invalido"),
        ("99999999999999999999", "invalido"),
        ([1], "invalido"),
        ({"v": 1}, "invalido"),
        ("3 unidades", "invalido"),
        ("1,2,3", "invalido"),
    ],
)
def test_interpretar_numero_invalido(valor, estado):
    assert interpretar_numero(valor).estado == estado


# --- tienda ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "valor",
    ["T05", "5", "05", 5.0, "5.0", "Tienda 5", "tienda-5", "T-05", " t5 ", "Tienda Sintética 5"],
)
def test_tienda_reparable(valor):
    tienda, hallazgos = normalizar_tienda(valor, CAT)
    assert tienda == 5
    assert [h.codigo for h in hallazgos] == ["TIENDA_FORMATO"]


def test_tienda_entera_sin_hallazgos():
    assert normalizar_tienda(5, CAT) == (5, [])


@pytest.mark.parametrize(
    ("valor", "codigo"),
    [
        (None, "TIENDA_FALTANTE"),
        ("  ", "TIENDA_FALTANTE"),
        (True, "TIENDA_ILEGIBLE"),
        ([5], "TIENDA_ILEGIBLE"),
        (5.5, "TIENDA_ILEGIBLE"),
        ("cinco", "TIENDA_ILEGIBLE"),
        (NoRepresentable("NaN"), "TIENDA_ILEGIBLE"),
        (0, "TIENDA_INEXISTENTE"),
        (-5, "TIENDA_INEXISTENTE"),
        ("-5", "TIENDA_INEXISTENTE"),
        (99, "TIENDA_INEXISTENTE"),
        (10**12, "TIENDA_ILEGIBLE"),
    ],
)
def test_tienda_rechazada(valor, codigo):
    with pytest.raises(SolicitudRechazada) as exc:
        normalizar_tienda(valor, CAT)
    assert exc.value.codigo == codigo


# --- fecha ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "fecha", "tipo"),
    [
        ("2025-11-17", date(2025, 11, 17), "ok"),
        ("2025-11-17T00:00:00", date(2025, 11, 17), "reparado"),
        ("2025-11-17 08:30", date(2025, 11, 17), "reparado"),
        ("2025-11-17T00:00:00.000Z", date(2025, 11, 17), "reparado"),
        ("2025-11-17T00:00:00-06:00", date(2025, 11, 17), "reparado"),
        ("2025/11/17", date(2025, 11, 17), "reparado"),
        ("2025.11.17", date(2025, 11, 17), "reparado"),
        ("2025-1-7", date(2025, 1, 7), "reparado"),
        ("20251117", date(2025, 11, 17), "reparado"),
        (20251117, date(2025, 11, 17), "reparado"),
        ("17/11/2025", date(2025, 11, 17), "reparado"),
        ("17-11-2025", date(2025, 11, 17), "reparado"),
        ("11/17/2025", date(2025, 11, 17), "reparado"),
        ("05/05/2026", date(2026, 5, 5), "reparado"),
        ("05/06/2026", date(2026, 6, 5), "ambigua"),
        (" 2025-11-17 ", date(2025, 11, 17), "reparado"),
    ],
)
def test_interpretar_fecha(valor, fecha, tipo):
    assert interpretar_fecha(valor) == (fecha, tipo)


@pytest.mark.parametrize(
    "valor", ["2025-02-30", "31/02/2026", "13/13/2026", "17/11/25", "ayer", "", True, 3.5, [2025]]
)
def test_fecha_ilegible(valor):
    with pytest.raises(ValueError):
        interpretar_fecha(valor)


def test_fecha_rango_derivado_de_datos():
    with pytest.raises(SolicitudRechazada) as exc:
        normalizar_fecha("2024-02-29", CAT, 1)
    assert exc.value.codigo == "FECHA_ANTES_HISTORICO"
    assert normalizar_fecha("2024-03-01", CAT, 1)[1] == []
    _, hallazgos = normalizar_fecha("2027-01-04", CAT, 1)
    assert [h.codigo for h in hallazgos] == ["FECHA_FUERA_COBERTURA"]


def test_fecha_pasada_dentro_del_historico_no_se_rechaza():
    assert normalizar_fecha("2025-11-17", CAT, 5) == (date(2025, 11, 17), [])


def test_fecha_antes_de_apertura():
    with pytest.raises(SolicitudRechazada) as exc:
        normalizar_fecha("2025-05-31", CAT, 10)
    assert exc.value.codigo == "FECHA_ANTES_APERTURA"
    assert normalizar_fecha("2025-06-01", CAT, 10)[0] == date(2025, 6, 1)


# --- familia ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("valor", "familia", "tipo"),
    [
        ("lacteos", "lacteos", "ok"),
        ("LÁCTEOS", "lacteos", "reparado"),
        ("lacteos ", "lacteos", "reparado"),
        ("Frutas y Verduras", "frutas_verduras", "reparado"),
        ("frutas & verduras", "frutas_verduras", "reparado"),
        ("FRUTAS-VERDURAS", "frutas_verduras", "reparado"),
        ("frutasverduras", "frutas_verduras", "reparado"),
        ("Cuidado Personal", "cuidado_personal", "reparado"),
        ("Snacks y dulces", "snacks", "reparado"),
        ("Bebé", "bebe", "reparado"),
        ("lacteo", "lacteos", "aproximada"),
        ("mascota", "mascotas", "aproximada"),
        ("panaderias", "panaderia", "aproximada"),
        ("juguetes", None, "desconocida"),
        ("", None, "desconocida"),
        (None, None, "desconocida"),
        (7, None, "desconocida"),
        ("frutas", None, "desconocida"),
    ],
)
def test_normalizar_familia(valor, familia, tipo):
    assert normalizar_familia(valor, CAT) == (familia, tipo)


# --- claves -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("clave", "forma"),
    [
        ("tiendaId", "tienda_id"),
        ("TIENDA_ID", "tienda_id"),
        (" Tienda ID ", "tienda_id"),
        ("Fecha", "fecha"),
        ("en-promoción", "en_promocion"),
    ],
)
def test_forma_canonica(clave, forma):
    assert forma_canonica(clave) == forma


def test_resolver_claves_alias_y_desconocidas():
    res = resolver_claves(
        [("tienda", 5), ("FECHA", "2025-11-17"), ("familias", []), ("canal", "web")],
        ALIAS_PREDICCION,
    )
    assert set(res.candidatos) == {"tienda_id", "fecha", "familias"}
    assert res.ignoradas == ("canal",)
    assert [h.campo for h in res.hallazgos] == ["tienda", "FECHA"]
    assert {h.codigo for h in res.hallazgos} == {"CLAVE_RENOMBRADA"}


def test_resolver_claves_conserva_duplicados():
    res = resolver_claves([("tienda_id", 5), ("tienda", 6)], ALIAS_PREDICCION)
    assert [c.valor for c in res.candidatos["tienda_id"]] == [5, 6]


# --- catálogo -----------------------------------------------------------------------------------


def test_cargar_catalogo_deriva_fechas_de_los_archivos(tmp_path):
    (tmp_path / "tiendas.csv").write_text(
        "tienda_id,nombre,ciudad,departamento,tipo,fecha_apertura\n"
        "1,Uno,X,X,express,2020-01-01\n2,Dos,Y,Y,express,2024-06-01\n",
        encoding="utf-8",
    )
    (tmp_path / "familias.csv").write_text(
        "familia_id,nombre\nabarrotes,Abarrotes\nlacteos,Lácteos\n", encoding="utf-8"
    )
    (tmp_path / "feriados.csv").write_text(
        "fecha,nombre,tipo,ambito\n2024-05-01,A,feriado,nacional\n2027-03-01,B,feriado,nacional\n",
        encoding="utf-8",
    )
    (tmp_path / "historico_ventas.csv").write_text(
        "fecha,tienda_id,familia_id,ventas,en_promocion\n"
        "2024-02-10,1,abarrotes,5,0\n2024-01-15,1,lacteos,3,0\n",
        encoding="utf-8",
    )
    cat = cargar_catalogo(tmp_path)
    assert (cat.fecha_inicio, cat.fecha_fin_cobertura) == (date(2024, 1, 15), date(2027, 3, 1))
    assert cat.tiendas[2].fecha_apertura == date(2024, 6, 1)
    assert list(cat.familias) == ["abarrotes", "lacteos"]
    (tmp_path / "historico_ventas.csv").unlink()
    assert cargar_catalogo(tmp_path).fecha_inicio == date(2024, 5, 1)
