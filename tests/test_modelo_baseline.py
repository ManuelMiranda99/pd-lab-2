"""Baseline, features mínimas, métricas y criterio de reemplazo (sin MLflow ni BD)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from ayudas_esqueleto import COSTOS, FAMILIAS, panel_sintetico
from novamercado.features.baseline import media_mismo_dia
from novamercado.modelo.baseline import (
    BaselineDiaSemana,
    ajustar_baseline,
    predicciones_rodantes,
    ultimas_fechas,
)
from novamercado.modelo.criterio import decidir_reemplazo
from novamercado.modelo.metricas import costo_total, metricas_tecnicas


def _solicitud(tienda: int, fecha: str, familias=FAMILIAS) -> pd.DataFrame:
    return pd.DataFrame(
        [(tienda, pd.Timestamp(fecha), f, 0) for f in familias],
        columns=["tienda_id", "fecha", "familia_id", "en_promocion"],
    )


def test_media_mismo_dia_usa_las_ultimas_k_del_mismo_dia_y_solo_fechas_anteriores():
    fechas = pd.date_range("2025-01-06", periods=42, freq="D")  # lunes a domingo, 6 semanas
    panel = pd.DataFrame(
        {"tienda_id": 1, "fecha": fechas, "familia_id": "x", "ventas": range(42)}
    ).astype({"ventas": float})
    objetivo = pd.Timestamp("2025-02-17")  # lunes, justo después de las 6 semanas
    feats = media_mismo_dia(panel, objetivo, k=2)
    # Lunes previos: índices 0, 7, 14, 21, 28, 35 → últimos 2 = 28 y 35.
    assert feats.loc[0, "media_dia"] == pytest.approx((28 + 35) / 2)
    # Una fila en la fecha objetivo (o después) no se usa.
    con_futuro = pd.concat(
        [
            panel,
            pd.DataFrame([{"tienda_id": 1, "fecha": objetivo, "familia_id": "x", "ventas": 1e6}]),
        ]
    )
    assert media_mismo_dia(con_futuro, objetivo, k=2).equals(feats)


def test_prediccion_no_cambia_si_se_altera_la_venta_del_mismo_dia():
    panel = panel_sintetico()
    d = panel["fecha"].max()
    modelo = ajustar_baseline(panel, COSTOS, antes_de=d)
    base = modelo.predecir(_solicitud(1, str(d.date())), panel)
    alterado = panel.copy()
    alterado.loc[alterado["fecha"] == d, "ventas"] = 99_999
    assert modelo.predecir(_solicitud(1, str(d.date())), alterado).equals(base)


def test_salida_entera_no_negativa_y_respaldos_sin_panel():
    modelo = BaselineDiaSemana(
        respaldo={"1|abarrotes": 50.0}, respaldo_familia={"lacteos": 20.0}, factores={}
    )
    vacio = panel_sintetico().iloc[0:0]
    out = modelo.predecir(_solicitud(1, "2026-03-02", ["abarrotes", "lacteos", "nueva"]), vacio)
    assert list(out["venta_esperada"]) == [50.0, 20.0, 0.0]
    assert all(isinstance(c, int) and c >= 0 for c in out["cantidad_sugerida"])


def test_factor_sigue_al_costo_de_faltante():
    panel = panel_sintetico(dias=120)
    modelo = ajustar_baseline(panel, COSTOS)
    # abarrotes: faltar cuesta 5x sobrar → factor mayor que panadería (sobrar cuesta 2.5x).
    assert modelo.factores["abarrotes"] > modelo.factores["panaderia"]
    pred = modelo.predecir(_solicitud(1, "2025-05-10"), panel)
    for v, c in zip(pred["venta_esperada"], pred["cantidad_sugerida"], strict=True):
        assert c >= 0 and math.isfinite(v)


def test_cuantil_reduce_costo_frente_a_sugerir_la_prediccion():
    panel = panel_sintetico(dias=150)
    corte = ultimas_fechas(panel, 28)[0]
    modelo = ajustar_baseline(panel, COSTOS, antes_de=corte)
    pred = predicciones_rodantes(modelo, panel, ultimas_fechas(panel, 28))
    assert costo_total(pred, COSTOS) <= costo_total(pred, COSTOS, columna="venta_esperada")


def test_costo_total_y_metricas():
    df = pd.DataFrame(
        {
            "familia_id": ["abarrotes", "panaderia"],
            "ventas": [10, 10],
            "venta_esperada": [8.0, 12.0],
            "cantidad_sugerida": [8, 12],
        }
    )
    # abarrotes: faltan 2 × 1.0 = 2 ; panadería: sobran 2 × 2.5 = 5
    assert costo_total(df, COSTOS) == pytest.approx(7.0)
    m = metricas_tecnicas(df)
    assert m["mae"] == pytest.approx(2.0) and m["sesgo"] == pytest.approx(0.0)
    assert m["wape"] == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("retador", "vigente", "mejora", "promueve"),
    [
        (100.0, None, 0.0, True),
        (90.0, 100.0, 0.0, True),
        (100.0, 100.0, 0.0, True),
        (101.0, 100.0, 0.0, False),
        (96.0, 100.0, 0.05, False),
        (95.0, 100.0, 0.05, True),
    ],
)
def test_criterio_de_reemplazo(retador, vigente, mejora, promueve):
    d = decidir_reemplazo(retador, vigente, mejora_minima=mejora)
    assert d.promover is promueve
    assert d.motivo


def test_motivo_legible_con_ambos_costos():
    d = decidir_reemplazo(18210.0, 19940.0)
    assert d.motivo == "Costo en validación 18 210 vs 19 940 del vigente"
