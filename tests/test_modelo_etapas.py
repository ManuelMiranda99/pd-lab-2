"""Etapas del DAG `reentrenamiento` con Postgres y un registro de alias falso (sin MLflow).

Verifican el orden de escritura de DECISIONES §6.4: registrar → provisional → alias → `exitoso`, y
que un fallo o un retador que no gana **no** toquen el alias.
"""

from __future__ import annotations

import psycopg
import pytest

from ayudas_esqueleto import bd as _fixture_bd  # noqa: F401 — fixture
from ayudas_esqueleto import bd_url as _fixture_bd_url  # noqa: F401 — fixture
from ayudas_esqueleto import escribir_csvs
from novamercado.config import Config
from novamercado.db import repos
from novamercado.db.carga import cargar_datos
from novamercado.modelo import etapas
from novamercado.modelo.ciclo import Resultado


class AliasFalso:
    def __init__(self, version: str | None = "1", *, caido: bool = False) -> None:
        self.version = version
        self.caido = caido
        self.movimientos: list[str] = []

    def version_con_alias(self) -> str | None:
        if self.caido:
            raise ConnectionError("MLflow no responde")
        return self.version

    def mover_alias(self, version: str) -> None:
        if self.caido:
            raise ConnectionError("MLflow no responde")
        self.version = version
        self.movimientos.append(version)
        self.promovidas: list[str] = getattr(self, "promovidas", [])

    def marcar_promovida(self, version: str) -> None:
        self.promovidas = [*getattr(self, "promovidas", []), version]


def _entrenador(promover: bool, *, registrada: str = "2", previa: str | None = "1"):
    def entrenar(cfg, *, id_reentreno, comparar, mover_alias):
        assert comparar and not mover_alias  # el DAG nunca mueve el alias al entrenar
        motivo = "Costo 100 vs 120 del vigente: promovido" if promover else "Costo 130: se conserva"
        return Resultado(
            promovido=promover,
            version=previa,
            version_registrada=registrada,
            version_previa=previa,
            motivo=motivo,
            detalle={"costo_retador": 100.0},
        )

    return entrenar


def _fallar(*_a, **_k):
    raise RuntimeError("el entrenamiento explotó")


@pytest.fixture
def cfg(bd, bd_url, tmp_path) -> Config:
    escribir_csvs(tmp_path, dias=21)
    cargar_datos(bd, tmp_path)
    return Config(db_url=bd_url)


def _fila(cfg: Config, id_: str) -> repos.Reentrenamiento:
    with psycopg.connect(cfg.db_url) as conn:
        fila = repos.obtener_reentrenamiento(conn, id_)
    assert fila is not None
    return fila


def _crear(cfg: Config, id_: str = "r1") -> str:
    with psycopg.connect(cfg.db_url) as conn:
        repos.crear_reentrenamiento(conn, id_)
    return id_


def _correr(cfg, id_, alias, entrenador) -> None:
    """Corre las etapas como el DAG: se detiene en la primera que falla y siempre cierra."""
    try:
        etapas.verificar(id_, cfg, registro=alias)
        etapas.entrenar(id_, cfg, entrenador=entrenador)
        etapas.promover(id_, cfg, registro=alias)
        etapas.confirmar(id_, cfg, registro=alias)
    except Exception:  # noqa: BLE001 — el DAG marca la tarea fallida y sigue con `cerrar`
        pass
    etapas.cerrar(id_, cfg, registro=alias)


@pytest.mark.integracion
def test_retador_que_gana_mueve_el_alias_y_queda_exitoso(cfg):
    alias = AliasFalso("1")
    id_ = _crear(cfg)
    _correr(cfg, id_, alias, _entrenador(True))
    fila = _fila(cfg, id_)
    assert (fila.estado, fila.promovido, fila.version) == ("exitoso", True, "2")
    assert "promovido" in fila.motivo
    assert alias.movimientos == ["2"]
    d = fila.detalle
    assert d["registrado"] and d["alias_movido"] and d["version_previa"] == "1"
    assert d["etapa"] == "terminado" and d["resultado"] == {"costo_retador": 100.0}


@pytest.mark.integracion
def test_retador_que_no_gana_no_toca_el_alias_y_version_es_la_vigente(cfg):
    alias = AliasFalso("1")
    id_ = _crear(cfg)
    _correr(cfg, id_, alias, _entrenador(False))
    fila = _fila(cfg, id_)
    assert (fila.estado, fila.promovido, fila.version) == ("exitoso", False, "1")
    assert alias.movimientos == []


@pytest.mark.integracion
def test_fallo_al_entrenar_queda_fallido_con_la_etapa_y_sin_tocar_el_alias(cfg):
    alias = AliasFalso("1")
    id_ = _crear(cfg)
    _correr(cfg, id_, alias, _fallar)
    fila = _fila(cfg, id_)
    assert (fila.estado, fila.promovido, fila.version) == ("fallido", False, "1")
    assert "etapa 'entrenar'" in fila.motivo and "explotó" in fila.motivo
    assert "vigente no cambió" in fila.motivo
    assert alias.movimientos == []


@pytest.mark.integracion
def test_alias_movido_y_fallo_al_confirmar_se_cierra_como_exitoso(cfg, monkeypatch):
    alias = AliasFalso("1")
    id_ = _crear(cfg)

    def confirmar_roto(*_a, **_k):
        raise RuntimeError("se cayó la BD justo al confirmar")

    monkeypatch.setattr(etapas, "confirmar", confirmar_roto)
    _correr(cfg, id_, alias, _entrenador(True))
    fila = _fila(cfg, id_)
    # El modelo nuevo ya está en uso: informar `fallido` sería falso.
    assert (fila.estado, fila.promovido, fila.version) == ("exitoso", True, "2")
    assert "alias ya se había movido" in fila.motivo


@pytest.mark.integracion
def test_alias_movido_sin_anotarlo_se_detecta_al_cerrar(cfg):
    """Fallo entre mover el alias y anotar `alias_movido`: `cerrar` lo deduce del registro."""
    alias = AliasFalso("1")
    id_ = _crear(cfg)
    etapas.verificar(id_, cfg, registro=alias)
    etapas.entrenar(id_, cfg, entrenador=_entrenador(True))
    alias.mover_alias("2")  # como si `promover` muriera justo después de mover el alias
    etapas.cerrar(id_, cfg, registro=alias)
    fila = _fila(cfg, id_)
    assert (fila.estado, fila.promovido, fila.version) == ("exitoso", True, "2")


@pytest.mark.integracion
def test_alias_cambiado_durante_el_reentreno_no_se_pisa(cfg):
    alias = AliasFalso("1")
    id_ = _crear(cfg)
    etapas.verificar(id_, cfg, registro=alias)
    etapas.entrenar(id_, cfg, entrenador=_entrenador(True))
    alias.version = "5"  # otro proceso lo movió
    assert etapas.promover(id_, cfg, registro=alias) == {"alias_movido": False}
    etapas.confirmar(id_, cfg, registro=alias)
    fila = _fila(cfg, id_)
    assert (fila.estado, fila.promovido, fila.version) == ("exitoso", False, "5")
    assert "el alias cambió de v1 a v5" in fila.motivo
    assert alias.movimientos == []


@pytest.mark.integracion
def test_mlflow_caido_al_verificar_falla_sin_entrenar(cfg):
    alias = AliasFalso("1", caido=True)
    id_ = _crear(cfg)
    llamado = []
    _correr(cfg, id_, alias, lambda *a, **k: llamado.append(1))
    fila = _fila(cfg, id_)
    assert fila.estado == "fallido" and not fila.promovido and fila.version is None
    assert "etapa 'verificar'" in fila.motivo and "MLflow no disponible" in fila.motivo
    assert llamado == []


@pytest.mark.integracion
def test_disparo_manual_desde_airflow_crea_la_fila(cfg):
    alias = AliasFalso("1")
    id_ = "manual__2026-03-01T00:00:00+00:00"
    _correr(cfg, id_, alias, _entrenador(False))
    assert _fila(cfg, id_).estado == "exitoso"


@pytest.mark.integracion
def test_sin_datos_falla_al_verificar(bd, bd_url):
    cfg = Config(db_url=bd_url)
    id_ = _crear(cfg)
    _correr(cfg, id_, AliasFalso(None), _entrenador(True))
    fila = _fila(cfg, id_)
    assert fila.estado == "fallido" and "No hay datos" in fila.motivo


@pytest.mark.integracion
def test_etapas_no_reabren_una_ejecucion_terminada(cfg):
    alias = AliasFalso("1")
    id_ = _crear(cfg)
    _correr(cfg, id_, alias, _entrenador(False))
    with pytest.raises(etapas.ErrorEtapa, match="ya terminó"):
        etapas.promover(id_, cfg, registro=alias)
    assert etapas.cerrar(id_, cfg, registro=alias)["cambios"] is False
    assert _fila(cfg, id_).estado == "exitoso"


@pytest.mark.parametrize(
    ("error", "esperado"),
    [
        (
            ConnectionError(
                "API request to http://mlflow:5000/api/2.0/mlflow/experiments/get-by-name failed"
                " with exception HTTPConnectionPool(host='mlflow', port=5000): Max retries exceeded"
            ),
            "MLflow no respondió (sin conexión)",
        ),
        (RuntimeError("primera línea\nsegunda"), "primera línea"),
        (RuntimeError(""), "RuntimeError"),
        (RuntimeError("x" * 500), "x" * (etapas.LARGO_ERROR - 1) + "…"),
    ],
)
def test_texto_error_legible(error, esperado):
    assert etapas.texto_error(error) == esperado
