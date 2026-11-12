"""BD del producto: localización de archivos (sin BD) y migraciones/carga/panel (Postgres)."""

from __future__ import annotations

import threading
from datetime import date

import pandas as pd
import pytest

from ayudas_esqueleto import bd as _fixture_bd  # noqa: F401 — fixture
from ayudas_esqueleto import bd_url as _fixture_bd_url  # noqa: F401 — fixture
from ayudas_esqueleto import escribir_csvs
from novamercado.db import repos
from novamercado.db.carga import ArchivosFaltantes, cargar_datos, localizar_archivos
from novamercado.db.conexion import bloqueo_consultivo
from novamercado.db.esquema import migrar
from novamercado.features.panel import leer_historia, leer_panel

# --- sin BD ----------------------------------------------------------------------------------


def test_localiza_archivos_en_subcarpeta_y_sin_distinguir_mayusculas(tmp_path):
    escribir_csvs(tmp_path / "datos_lab2")
    (tmp_path / "datos_lab2" / "costos.csv").rename(tmp_path / "datos_lab2" / "COSTOS.csv")
    archivos = localizar_archivos(tmp_path)
    assert set(archivos) == {
        "tiendas.csv",
        "familias.csv",
        "costos.csv",
        "feriados.csv",
        "historico_ventas.csv",
    }


def test_falla_con_mensaje_claro_si_faltan_archivos(tmp_path):
    escribir_csvs(tmp_path)
    (tmp_path / "feriados.csv").unlink()
    with pytest.raises(ArchivosFaltantes, match="feriados.csv"):
        localizar_archivos(tmp_path)
    with pytest.raises(ArchivosFaltantes, match="No existe"):
        localizar_archivos(tmp_path / "no_existe")


# --- con Postgres ----------------------------------------------------------------------------


@pytest.mark.integracion
def test_migrar_es_idempotente(bd):
    assert migrar(bd) == []
    versiones = [v for (v,) in bd.execute("SELECT version FROM migraciones")]
    bd.commit()
    assert versiones == [1, 2]


@pytest.mark.integracion
def test_carga_con_bom_es_idempotente(bd, tmp_path):
    escribir_csvs(tmp_path, bom=True, dias=14)
    primera = cargar_datos(bd, tmp_path)
    assert primera.cargado and primera.filas["historico_ventas"] == 14 * 2 * 3
    segunda = cargar_datos(bd, tmp_path)
    assert not segunda.cargado and segunda.sha256 == primera.sha256
    n = bd.execute("SELECT count(*) FROM historico_ventas").fetchone()[0]
    bd.commit()
    assert n == 14 * 2 * 3
    assert repos.costos_por_familia(bd)["panaderia"] == (1.0, 2.5)


@pytest.mark.integracion
def test_carga_fallida_no_deja_nada(bd, tmp_path):
    escribir_csvs(tmp_path, dias=7)
    hist = tmp_path / "historico_ventas.csv"
    hist.write_text(hist.read_text() + "2025-01-20,99,abarrotes,1,0\n")  # tienda inexistente
    with pytest.raises(Exception):  # noqa: B017 — violación de FK de Postgres
        cargar_datos(bd, tmp_path)
    bd.rollback()
    assert bd.execute("SELECT count(*) FROM tiendas").fetchone()[0] == 0
    assert bd.execute("SELECT count(*) FROM carga_datos").fetchone()[0] == 0


@pytest.mark.integracion
def test_panel_filtra_fecha_menor_a_d_en_sql_y_observacion_gana(bd, tmp_path):
    escribir_csvs(tmp_path, dias=14)  # 2025-01-06 … 2025-01-19
    cargar_datos(bd, tmp_path)
    bd.execute(
        "INSERT INTO observaciones (tienda_id, fecha, familia_id, ventas) VALUES "
        "(1, '2025-01-10', 'abarrotes', 7777), (1, '2025-01-20', 'abarrotes', 5)"
    )
    bd.commit()
    panel = leer_panel(bd, date(2025, 1, 15), tienda_id=1)
    assert panel["fecha"].max() < pd.Timestamp("2025-01-15")
    assert set(panel["tienda_id"]) == {1}
    fila = panel[(panel["fecha"] == "2025-01-10") & (panel["familia_id"] == "abarrotes")]
    assert list(fila["ventas"]) == [7777.0]
    historia = leer_historia(bd)
    assert str(historia["fecha"].max().date()) == "2025-01-20"


@pytest.mark.integracion
def test_reentrenamiento_termina_una_sola_vez(bd):
    repos.crear_reentrenamiento(bd, "abc")
    assert repos.obtener_reentrenamiento(bd, "abc").estado == "en_curso"
    assert repos.terminar_reentrenamiento(
        bd, "abc", estado="exitoso", promovido=True, version="2", motivo="ok"
    )
    assert not repos.terminar_reentrenamiento(
        bd, "abc", estado="fallido", promovido=False, version="1", motivo="tarde"
    )
    fila = repos.obtener_reentrenamiento(bd, "abc")
    assert (fila.estado, fila.promovido, fila.version) == ("exitoso", True, "2")
    assert repos.obtener_reentrenamiento(bd, "no-existe") is None


@pytest.mark.integracion
def test_bloqueo_consultivo_serializa(bd_url):
    orden: list[str] = []
    dentro = threading.Event()

    def otro():
        dentro.wait(5)
        with bloqueo_consultivo(bd_url):
            orden.append("segundo")

    hilo = threading.Thread(target=otro)
    hilo.start()
    with bloqueo_consultivo(bd_url):
        dentro.set()
        hilo.join(0.5)
        orden.append("primero")
    hilo.join(5)
    assert orden == ["primero", "segundo"]
