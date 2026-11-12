"""Motor de calidad: hallazgos, rechazos y construcción del bloque ``calidad``.

Los normalizadores devuelven *hallazgos* (código + campo + datos). La acción y el mensaje salen
siempre de la tabla de :mod:`novamercado.limpieza.politicas`; aquí solo se traducen a
``ObservacionCalidad`` o a un rechazo 422.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from novamercado.contrato.salida import Calidad, ObservacionCalidad
from novamercado.limpieza.politicas import Accion, politica


def mostrar(valor: Any, largo: int = 40) -> str:
    """Representación corta y legible de un valor recibido (para mensajes)."""
    if isinstance(valor, str):
        texto = valor
    elif valor is None:
        texto = "null"
    elif isinstance(valor, bool):
        texto = "true" if valor else "false"
    else:
        texto = str(valor)
    return texto if len(texto) <= largo else texto[: largo - 1] + "…"


@dataclass(frozen=True)
class Hallazgo:
    """Problema detectado en un campo concreto de la solicitud."""

    codigo: str
    campo: str
    datos: dict[str, Any] = field(default_factory=dict)

    @property
    def accion(self) -> Accion:
        """Acción que la tabla de políticas asigna a este problema."""
        return politica(self.codigo).accion

    @property
    def detalle(self) -> str:
        """Mensaje legible generado con la plantilla de la tabla."""
        return politica(self.codigo).formatear(**self.datos)

    def a_observacion(self) -> ObservacionCalidad | None:
        """Observación para ``calidad``; ``None`` si la política es ``ignorar``."""
        accion = self.accion
        if accion is Accion.IGNORAR:
            return None
        if accion is Accion.RECHAZAR:
            raise ValueError(f"{self.codigo} es un rechazo, no una observación")
        return ObservacionCalidad(campo=self.campo, accion=accion.value, detalle=self.detalle)


class SolicitudRechazada(Exception):  # noqa: N818 - nombre del dominio
    """Rechazo de la solicitud: la API responde 422 con ``detail``."""

    def __init__(self, codigo: str, campo: str, **datos: Any) -> None:
        pol = politica(codigo)
        if pol.accion is not Accion.RECHAZAR:
            raise ValueError(f"{codigo} no es una política de rechazo")
        self.codigo = codigo
        self.campo = campo
        self.datos = datos
        self.detail = f"{campo}: {pol.formatear(**datos)}" if campo else pol.formatear(**datos)
        super().__init__(self.detail)


def construir_calidad(hallazgos: Iterable[Hallazgo]) -> Calidad:
    """Convierte los hallazgos en el bloque ``calidad`` con estado derivado."""
    observaciones = [o for h in hallazgos if (o := h.a_observacion()) is not None]
    return Calidad.desde_observaciones(observaciones)
