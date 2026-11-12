"""Modelos Pydantic de **salida**, exactos al Anexo del enunciado.

Reglas que garantizan estos modelos (y que las pruebas verifican):

- ``calidad.estado`` se deriva de las acciones: algún ``advertir`` → ``advertencia``; solo
  ``reparar`` → ``corregido``; ninguna → ``ok`` con ``"observaciones": []``.
- ``cantidad_sugerida`` es entero estricto ≥ 0; ``venta_esperada`` es finita y ≥ 0.
- ``version_modelo`` y ``version`` se serializan siempre como texto.
- ``GET /admin/reentrenar/{id}``: ``promovido``, ``version`` y ``motivo`` aparecen cuando la
  ejecución terminó y se omiten mientras está ``en_curso``.

No se usa ``exclude_none`` global: la omisión de campos está acotada a cada modelo.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

AccionCalidad = Literal["reparar", "advertir"]
EstadoCalidad = Literal["ok", "corregido", "advertencia"]


def _a_texto(valor: Any) -> Any:
    """Convierte números de versión a texto (``3`` → ``"3"``); deja pasar lo demás."""
    if isinstance(valor, bool):
        return valor  # que falle la validación: un booleano no es una versión
    if isinstance(valor, int | float):
        return str(int(valor)) if float(valor).is_integer() else str(valor)
    return valor


Texto = Annotated[str, BeforeValidator(_a_texto)]
EnteroNoNegativo = Annotated[int, Field(strict=True, ge=0)]
RealFinitoNoNegativo = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class _Salida(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservacionCalidad(_Salida):
    """Una corrección o supuesto aplicado a la solicitud."""

    campo: str
    accion: AccionCalidad
    detalle: str


def derivar_estado(observaciones: list[ObservacionCalidad]) -> EstadoCalidad:
    """Estado de calidad derivado de las acciones (nunca se fija a mano)."""
    acciones = {o.accion for o in observaciones}
    if "advertir" in acciones:
        return "advertencia"
    if "reparar" in acciones:
        return "corregido"
    return "ok"


class Calidad(_Salida):
    """Bloque ``calidad`` de las respuestas de predicción y observaciones."""

    estado: EstadoCalidad
    observaciones: list[ObservacionCalidad] = Field(default_factory=list)

    @model_validator(mode="after")
    def _estado_coherente(self) -> Calidad:
        esperado = derivar_estado(self.observaciones)
        if self.estado != esperado:
            raise ValueError(
                f"estado '{self.estado}' incoherente con las observaciones (debe ser '{esperado}')"
            )
        return self

    @classmethod
    def desde_observaciones(cls, observaciones: list[ObservacionCalidad]) -> Calidad:
        """Construye la calidad derivando el estado de las observaciones."""
        return cls(estado=derivar_estado(observaciones), observaciones=list(observaciones))


class PrediccionFamilia(_Salida):
    """Predicción de una familia."""

    familia_id: str
    venta_esperada: RealFinitoNoNegativo
    cantidad_sugerida: EnteroNoNegativo


def sanear_prediccion(
    familia_id: str, venta_esperada: float | None, cantidad_sugerida: float | None
) -> PrediccionFamilia:
    """Lleva la salida cruda de un modelo al contrato: sin NaN/inf, ``ceil`` a entero ≥ 0.

    Un valor no finito o negativo se convierte en 0 para que ninguna salida del modelo termine
    en un 500. ``venta_esperada`` se redondea a 2 decimales.
    """

    def _finito(x: float | None) -> float:
        try:
            v = float(x)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        return v if math.isfinite(v) and v > 0 else 0.0

    venta = round(_finito(venta_esperada), 2)
    cantidad = _finito(cantidad_sugerida)
    return PrediccionFamilia(
        familia_id=familia_id,
        venta_esperada=venta,
        cantidad_sugerida=int(math.ceil(cantidad - 1e-9)) if cantidad > 0 else 0,
    )


class PrediccionResponse(_Salida):
    """Respuesta 200 de ``POST /v1/prediccion``."""

    tienda_id: Annotated[int, Field(strict=True)]
    fecha: date
    version_modelo: Texto
    predicciones: list[PrediccionFamilia]
    calidad: Calidad


class ObservacionesResponse(_Salida):
    """Respuesta 200 de ``POST /v1/observaciones``."""

    registros_guardados: EnteroNoNegativo
    calidad: Calidad


class ReentrenoIniciado(_Salida):
    """Respuesta 202 de ``POST /admin/reentrenar``."""

    id: Texto


class ReentrenoStatus(_Salida):
    """Respuesta de ``GET /admin/reentrenar/{id}``."""

    estado: Literal["en_curso", "exitoso", "fallido"]
    promovido: bool | None = None
    version: Texto | None = None
    motivo: str | None = None

    @model_validator(mode="after")
    def _campos_de_cierre(self) -> ReentrenoStatus:
        if self.estado != "en_curso" and (self.promovido is None or self.motivo is None):
            raise ValueError("una ejecución terminada debe incluir 'promovido' y 'motivo'")
        if self.estado == "fallido" and self.promovido:
            raise ValueError("una ejecución fallida no puede promover un modelo")
        return self

    @model_serializer(mode="wrap")
    def _serializar(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        datos = handler(self)
        if self.estado == "en_curso":
            for clave in ("promovido", "version", "motivo"):
                datos.pop(clave, None)
        return datos


class ModeloInfo(_Salida):
    """Respuesta de ``GET /modelo``: modelo vigente en memoria."""

    nombre: Texto
    alias: Texto
    version: Texto


class Health(BaseModel):
    """Respuesta de ``GET /health``. Admite campos adicionales (p. ej. componentes)."""

    model_config = ConfigDict(extra="allow", frozen=True)

    estado: Literal["ok", "degradado"]
    motivo: str | None = None

    @model_validator(mode="after")
    def _motivo_si_degradado(self) -> Health:
        if self.estado == "degradado" and not self.motivo:
            raise ValueError("'degradado' requiere 'motivo'")
        return self

    @model_serializer(mode="wrap")
    def _serializar(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, Any]:
        datos = handler(self)
        if self.estado == "ok" and datos.get("motivo") is None:
            datos.pop("motivo", None)
        return datos


class ErrorRespuesta(_Salida):
    """Cuerpo de un rechazo 422 (o de un 404/503): ``detail`` legible."""

    detail: str
