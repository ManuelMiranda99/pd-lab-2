"""Pruebas extra sobre los datos publicados (``$DATOS_DIR``, por defecto ``./data``).

Se omiten si la carpeta no existe (p. ej. en la CI): los datos nunca se versionan.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from novamercado.limpieza import (
    SolicitudRechazada,
    cargar_catalogo,
    procesar_observaciones,
    procesar_prediccion,
)

DATOS = Path(os.environ.get("DATOS_DIR", "./data"))
EJEMPLOS = DATOS / "ejemplos"

pytestmark = pytest.mark.skipif(
    not (EJEMPLOS / "errores").is_dir() or not (DATOS / "tiendas.csv").is_file(),
    reason=f"no hay datos publicados en {DATOS}",
)

#: archivo → (endpoint, estado esperado, códigos de hallazgo esperados)
ESPERADO = {
    "prediccion_valida.json": ("prediccion", "ok", []),
    "observacion_valida.json": ("observaciones", "ok", []),
    "E01_tienda_texto.json": ("prediccion", "corregido", ["TIENDA_FORMATO"]),
    "E02_familia_otra_forma.json": ("prediccion", "corregido", ["FAMILIA_FORMATO"] * 2),
    "E03_promocion_texto.json": ("prediccion", "corregido", ["NUMERO_FORMATO"] * 2),
    "E04_fecha_formato.json": ("prediccion", "corregido", ["FECHA_FORMATO"]),
    "E05_faltan_familias.json": ("prediccion", "advertencia", ["FAMILIAS_FALTANTES_PRED"]),
    "E06_familia_repetida.json": ("prediccion", "advertencia", ["FAMILIA_REPETIDA_PRED"]),
    "E07_campo_renombrado.json": ("prediccion", "corregido", ["CLAVE_RENOMBRADA"]),
    "E08_ventas_negativas.json": ("observaciones", "advertencia", ["VENTAS_NEGATIVAS"]),
    "E09_envio_repetido.json": ("observaciones", "ok", []),
    "E10_numero_formato.json": ("observaciones", "corregido", ["NUMERO_FORMATO"] * 2),
}


@pytest.fixture(scope="module")
def catalogo():
    return cargar_catalogo(DATOS)


def _ruta(nombre: str) -> Path:
    return EJEMPLOS / nombre if (EJEMPLOS / nombre).exists() else EJEMPLOS / "errores" / nombre


def test_catalogo_real_deriva_rango_de_los_datos(catalogo):
    assert len(catalogo.familias) == 12
    assert catalogo.fecha_inicio <= date(2025, 11, 17) <= catalogo.fecha_fin_cobertura


@pytest.mark.parametrize("nombre", sorted(ESPERADO))
def test_ejemplos_publicados(nombre, catalogo):
    ruta = _ruta(nombre)
    if not ruta.exists():
        pytest.skip(f"falta {nombre}")
    endpoint, estado, cods = ESPERADO[nombre]
    funcion = procesar_prediccion if endpoint == "prediccion" else procesar_observaciones
    r = funcion(ruta.read_bytes(), catalogo)
    assert r.calidad.estado == estado
    assert [h.codigo for h in r.hallazgos] == cods
    if endpoint == "prediccion":
        assert len(r.solicitud.familias) == 12


def test_ejemplo_del_anexo_lacteos(catalogo):
    r = procesar_prediccion(_ruta("E02_familia_otra_forma.json").read_bytes(), catalogo)
    assert any(
        o.detalle == "'LÁCTEOS' se interpretó como 'lacteos'" for o in r.calidad.observaciones
    )


def test_todas_las_tiendas_reales_aceptan_su_apertura(catalogo):
    for t in catalogo.tiendas.values():
        cuerpo = (
            f'{{"tienda_id": {t.tienda_id}, "fecha": "{t.fecha_apertura.isoformat()}", '
            '"familias": [{"familia_id": "abarrotes", "en_promocion": 0}]}'
        )
        if t.fecha_apertura < catalogo.fecha_inicio:
            with pytest.raises(SolicitudRechazada):
                procesar_prediccion(cuerpo, catalogo)
        else:
            assert procesar_prediccion(cuerpo, catalogo).solicitud.tienda_id == t.tienda_id
