"""La tabla de políticas en código es la única fuente y coincide con DECISIONES.md."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from novamercado.limpieza import POLITICAS, Accion, Hallazgo, SolicitudRechazada, tabla_markdown
from novamercado.limpieza.calidad import construir_calidad

RAIZ = Path(__file__).resolve().parents[1]
FUENTES = RAIZ / "src" / "novamercado"


def _filas(tabla: str) -> list[list[str]]:
    filas = []
    for linea in tabla.strip().splitlines()[2:]:
        filas.append([c.strip() for c in linea.strip().strip("|").split("|")])
    return filas


def test_decisiones_contiene_la_tabla_del_codigo():
    texto = (RAIZ / "DECISIONES.md").read_text(encoding="utf-8")
    m = re.search(r"<!-- politicas:inicio -->\n(.*?)\n<!-- politicas:fin -->", texto, re.S)
    assert m, "faltan los marcadores de la tabla de políticas en DECISIONES.md"
    assert _filas(m.group(1)) == _filas(tabla_markdown()), (
        "DECISIONES.md §1 no coincide con limpieza/politicas.py: regenerar con tabla_markdown()"
    )


def test_codigos_usados_en_el_codigo_existen_en_la_tabla():
    usados: set[str] = set()
    for archivo in (FUENTES / "limpieza").glob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        usados |= set(re.findall(r'(?:Hallazgo|SolicitudRechazada)\(\s*"([A-Z_]+)"', texto))
    assert usados, "no se encontraron códigos en el código"
    assert usados <= set(POLITICAS), usados - set(POLITICAS)


def test_catalogo_e01_e10_cubierto():
    ejemplos = " ".join(p.ejemplo for p in POLITICAS.values())
    for i in range(1, 11):
        assert f"E{i:02d}" in ejemplos


def test_mensajes_de_politicas_no_ignoradas():
    for p in POLITICAS.values():
        assert p.problema and p.efecto
        if p.accion is not Accion.IGNORAR:
            assert p.mensaje, p.codigo
        if p.accion is Accion.RECHAZAR:
            assert p.efecto == "422"


def test_rechazo_solo_con_politica_de_rechazo():
    with pytest.raises(ValueError):
        SolicitudRechazada("NUMERO_FORMATO", "x", recibido="1", valor=1)
    with pytest.raises(ValueError):
        Hallazgo("TIENDA_INEXISTENTE", "tienda_id", {"tienda_id": 1}).a_observacion()


def test_ignorar_no_genera_observacion():
    calidad = construir_calidad([Hallazgo("CLAVE_DESCONOCIDA", "x")])
    assert calidad.estado == "ok" and calidad.observaciones == []


def test_sin_reloj_del_sistema():
    """Regla 3: la limpieza y el contrato no usan ``now()``/``today()``."""
    for carpeta in ("limpieza", "contrato"):
        for archivo in (FUENTES / carpeta).glob("*.py"):
            texto = archivo.read_text(encoding="utf-8")
            assert not re.search(r"\b(now|today|utcnow)\(", texto), archivo.name
            assert "time.time(" not in texto, archivo.name
