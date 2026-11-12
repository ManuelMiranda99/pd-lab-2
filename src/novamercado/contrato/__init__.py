"""Modelos Pydantic de entrada (forma canónica, estricta) y salida (exacta al Anexo)."""

from novamercado.contrato.entrada import (
    LIMITE_ENTERO,
    FamiliaPromocion,
    SolicitudObservaciones,
    SolicitudPrediccion,
    VentaFamilia,
)
from novamercado.contrato.salida import (
    Calidad,
    ErrorRespuesta,
    Health,
    ModeloInfo,
    ObservacionCalidad,
    ObservacionesResponse,
    PrediccionFamilia,
    PrediccionResponse,
    ReentrenoIniciado,
    ReentrenoStatus,
    derivar_estado,
    sanear_prediccion,
)

__all__ = [
    "LIMITE_ENTERO",
    "Calidad",
    "ErrorRespuesta",
    "FamiliaPromocion",
    "Health",
    "ModeloInfo",
    "ObservacionCalidad",
    "ObservacionesResponse",
    "PrediccionFamilia",
    "PrediccionResponse",
    "ReentrenoIniciado",
    "ReentrenoStatus",
    "SolicitudObservaciones",
    "SolicitudPrediccion",
    "VentaFamilia",
    "derivar_estado",
    "sanear_prediccion",
]
