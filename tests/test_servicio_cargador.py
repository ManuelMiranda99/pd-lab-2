"""Caché atómica y hot-reload del modelo con un registro falso (sin MLflow)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novamercado.servicio.cache import CacheModelos, EntradaCache
from novamercado.servicio.cargador import GestorModelo
from novamercado.servicio.registro import RegistroNoDisponible


class RegistroFalso:
    def __init__(self) -> None:
        self.version: str | None = None
        self.caido = False
        self.descargas: list[str] = []

    def version_con_alias(self, nombre: str, alias: str) -> str | None:
        if self.caido:
            raise RegistroNoDisponible("caído")
        return self.version

    def descargar(self, nombre: str, version: str, destino: Path) -> Path:
        if self.caido:
            raise RegistroNoDisponible("caído")
        self.descargas.append(version)
        carpeta = destino / "modelo"
        carpeta.mkdir()
        (carpeta / "MLmodel").write_text(f"version: {version}")
        return carpeta


def cargador_falso(ruta: Path):
    texto = (ruta / "MLmodel").read_text()
    if "malo" in texto:
        raise ValueError("predicción de humo fallida")
    return f"modelo<{texto}>"


@pytest.fixture
def gestor(tmp_path):
    registro = RegistroFalso()
    g = GestorModelo(
        registro,
        CacheModelos(tmp_path / "cache"),
        nombre="novamercado",
        alias="champion",
        cargador=cargador_falso,
    )
    g.cache.preparar()
    return g, registro


def test_sin_alias_no_hay_modelo(gestor):
    g, _ = gestor
    assert g.sincronizar() is True
    assert g.actual is None


def test_alias_nuevo_se_descarga_valida_y_queda_en_cache(gestor):
    g, reg = gestor
    reg.version = "1"
    assert g.sincronizar()
    assert g.actual is not None and g.actual.version == "1" and g.actual.origen == "mlflow"
    puntero = json.loads((g.cache.dir / "actual.json").read_text())
    assert puntero["version"] == "1"
    # Mismo alias: no vuelve a descargar.
    g.sincronizar()
    assert reg.descargas == ["1"]


def test_cambio_de_version_sin_reinicio(gestor):
    g, reg = gestor
    reg.version = "1"
    g.sincronizar()
    anterior = g.actual
    reg.version = "2"
    g.sincronizar()
    assert g.actual.version == "2"
    assert anterior.version == "1"  # la referencia previa no se modifica (inmutable)


def test_humo_fallido_conserva_el_vigente(gestor, monkeypatch):
    g, reg = gestor
    reg.version = "1"
    g.sincronizar()

    def descarga_mala(nombre, version, destino):
        carpeta = destino / "modelo"
        carpeta.mkdir()
        (carpeta / "MLmodel").write_text("malo")
        return carpeta

    monkeypatch.setattr(reg, "descargar", descarga_mala)
    reg.version = "2"
    assert g.sincronizar() is False
    assert g.actual.version == "1"
    assert g.cache.leer_actual().version == "1"
    assert not g.cache.ruta_version("novamercado", "2").exists()


def test_mlflow_caido_arranca_desde_cache(tmp_path):
    cache = CacheModelos(tmp_path / "cache")
    cache.preparar()
    ruta = cache.ruta_version("novamercado", "3")
    ruta.mkdir()
    (ruta / "MLmodel").write_text("version: 3")
    cache.marcar_actual(EntradaCache("novamercado", "champion", "3", ruta))

    reg = RegistroFalso()
    reg.caido = True
    g = GestorModelo(reg, cache, nombre="novamercado", alias="champion", cargador=cargador_falso)
    assert g.sincronizar() is False
    assert g.cargar_desde_cache() is True
    assert g.actual.version == "3" and g.actual.origen == "cache"


def test_sin_mlflow_ni_cache_no_hay_modelo(tmp_path):
    reg = RegistroFalso()
    reg.caido = True
    g = GestorModelo(
        reg, CacheModelos(tmp_path / "c"), nombre="n", alias="a", cargador=cargador_falso
    )
    g.cache.preparar()
    assert g.sincronizar() is False
    assert g.cargar_desde_cache() is False
    assert g.actual is None


def test_cache_limpia_temporales_y_poda_versiones(tmp_path):
    cache = CacheModelos(tmp_path)
    cache.preparar()
    tmp = cache.carpeta_temporal()
    assert tmp.name.startswith("tmp-")
    cache.preparar()
    assert not tmp.exists()
    for v in range(1, 6):
        ruta = cache.ruta_version("m", str(v))
        ruta.mkdir()
        cache.marcar_actual(EntradaCache("m", "champion", str(v), ruta))
    restantes = sorted(p.name for p in cache.dir_versiones.iterdir())
    assert len(restantes) <= 3 and "m-v5" in restantes
    assert cache.leer_actual().version == "5"


def test_puntero_corrupto_se_ignora(tmp_path):
    cache = CacheModelos(tmp_path)
    cache.preparar()
    (tmp_path / "actual.json").write_text("{no es json")
    assert cache.leer_actual() is None


def test_sin_modelo_consulta_mas_seguido(gestor):
    g, reg = gestor
    g.poll_segundos = 20
    assert g._espera() == 5
    reg.version = "1"
    g.sincronizar()
    assert g._espera() == 20
