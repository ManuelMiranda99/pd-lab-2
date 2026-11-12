"""Criterio de reemplazo del modelo vigente (DECISIONES §4.2), como función pura.

El retador se promueve solo si se cumplen **todas**:
1. hay al menos `DIAS_MINIMOS` días evaluables después del corte del vigente (dos ventanas
   completas de 7 días);
2. `costo_retador ≤ costo_vigente × (1 − mejora_minima)` en el total de las ventanas;
3. el retador gana en al menos la mitad de las ventanas;
4. el retador cuesta menos que el baseline en esas mismas ventanas.

Sin vigente (arranque en frío) basta la condición 4. Todos los costos vienen de las **mismas**
ventanas y días.
"""

from __future__ import annotations

from dataclasses import dataclass

MEJORA_MINIMA = 0.005
DIAS_MINIMOS = 14


@dataclass(frozen=True)
class Decision:
    promover: bool
    motivo: str


@dataclass(frozen=True)
class Comparacion:
    """Costos por ventana (mismo orden y mismos días para los tres modelos)."""

    retador: list[float]
    baseline: list[float]
    vigente: list[float] | None = None  # None: no hay modelo vigente
    dias: int = 0  # días evaluados (después del corte del vigente, si lo hay)

    @property
    def total_retador(self) -> float:
        return float(sum(self.retador))

    @property
    def total_baseline(self) -> float:
        return float(sum(self.baseline))

    @property
    def total_vigente(self) -> float | None:
        return None if self.vigente is None else float(sum(self.vigente))

    @property
    def ventanas_ganadas(self) -> int:
        if self.vigente is None:
            return 0
        return sum(r < v for r, v in zip(self.retador, self.vigente, strict=True))


def miles(x: float) -> str:
    """18210.4 → '18 210' (separador de miles con espacio, como en el Anexo)."""
    return f"{round(x):,}".replace(",", " ")


def porcentaje(relativo: float) -> str:
    """-0.0073 → '−0.7 %'."""
    return f"{relativo * 100:+.1f} %".replace("-", "−")


def _relativo(a: float, b: float) -> float:
    return a / b - 1.0 if b > 0 else 0.0


def decidir_reemplazo(
    comp: Comparacion, *, mejora_minima: float = MEJORA_MINIMA, dias_minimos: int = DIAS_MINIMOS
) -> Decision:
    """Aplica §4.2 y devuelve la decisión con un motivo legible que lleva ambos costos."""
    ret, base = comp.total_retador, comp.total_baseline
    vs_base = f"{miles(ret)} vs {miles(base)} del baseline ({porcentaje(_relativo(ret, base))})"
    if comp.vigente is None:
        if comp.retador and ret < base:
            return Decision(True, f"Sin modelo vigente; costo en validación {vs_base}: promovido")
        return Decision(
            False, f"Sin modelo vigente; costo en validación {vs_base}: no mejora al baseline"
        )

    if comp.dias < dias_minimos or not comp.retador:
        return Decision(
            False,
            f"Solo {comp.dias} días evaluables después del corte del vigente "
            f"(mínimo {dias_minimos}): se conserva el vigente",
        )
    vig = float(comp.total_vigente or 0.0)
    n, ganadas = len(comp.retador), comp.ventanas_ganadas
    texto = (
        f"Costo en validación {miles(ret)} vs {miles(vig)} del vigente "
        f"({porcentaje(_relativo(ret, vig))}, gana {ganadas}/{n} ventanas)"
    )
    fallas = []
    if not ret <= vig * (1.0 - mejora_minima):
        fallas.append(f"mejora menor a {porcentaje(-mejora_minima).lstrip('−')}")
    if 2 * ganadas < n:
        fallas.append("gana menos de la mitad de las ventanas")
    if not ret < base:
        fallas.append(f"no mejora al baseline ({miles(base)})")
    if fallas:
        return Decision(False, f"{texto}: se conserva el vigente ({'; '.join(fallas)})")
    return Decision(True, f"{texto}: promovido")
