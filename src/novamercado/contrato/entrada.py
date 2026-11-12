"""Modelos Pydantic de **entrada** en su forma canónica (ya limpia).

La tolerancia no está en estos modelos sino en ``novamercado.limpieza``: el cuerpo crudo pasa
por la lectura tolerante y los normalizadores, y el resultado se valida aquí en modo estricto
como última barrera (un booleano nunca se convierte en número, no hay NaN, los enteros caben en
un ``integer`` de PostgreSQL). Las claves desconocidas se ignoran (``extra="ignore"``).

Los modelos también sirven para documentar el contrato en OpenAPI.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

#: Máximo de un ``integer`` de PostgreSQL; cualquier entero mayor se trata como fuera de dominio.
LIMITE_ENTERO = 2_147_483_647

IdTienda = Annotated[int, Field(strict=True, ge=1, le=LIMITE_ENTERO)]
Conteo = Annotated[int, Field(strict=True, ge=0, le=LIMITE_ENTERO)]
IdFamilia = Annotated[str, Field(strict=True, min_length=1, max_length=64)]


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, frozen=True)


class FamiliaPromocion(_Entrada):
    """Familia de una solicitud de predicción."""

    familia_id: IdFamilia
    en_promocion: Conteo


class SolicitudPrediccion(_Entrada):
    """Cuerpo canónico de ``POST /v1/prediccion``."""

    tienda_id: IdTienda
    fecha: date
    familias: Annotated[list[FamiliaPromocion], Field(min_length=1)]


class VentaFamilia(_Entrada):
    """Venta real de una familia."""

    familia_id: IdFamilia
    ventas: Conteo


class SolicitudObservaciones(_Entrada):
    """Cuerpo canónico de ``POST /v1/observaciones`` (solo las familias que se guardan)."""

    tienda_id: IdTienda
    fecha: date
    ventas: list[VentaFamilia]
