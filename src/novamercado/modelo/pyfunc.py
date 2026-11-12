"""Empaquetado del modelo como pyfunc de MLflow (con el paquete `novamercado` en `code_paths`).

Entrada del pyfunc: un DataFrame con la columna `rol`:
- `rol == "solicitud"`: filas a predecir (`tienda_id, fecha, familia_id, en_promocion`).
- `rol == "panel"`: ventas anteriores (`tienda_id, fecha, familia_id, ventas`), ya filtradas
  `fecha < D` en SQL.
Salida: `tienda_id, fecha, familia_id, venta_esperada, cantidad_sugerida`.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import mlflow.pyfunc
import pandas as pd

import novamercado

RUTA_PAQUETE = Path(novamercado.__file__).resolve().parent

REQUISITOS_PIP = [
    "mlflow-skinny==3.18.0",
    "numpy==2.4.6",
    "pandas==3.1.1",
]


class ModeloNovaMercado(mlflow.pyfunc.PythonModel):
    """Envuelve un modelo con `predecir(solicitud, panel)`."""

    def __init__(self, modelo: Any) -> None:
        self.modelo = modelo

    def predict(self, context: Any, model_input: pd.DataFrame, params: Any = None) -> pd.DataFrame:
        rol = model_input["rol"]
        solicitud = model_input[rol == "solicitud"]
        panel = model_input[rol == "panel"]
        panel = panel.assign(
            tienda_id=panel["tienda_id"].astype("int64"),
            fecha=pd.to_datetime(panel["fecha"]),
            ventas=panel["ventas"].astype(float),
        )
        return self.modelo.predecir(solicitud, panel)


def armar_entrada(solicitud: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    """Une solicitud y panel en el DataFrame de entrada del pyfunc."""
    s = solicitud.loc[:, ["tienda_id", "fecha", "familia_id", "en_promocion"]].assign(
        rol="solicitud", ventas=float("nan")
    )
    p = panel.loc[:, ["tienda_id", "fecha", "familia_id", "ventas"]].assign(
        rol="panel", en_promocion=0
    )
    df = pd.concat([s, p], ignore_index=True)
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["tienda_id"] = df["tienda_id"].astype("int64")
    df["en_promocion"] = df["en_promocion"].astype("int64")
    df["ventas"] = df["ventas"].astype(float)
    return df[["rol", "tienda_id", "fecha", "familia_id", "en_promocion", "ventas"]]


def entrada_de_humo() -> pd.DataFrame:
    """Entrada sintética mínima para validar un modelo recién descargado."""
    fechas = pd.date_range("2024-01-01", periods=35, freq="D")
    panel = pd.DataFrame(
        [(1, f, fam, 10.0) for f in fechas for fam in ("humo_a", "humo_b")],
        columns=["tienda_id", "fecha", "familia_id", "ventas"],
    )
    solicitud = pd.DataFrame(
        [(1, fechas[-1] + pd.Timedelta(days=1), fam, 0) for fam in ("humo_a", "humo_b")],
        columns=["tienda_id", "fecha", "familia_id", "en_promocion"],
    )
    return armar_entrada(solicitud, panel)


def validar_salida(salida: pd.DataFrame, filas_esperadas: int) -> None:
    """Lanza ValueError si la salida no cumple lo mínimo (filas, finitos, enteros ≥ 0)."""
    if len(salida) != filas_esperadas:
        raise ValueError(f"El modelo devolvió {len(salida)} filas; se esperaban {filas_esperadas}")
    for v in salida["venta_esperada"]:
        if not math.isfinite(float(v)) or float(v) < 0:
            raise ValueError(f"venta_esperada inválida: {v}")
    for c in salida["cantidad_sugerida"]:
        if int(c) != c or int(c) < 0:
            raise ValueError(f"cantidad_sugerida inválida: {c}")


def guardar_local(modelo: Any, destino: Path) -> None:
    """Guarda el pyfunc en una carpeta local (pruebas y caché)."""
    mlflow.pyfunc.save_model(
        path=str(destino),
        python_model=ModeloNovaMercado(modelo),
        code_paths=[str(RUTA_PAQUETE)],
        pip_requirements=REQUISITOS_PIP,
    )
