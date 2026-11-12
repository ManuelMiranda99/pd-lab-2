"""Salud del servicio: chequeos en un hilo de fondo con timeouts cortos y resultado en caché.

`GET /health` solo lee el último resultado (no bloquea ni espera a las dependencias).
Con `salud_segundos=10` y timeouts de 3 s, una caída de MLflow se refleja en < 1 min.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

Chequeo = Callable[[], bool]


@dataclass(frozen=True)
class EstadoSalud:
    componentes: dict[str, bool] = field(default_factory=dict)

    @property
    def caidos(self) -> list[str]:
        return [n for n, ok in self.componentes.items() if not ok]


def resumir(estado: EstadoSalud, hay_modelo: bool) -> tuple[int, dict]:
    """(código HTTP, cuerpo) de `/health`. Sin modelo: 503."""
    componentes = {**estado.componentes, "modelo": hay_modelo}
    caidos = [n for n, ok in componentes.items() if not ok]
    if not hay_modelo:
        motivo = "Sin modelo cargado todavía"
        otros = [c for c in caidos if c != "modelo"]
        if otros:
            motivo += f"; no disponible: {', '.join(otros)}"
        return 503, {"estado": "degradado", "motivo": motivo, "componentes": componentes}
    if caidos:
        return 200, {
            "estado": "degradado",
            "motivo": f"No disponible: {', '.join(caidos)}",
            "componentes": componentes,
        }
    return 200, {"estado": "ok", "componentes": componentes}


class MonitorSalud:
    def __init__(self, chequeos: dict[str, Chequeo], *, intervalo: int = 10) -> None:
        self.chequeos = chequeos
        self.intervalo = intervalo
        # Hasta el primer chequeo, todo se considera caído (no se reporta "ok" sin verificar).
        self._estado = EstadoSalud({n: False for n in chequeos})
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None

    @property
    def estado(self) -> EstadoSalud:
        return self._estado

    def revisar(self) -> EstadoSalud:
        resultado: dict[str, bool] = {}
        for nombre, chequeo in self.chequeos.items():
            try:
                resultado[nombre] = bool(chequeo())
            except Exception:  # noqa: BLE001 — un chequeo que falla cuenta como caído
                log.debug("Chequeo %s falló", nombre, exc_info=True)
                resultado[nombre] = False
        self._estado = EstadoSalud(resultado)
        return self._estado

    def arrancar(self) -> None:
        self._hilo = threading.Thread(target=self._bucle, name="monitor-salud", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()
        if self._hilo is not None:
            self._hilo.join(timeout=5)

    def _bucle(self) -> None:
        self.revisar()
        while not self._detener.wait(self.intervalo):
            self.revisar()
