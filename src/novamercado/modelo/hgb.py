"""Modelo ML: HistGradientBoosting global con pérdida Poisson y cuantil τ por familia (§3).

- `venta_esperada`: media estimada por un HGB global (todas las tiendas y familias) entrenado con
  `features.variables` (la misma función al entrenar y al servir).
- `cantidad_sugerida = ceil(venta_esperada × q_f)`, con `q_f` el cuantil `τ_f = c_f/(c_f + c_s)`
  de la razón real/predicho medida **fuera de muestra** (newsvendor, DECISIONES §3.1).

`RecetaHGB` calcula las variables una sola vez y entrena con cualquier corte, de modo que la
validación con origen móvil (varios cortes) no repite trabajo. Como cada variable de la fecha `D`
solo depende de datos `< D`, entrenar con las filas `< corte` no usa nada posterior al corte.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from novamercado.features.variables import (
    FEATURES,
    Contexto,
    variables_entrenamiento,
    variables_prediccion,
)
from novamercado.modelo.baseline import COLUMNAS_SALIDA, COLUMNAS_SOLICITUD, FACTOR_MAX, FACTOR_MIN

#: Hiperparámetros validados en el backtest (DECISIONES §3: −42 % de costo frente al baseline).
HIPERPARAMETROS: dict[str, Any] = {
    "loss": "poisson",
    "max_iter": 400,
    "learning_rate": 0.06,
    "max_leaf_nodes": 63,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
}
DIAS_CALIBRACION = 56
#: Vida media (días) del peso de cada fila al entrenar: una fila de hace `VIDA_MEDIA_DIAS` pesa la
#: mitad que la del día anterior al corte (DECISIONES §4.4). None = todas pesan igual.
VIDA_MEDIA_DIAS: float | None = None
#: Mínimo de filas de una familia en la ventana de calibración para estimar su cuantil.
MIN_FILAS_CALIBRACION = 20
NOMBRE = "hgb_poisson"


def tau_por_familia(costos: dict[str, tuple[float, float]]) -> dict[str, float]:
    """τ_f = c_f / (c_f + c_s): el cuantil óptimo del costo del enunciado."""
    return {f: cf / (cf + cs) for f, (cf, cs) in costos.items() if cf + cs > 0}


def calibrar_factores(
    real: np.ndarray,
    predicho: np.ndarray,
    familias: pd.Series,
    costos: dict[str, tuple[float, float]],
) -> dict[str, float]:
    """q_f = cuantil τ_f de real/predicho por familia (solo predicciones > 0), acotado."""
    tau = tau_por_familia(costos)
    df = pd.DataFrame(
        {"r": real / np.maximum(predicho, 1e-9), "f": familias.to_numpy(), "p": predicho}
    )
    df = df[(df["p"] > 0) & np.isfinite(df["r"])]
    factores: dict[str, float] = {}
    for familia, grupo in df.groupby("f"):
        if familia not in tau or len(grupo) < MIN_FILAS_CALIBRACION:
            continue
        q = float(np.quantile(grupo["r"], tau[familia]))
        factores[str(familia)] = float(np.clip(q, FACTOR_MIN, FACTOR_MAX))
    return factores


def pesos_recencia(
    fechas: pd.Series, antes_de: pd.Timestamp, vida_media_dias: float | None
) -> np.ndarray | None:
    """Peso `0.5 ** (antigüedad / vida_media)` por fila, con la antigüedad medida desde el corte
    (no desde el reloj). None si no se pondera."""
    if vida_media_dias is None:
        return None
    edad = (antes_de - pd.to_datetime(fechas)).dt.days.to_numpy(dtype=float)
    return np.power(0.5, np.clip(edad, 0.0, None) / float(vida_media_dias))


@dataclass
class ModeloHGB:
    """Modelo serializable dentro del pyfunc: `predecir(solicitud, panel)` como el baseline."""

    ctx: Contexto
    estimador: HistGradientBoostingRegressor
    factores: dict[str, float] = field(default_factory=dict)
    fecha_corte: pd.Timestamp | None = None  # entrenado con filas `fecha < fecha_corte`
    nombre: str = NOMBRE

    def estimar(self, variables: pd.DataFrame) -> np.ndarray:
        """Venta esperada (≥ 0 y finita) para filas que ya tienen `FEATURES`."""
        if variables.empty:
            return np.zeros(0)
        p = np.asarray(self.estimador.predict(variables[FEATURES]), dtype=float)
        return np.where(np.isfinite(p), np.clip(p, 0.0, None), 0.0)

    def predecir(self, solicitud: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
        """Predice cada fila de `solicitud` (mismo orden) con el panel anterior a su fecha."""
        sol = solicitud.loc[:, COLUMNAS_SOLICITUD].copy()
        if sol.empty:
            return pd.DataFrame(columns=COLUMNAS_SALIDA)
        sol["tienda_id"] = sol["tienda_id"].astype("int64")
        sol["familia_id"] = sol["familia_id"].astype(str)
        sol["fecha"] = pd.to_datetime(sol["fecha"])
        variables = variables_prediccion(sol, panel, self.ctx)
        venta = self.estimar(variables)
        factor = sol["familia_id"].map(self.factores).fillna(1.0).to_numpy(dtype=float)
        sol["venta_esperada"] = np.round(venta, 2)
        sol["cantidad_sugerida"] = [
            max(0, math.ceil(v * f - 1e-9)) for v, f in zip(venta, factor, strict=True)
        ]
        return sol[COLUMNAS_SALIDA].reset_index(drop=True)


class RecetaHGB:
    """Variables calculadas una vez; entrena estimadores y modelos con cualquier corte."""

    def __init__(
        self,
        historia: pd.DataFrame,
        ctx: Contexto,
        costos: dict[str, tuple[float, float]],
        *,
        semilla: int = 0,
        vida_media_dias: float | None = VIDA_MEDIA_DIAS,
    ) -> None:
        self.ctx = ctx
        self.costos = costos
        self.semilla = semilla
        self.vida_media_dias = vida_media_dias
        self.entreno = variables_entrenamiento(historia, ctx, enmascarar=True, semilla=semilla)
        # Sin enmascarar: idénticas a las que se calculan al servir (para calibrar q_f).
        self.limpias = variables_entrenamiento(historia, ctx, enmascarar=False)

    def estimador(self, antes_de: pd.Timestamp) -> HistGradientBoostingRegressor | None:
        """HGB entrenado con las filas `fecha < antes_de` (None si no hay filas)."""
        filas = self.entreno[self.entreno["fecha"] < antes_de]
        if filas.empty:
            return None
        est = HistGradientBoostingRegressor(
            **HIPERPARAMETROS,
            categorical_features="from_dtype",
            early_stopping=False,
            random_state=self.semilla,
        )
        return est.fit(
            filas[FEATURES],
            filas["ventas"].to_numpy(dtype=float),
            sample_weight=pesos_recencia(filas["fecha"], antes_de, self.vida_media_dias),
        )

    def factores(
        self,
        estimador: HistGradientBoostingRegressor | None,
        desde: pd.Timestamp,
        hasta: pd.Timestamp,
    ) -> dict[str, float]:
        """q_f con las predicciones de `estimador` en `[desde, hasta)` (fuera de su muestra)."""
        if estimador is None:
            return {}
        filas = self.limpias[(self.limpias["fecha"] >= desde) & (self.limpias["fecha"] < hasta)]
        if filas.empty:
            return {}
        pred = np.clip(np.asarray(estimador.predict(filas[FEATURES]), dtype=float), 0, None)
        return calibrar_factores(
            filas["ventas"].to_numpy(dtype=float), pred, filas["familia_id"], self.costos
        )

    def modelo(
        self,
        antes_de: pd.Timestamp,
        *,
        auxiliar: tuple[HistGradientBoostingRegressor | None, pd.Timestamp] | None = None,
    ) -> ModeloHGB:
        """Modelo entrenado con `fecha < antes_de` y q_f calibrado fuera de muestra (§3.1).

        `auxiliar = (estimador, corte_aux)` reutiliza un estimador entrenado con `fecha <
        corte_aux` para calibrar; la ventana de calibración empieza en `corte_aux` o después,
        así que sigue siendo fuera de muestra. Sin él, se entrena uno con `fecha < antes_de − 56`.
        """
        inicio_cal = antes_de - pd.Timedelta(days=DIAS_CALIBRACION)
        if auxiliar is None:
            aux, corte_aux = self.estimador(inicio_cal), inicio_cal
        else:
            aux, corte_aux = auxiliar
        desde = max(inicio_cal, corte_aux)
        estimador = self.estimador(antes_de)
        if estimador is None:
            raise ValueError(f"No hay filas para entrenar antes de {antes_de.date()}")
        return ModeloHGB(
            ctx=self.ctx,
            estimador=estimador,
            factores=self.factores(aux, desde, antes_de),
            fecha_corte=antes_de,
        )
