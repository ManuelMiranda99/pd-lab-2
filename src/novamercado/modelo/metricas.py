"""Métricas técnicas y de negocio (costo de las sugerencias con `costos.csv`)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def costo_total(
    df: pd.DataFrame, costos: dict[str, tuple[float, float]], *, columna: str = "cantidad_sugerida"
) -> float:
    """Σ c_faltante·max(real − sugerido, 0) + c_sobrante·max(sugerido − real, 0).

    `df` necesita `familia_id`, `ventas` (real) y la columna sugerida.
    """
    cf = df["familia_id"].map(lambda f: costos.get(f, (1.0, 1.0))[0]).astype(float)
    cs = df["familia_id"].map(lambda f: costos.get(f, (1.0, 1.0))[1]).astype(float)
    real = df["ventas"].astype(float)
    sug = df[columna].astype(float)
    return float((cf * np.maximum(real - sug, 0) + cs * np.maximum(sug - real, 0)).sum())


def metricas_tecnicas(df: pd.DataFrame) -> dict[str, float]:
    """MAE, RMSE, WAPE y sesgo de `venta_esperada` frente a `ventas`."""
    err = df["venta_esperada"].astype(float) - df["ventas"].astype(float)
    total = float(df["ventas"].abs().sum())
    return {
        "mae": float(err.abs().mean()),
        "rmse": float(np.sqrt((err**2).mean())),
        "wape": float(err.abs().sum() / total) if total > 0 else float("nan"),
        "sesgo": float(err.mean()),
    }
