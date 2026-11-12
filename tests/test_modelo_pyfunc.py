"""Empaquetado pyfunc local (sin servidor de MLflow)."""

from __future__ import annotations

import mlflow.pyfunc
import pandas as pd
import pytest

from ayudas_esqueleto import COSTOS, FAMILIAS, panel_sintetico
from novamercado.modelo.baseline import ajustar_baseline
from novamercado.modelo.pyfunc import (
    armar_entrada,
    entrada_de_humo,
    guardar_local,
    validar_salida,
)


@pytest.fixture(scope="module")
def ruta_modelo(tmp_path_factory):
    panel = panel_sintetico()
    destino = tmp_path_factory.mktemp("pyfunc") / "modelo"
    guardar_local(ajustar_baseline(panel, COSTOS), destino)
    return destino


def test_pyfunc_guarda_code_paths_y_predice(ruta_modelo):
    assert (ruta_modelo / "code" / "novamercado" / "modelo" / "baseline.py").is_file()
    modelo = mlflow.pyfunc.load_model(str(ruta_modelo))
    panel = panel_sintetico()
    d = panel["fecha"].max() + pd.Timedelta(days=1)
    solicitud = pd.DataFrame(
        [(1, d, f, 0) for f in FAMILIAS],
        columns=["tienda_id", "fecha", "familia_id", "en_promocion"],
    )
    salida = modelo.predict(armar_entrada(solicitud, panel))
    validar_salida(salida, len(FAMILIAS))
    assert set(salida["familia_id"]) == set(FAMILIAS)


def test_humo_valida(ruta_modelo):
    modelo = mlflow.pyfunc.load_model(str(ruta_modelo))
    validar_salida(modelo.predict(entrada_de_humo()), 2)


@pytest.mark.parametrize(
    "salida",
    [
        pd.DataFrame({"venta_esperada": [1.0], "cantidad_sugerida": [1]}),  # filas de menos
        pd.DataFrame({"venta_esperada": [float("nan"), 1.0], "cantidad_sugerida": [1, 1]}),
        pd.DataFrame({"venta_esperada": [1.0, 1.0], "cantidad_sugerida": [-1, 1]}),
        pd.DataFrame({"venta_esperada": [1.0, 1.0], "cantidad_sugerida": [1.5, 1]}),
    ],
)
def test_validar_salida_rechaza_salidas_invalidas(salida):
    with pytest.raises(ValueError):
        validar_salida(salida, 2)
