"""Validación temporal con origen móvil (DECISIONES §4.1), sin MLflow ni BD.

- Ventanas: los días con dato posteriores al corte del vigente (los últimos `DIAS_MAX_EVALUACION`
  como máximo), en bloques **completos** de `LARGO_VENTANA` días contados desde el final (el resto
  más antiguo se descarta: una ventana de 1–6 días pesaría igual en el conteo de ventanas ganadas).
  Sin vigente, los últimos días de la historia.
- **Vigente:** se usa tal cual, sin reentrenar.
- **Retador:** la receta se reentrena con datos anteriores al inicio de cada ventana, y su q_f se
  calibra fuera de muestra. **Baseline:** igual, con su propia receta (condición 4 de §4.2).
- Los tres predicen cada día `D` con un panel `< D` (pronóstico a un paso, como en el servicio) y
  la promoción conocida de ese día (NaN si se desconoce, §6.3).

Para que el reentreno quepa en < 3 min, un solo estimador auxiliar (entrenado antes de la primera
ventana menos 56 días) calibra q_f de todas las ventanas y del modelo final: todas sus ventanas de
calibración son posteriores a su corte, así que la calibración sigue siendo fuera de muestra.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from novamercado.features.panel import DIAS_PANEL
from novamercado.features.variables import Contexto
from novamercado.modelo.baseline import ajustar_baseline
from novamercado.modelo.criterio import Comparacion
from novamercado.modelo.hgb import DIAS_CALIBRACION, ModeloHGB, RecetaHGB
from novamercado.modelo.metricas import costo_total, metricas_tecnicas

DIAS_MAX_EVALUACION = 21
LARGO_VENTANA = 7


def ventanas_evaluacion(
    fechas: list[pd.Timestamp] | pd.Series, corte: pd.Timestamp | None
) -> list[list[pd.Timestamp]]:
    """Días con dato `>= corte` (todos si no hay corte), los últimos 21, en bloques completos de 7
    contados desde el final (el resto más antiguo, de menos de 7 días, se descarta)."""
    dias = sorted(pd.to_datetime(pd.Series(list(fechas))).drop_duplicates())
    if corte is not None:
        dias = [d for d in dias if d >= corte]
    dias = dias[-DIAS_MAX_EVALUACION:]
    dias = dias[len(dias) % LARGO_VENTANA :]
    return [dias[i : i + LARGO_VENTANA] for i in range(0, len(dias), LARGO_VENTANA)]


def dias_evaluables(fechas: list[pd.Timestamp] | pd.Series, corte: pd.Timestamp | None) -> int:
    """Días que entrarían en ventanas completas de evaluación después de `corte`."""
    return sum(len(v) for v in ventanas_evaluacion(fechas, corte))


def prediccion_rodante(
    modelo: Any, historia: pd.DataFrame, fechas: list[pd.Timestamp]
) -> pd.DataFrame:
    """Para cada `D`, predice las filas reales de `D` con el panel `< D` (como el servicio)."""
    partes = []
    if "en_promocion" not in historia:
        historia = historia.assign(en_promocion=float("nan"))
    for d in fechas:
        reales = historia[historia["fecha"] == d]
        if reales.empty:
            continue
        panel = historia[
            (historia["fecha"] < d) & (historia["fecha"] >= d - pd.Timedelta(days=DIAS_PANEL))
        ]
        sol = reales[["tienda_id", "fecha", "familia_id", "en_promocion"]]
        pred = modelo.predecir(sol, panel)
        partes.append(
            pred.merge(
                reales[["tienda_id", "fecha", "familia_id", "ventas"]],
                on=["tienda_id", "fecha", "familia_id"],
            )
        )
    if not partes:
        return pd.DataFrame(
            columns=[
                "tienda_id",
                "fecha",
                "familia_id",
                "venta_esperada",
                "cantidad_sugerida",
                "ventas",
            ]
        )
    return pd.concat(partes, ignore_index=True)


@dataclass
class ResultadoEntreno:
    """Modelo final del retador y su evaluación contra vigente y baseline."""

    modelo: ModeloHGB
    comparacion: Comparacion
    ventanas: list[dict[str, Any]] = field(default_factory=list)
    tecnicas: dict[str, float] = field(default_factory=dict)
    tecnicas_baseline: dict[str, float] = field(default_factory=dict)
    tecnicas_vigente: dict[str, float] = field(default_factory=dict)
    segundos: float = 0.0


def entrenar_y_evaluar(
    historia: pd.DataFrame,
    ctx: Contexto,
    costos: dict[str, tuple[float, float]],
    *,
    vigente: Any | None = None,
    corte_vigente: pd.Timestamp | None = None,
    semilla: int = 0,
) -> ResultadoEntreno:
    """Entrena el retador (receta HGB) y lo compara con vigente y baseline en las mismas ventanas.

    `corte_vigente`: primer día que el vigente no vio al entrenar. Si hay vigente pero se
    desconoce su corte (modelos anteriores a esta versión), se evalúa en los últimos días.
    """
    t0 = time.perf_counter()
    historia = historia.copy()
    historia["fecha"] = pd.to_datetime(historia["fecha"])
    fin = historia["fecha"].max() + pd.Timedelta(days=1)
    vents = ventanas_evaluacion(historia["fecha"], corte_vigente if vigente is not None else None)
    receta = RecetaHGB(historia, ctx, costos, semilla=semilla)
    primera = vents[0][0] if vents else fin
    corte_aux = primera - pd.Timedelta(days=DIAS_CALIBRACION)
    auxiliar = (receta.estimador(corte_aux), corte_aux)

    filas: list[dict[str, Any]] = []
    preds: dict[str, list[pd.DataFrame]] = {"retador": [], "baseline": [], "vigente": []}
    for dias in vents:
        inicio = dias[0]
        if not (historia["fecha"] < inicio).any():
            continue  # sin datos anteriores a la ventana no hay con qué entrenar
        retador_w = receta.modelo(inicio, auxiliar=auxiliar)
        baseline_w = ajustar_baseline(historia, costos, antes_de=inicio)
        fila: dict[str, Any] = {
            "inicio": str(inicio.date()),
            "fin": str(dias[-1].date()),
            "dias": len(dias),
        }
        for rol, modelo in (("retador", retador_w), ("baseline", baseline_w), ("vigente", vigente)):
            if modelo is None:
                continue
            p = prediccion_rodante(modelo, historia, dias)
            preds[rol].append(p)
            fila[f"costo_{rol}"] = costo_total(p, costos)
        fila["filas"] = int(sum(len(p) for p in preds["retador"][-1:]))
        filas.append(fila)

    final = receta.modelo(fin, auxiliar=auxiliar)
    comp = Comparacion(
        retador=[f["costo_retador"] for f in filas],
        baseline=[f["costo_baseline"] for f in filas],
        vigente=[f["costo_vigente"] for f in filas] if vigente is not None else None,
        dias=sum(f["dias"] for f in filas),
    )

    def _tecnicas(rol: str) -> dict[str, float]:
        return metricas_tecnicas(pd.concat(preds[rol], ignore_index=True)) if preds[rol] else {}

    return ResultadoEntreno(
        modelo=final,
        comparacion=comp,
        ventanas=filas,
        tecnicas=_tecnicas("retador"),
        tecnicas_baseline=_tecnicas("baseline"),
        tecnicas_vigente=_tecnicas("vigente"),
        segundos=time.perf_counter() - t0,
    )
