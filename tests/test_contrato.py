"""Propiedades del contrato de salida y de los modelos canónicos de entrada (Anexo)."""

from __future__ import annotations

import json
import math
from datetime import date

import pytest
from pydantic import ValidationError

from novamercado.contrato import (
    Calidad,
    ErrorRespuesta,
    FamiliaPromocion,
    Health,
    ModeloInfo,
    ObservacionCalidad,
    ObservacionesResponse,
    PrediccionResponse,
    ReentrenoIniciado,
    ReentrenoStatus,
    SolicitudObservaciones,
    SolicitudPrediccion,
    derivar_estado,
    sanear_prediccion,
)

REPARAR = ObservacionCalidad(campo="familias[2].familia_id", accion="reparar", detalle="x")
ADVERTIR = ObservacionCalidad(campo="familias", accion="advertir", detalle="y")


def _respuesta(**cambios) -> PrediccionResponse:
    base = {
        "tienda_id": 5,
        "fecha": date(2025, 11, 17),
        "version_modelo": "3",
        "predicciones": [sanear_prediccion("abarrotes", 612.4, 639.2)],
        "calidad": Calidad.desde_observaciones([REPARAR]),
    }
    return PrediccionResponse(**(base | cambios))


# --- calidad ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("observaciones", "estado"),
    [
        ([], "ok"),
        ([REPARAR], "corregido"),
        ([REPARAR, ADVERTIR], "advertencia"),
        ([ADVERTIR], "advertencia"),
    ],
)
def test_estado_derivado(observaciones, estado):
    assert derivar_estado(observaciones) == estado
    assert Calidad.desde_observaciones(observaciones).estado == estado


def test_ok_siempre_serializa_lista_vacia():
    datos = json.loads(Calidad(estado="ok").model_dump_json())
    assert datos == {"estado": "ok", "observaciones": []}
    assert json.loads(Calidad.desde_observaciones([]).model_dump_json(exclude_none=True)) == datos


@pytest.mark.parametrize(
    ("estado", "observaciones"),
    [("ok", [REPARAR]), ("corregido", []), ("corregido", [ADVERTIR]), ("advertencia", [REPARAR])],
)
def test_estado_incoherente_se_rechaza(estado, observaciones):
    with pytest.raises(ValidationError):
        Calidad(estado=estado, observaciones=observaciones)


def test_accion_solo_reparar_o_advertir():
    with pytest.raises(ValidationError):
        ObservacionCalidad(campo="x", accion="rechazar", detalle="z")


# --- predicción ---------------------------------------------------------------------------------


def test_respuesta_prediccion_tiene_campos_exactos_del_anexo():
    datos = json.loads(_respuesta().model_dump_json())
    assert list(datos) == ["tienda_id", "fecha", "version_modelo", "predicciones", "calidad"]
    assert datos["fecha"] == "2025-11-17"
    assert set(datos["predicciones"][0]) == {"familia_id", "venta_esperada", "cantidad_sugerida"}
    assert datos["calidad"]["observaciones"][0] == {
        "campo": "familias[2].familia_id",
        "accion": "reparar",
        "detalle": "x",
    }


@pytest.mark.parametrize("version", [3, "3", 3.0])
def test_version_modelo_siempre_texto(version):
    datos = json.loads(_respuesta(version_modelo=version).model_dump_json())
    assert datos["version_modelo"] == "3"


def test_version_modelo_booleana_invalida():
    with pytest.raises(ValidationError):
        _respuesta(version_modelo=True)


@pytest.mark.parametrize("cantidad", [-1, 2.5, True, "3", float("nan")])
def test_cantidad_sugerida_entero_no_negativo(cantidad):
    with pytest.raises(ValidationError):
        _respuesta(
            predicciones=[{"familia_id": "a", "venta_esperada": 1.0, "cantidad_sugerida": cantidad}]
        )


@pytest.mark.parametrize("venta", [float("nan"), float("inf"), -0.5])
def test_venta_esperada_finita_no_negativa(venta):
    with pytest.raises(ValidationError):
        _respuesta(
            predicciones=[{"familia_id": "a", "venta_esperada": venta, "cantidad_sugerida": 1}]
        )


@pytest.mark.parametrize(
    ("venta", "cantidad", "esperado"),
    [
        (612.4, 639.2, (612.4, 640)),
        (float("nan"), float("inf"), (0.0, 0)),
        (-3.0, -1.0, (0.0, 0)),
        (10.0, 10.0, (10.0, 10)),
        (None, "x", (0.0, 0)),
        (0.004, 0.2, (0.0, 1)),
    ],
)
def test_sanear_prediccion(venta, cantidad, esperado):
    p = sanear_prediccion("bebidas", venta, cantidad)
    assert (p.venta_esperada, p.cantidad_sugerida) == esperado
    assert isinstance(p.cantidad_sugerida, int) and p.cantidad_sugerida >= 0
    assert math.isfinite(p.venta_esperada)


def test_tienda_id_entero_estricto_en_salida():
    with pytest.raises(ValidationError):
        _respuesta(tienda_id="5")


# --- observaciones, reentreno, modelo, health -------------------------------------------------


def test_observaciones_response():
    r = ObservacionesResponse(registros_guardados=0, calidad=Calidad(estado="ok"))
    assert json.loads(r.model_dump_json()) == {
        "registros_guardados": 0,
        "calidad": {"estado": "ok", "observaciones": []},
    }
    with pytest.raises(ValidationError):
        ObservacionesResponse(registros_guardados=-1, calidad=Calidad(estado="ok"))


def test_reentreno_en_curso_omite_campos_de_cierre():
    assert json.loads(ReentrenoStatus(estado="en_curso").model_dump_json()) == {
        "estado": "en_curso"
    }


@pytest.mark.parametrize("estado", ["exitoso", "fallido"])
def test_reentreno_terminado_incluye_campos(estado):
    r = ReentrenoStatus(estado=estado, promovido=False, version=4, motivo="Costo 1 vs 2")
    datos = json.loads(r.model_dump_json())
    assert datos == {"estado": estado, "promovido": False, "version": "4", "motivo": "Costo 1 vs 2"}


def test_reentreno_terminado_sin_motivo_es_invalido():
    with pytest.raises(ValidationError):
        ReentrenoStatus(estado="exitoso")
    with pytest.raises(ValidationError):
        ReentrenoStatus(estado="fallido", promovido=True, version="3", motivo="x")


def test_reentreno_iniciado_y_modelo_como_texto():
    assert ReentrenoIniciado(id=17).model_dump() == {"id": "17"}
    assert ModeloInfo(nombre="m", alias="champion", version=2).model_dump() == {
        "nombre": "m",
        "alias": "champion",
        "version": "2",
    }


def test_health():
    assert json.loads(Health(estado="ok").model_dump_json()) == {"estado": "ok"}
    h = Health(estado="degradado", motivo="MLflow no responde", componentes={"mlflow": "caido"})
    assert json.loads(h.model_dump_json())["motivo"] == "MLflow no responde"
    with pytest.raises(ValidationError):
        Health(estado="degradado")
    with pytest.raises(ValidationError):
        Health(estado="caido", motivo="x")


def test_error_respuesta():
    assert ErrorRespuesta(detail="motivo").model_dump() == {"detail": "motivo"}


# --- modelos canónicos de entrada ---------------------------------------------------------------


@pytest.mark.parametrize("valor", [True, False, "3", 2.0, -1, 2**31])
def test_entrada_canonica_bloquea_conversiones_laxas(valor):
    with pytest.raises(ValidationError):
        FamiliaPromocion(familia_id="abarrotes", en_promocion=valor)


def test_entrada_canonica_ignora_extras_y_exige_familias():
    s = SolicitudPrediccion.model_validate(
        {
            "tienda_id": 5,
            "fecha": date(2025, 11, 17),
            "extra": 1,
            "familias": [{"familia_id": "abarrotes", "en_promocion": 0, "otro": "x"}],
        }
    )
    assert s.familias[0].en_promocion == 0
    with pytest.raises(ValidationError):
        SolicitudPrediccion(tienda_id=5, fecha=date(2025, 11, 17), familias=[])
    with pytest.raises(ValidationError):
        SolicitudObservaciones(tienda_id=True, fecha=date(2025, 11, 17), ventas=[])
