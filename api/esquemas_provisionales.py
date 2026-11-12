"""Modelos Pydantic PROVISIONALES y mínimos del contrato (Anexo del PDF).

⚠ Provisionales: se reemplazan por `novamercado.contrato` en la Fase 4 (entrada tolerante con
limpieza y observaciones de calidad). Aquí la entrada es estricta y la calidad siempre es `ok`.
Los nombres de campos son los exactos del Anexo.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field


class FamiliaSolicitud(BaseModel):
    familia_id: str
    en_promocion: int = Field(ge=0)


class SolicitudPrediccion(BaseModel):
    tienda_id: int
    fecha: date
    familias: list[FamiliaSolicitud] = Field(min_length=1)


class PrediccionFamilia(BaseModel):
    familia_id: str
    venta_esperada: float
    cantidad_sugerida: int = Field(ge=0)


class ObservacionCalidad(BaseModel):
    campo: str
    accion: Literal["reparar", "advertir"]
    detalle: str


class Calidad(BaseModel):
    estado: Literal["ok", "corregido", "advertencia"]
    observaciones: list[ObservacionCalidad]


class RespuestaPrediccion(BaseModel):
    tienda_id: int
    fecha: str
    version_modelo: str
    predicciones: list[PrediccionFamilia]
    calidad: Calidad


class RespuestaReentreno(BaseModel):
    id: str


class EstadoReentreno(BaseModel):
    estado: Literal["en_curso", "exitoso", "fallido"]
    promovido: bool | None = None
    version: str | None = None
    motivo: str | None = None


class ModeloInfo(BaseModel):
    nombre: str
    alias: str
    version: str
