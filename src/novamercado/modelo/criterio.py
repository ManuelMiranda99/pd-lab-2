"""Criterio de reemplazo del modelo vigente (versión del esqueleto; se afina en la Fase 5)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    promover: bool
    motivo: str


def _miles(x: float) -> str:
    """18210.4 → '18 210' (separador de miles con espacio, como en el Anexo)."""
    return f"{round(x):,}".replace(",", " ")


def decidir_reemplazo(
    costo_retador: float, costo_vigente: float | None, *, mejora_minima: float = 0.0
) -> Decision:
    """Promueve si no hay vigente o si el retador cuesta ≤ vigente × (1 − mejora_minima).

    Ambos costos deben venir de la **misma** ventana de validación.
    """
    if costo_vigente is None:
        return Decision(True, f"Sin modelo vigente; costo en validación {_miles(costo_retador)}")
    umbral = costo_vigente * (1.0 - mejora_minima)
    texto = f"Costo en validación {_miles(costo_retador)} vs {_miles(costo_vigente)} del vigente"
    if costo_retador <= umbral:
        return Decision(True, texto)
    return Decision(False, f"{texto}: no supera el criterio")
