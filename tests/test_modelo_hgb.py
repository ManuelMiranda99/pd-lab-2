"""Modelo ML (HGB Poisson + cuantil por familia), validación con origen móvil y criterio §4.2.

Datos sintéticos propios (sin datos publicados) y sin servidor de MLflow.
"""

from __future__ import annotations

import time

import mlflow.pyfunc
import numpy as np
import pandas as pd
import pytest

from ayudas_esqueleto import COSTOS, FAMILIAS
from novamercado.features.variables import Contexto
from novamercado.modelo import hgb
from novamercado.modelo.baseline import ajustar_baseline
from novamercado.modelo.criterio import Comparacion, decidir_reemplazo
from novamercado.modelo.hgb import RecetaHGB, calibrar_factores, tau_por_familia
from novamercado.modelo.metricas import costo_total
from novamercado.modelo.pyfunc import armar_entrada, entrada_de_humo, guardar_local, validar_salida
from novamercado.modelo.validacion import (
    entrenar_y_evaluar,
    prediccion_rodante,
    ventanas_evaluacion,
)

INICIO = pd.Timestamp("2025-01-06")
DIAS = 200


@pytest.fixture(autouse=True)
def hgb_rapido(monkeypatch):
    """Menos iteraciones para que la suite siga siendo rápida (la receta es la misma)."""
    monkeypatch.setitem(hgb.HIPERPARAMETROS, "max_iter", 60)
    monkeypatch.setitem(hgb.HIPERPARAMETROS, "learning_rate", 0.15)


def _ctx() -> Contexto:
    tiendas = pd.DataFrame(
        {
            "tienda_id": [1, 2],
            "ciudad": ["Guatemala", "Mixco"],
            "tipo": ["hipermercado", "express"],
            "fecha_apertura": ["2019-01-01", "2019-01-01"],
        }
    )
    feriados = pd.DataFrame(
        {
            "fecha": ["2025-04-18"],
            "nombre": ["Viernes Santo"],
            "tipo": ["feriado"],
            "ambito": ["nacional"],
        }
    )
    return Contexto.desde_tablas(tiendas, feriados, FAMILIAS)


def historia_sintetica(dias: int = DIAS, semilla: int = 3, *, salto: float = 1.0) -> pd.DataFrame:
    """Nivel por tienda y familia, fin de semana ×1.3, día 15 ×1.2, promoción +4 % por producto.

    `salto` multiplica la demanda de la última cuarta parte (cambio de demanda).
    """
    rng = np.random.default_rng(semilla)
    fechas = pd.date_range(INICIO, periods=dias, freq="D")
    filas = []
    for t in (1, 2):
        for i, fam in enumerate(FAMILIAS):
            base = 60 * (i + 1) * (2 if t == 1 else 1)
            for k, f in enumerate(fechas):
                promo = int(rng.choice([0, 0, 1, 3, 6]))
                media = base * (1.3 if f.dayofweek >= 5 else 1.0) * (1.2 if f.day == 15 else 1.0)
                media *= 1.04**promo * (salto if k >= dias * 3 // 4 else 1.0)
                filas.append((t, f, fam, float(rng.poisson(media)), promo))
    return pd.DataFrame(
        filas, columns=["tienda_id", "fecha", "familia_id", "ventas", "en_promocion"]
    )


@pytest.fixture(scope="module")
def historia() -> pd.DataFrame:
    return historia_sintetica()


def _sol(h: pd.DataFrame, d: pd.Timestamp) -> pd.DataFrame:
    return h[h["fecha"] == d][["tienda_id", "fecha", "familia_id", "en_promocion"]]


# ------------------------------------------------------------------------- cuantil por familia


def test_tau_y_cuantil_del_ratio_siguen_al_costo():
    tau = tau_por_familia(COSTOS)
    assert tau["abarrotes"] == pytest.approx(1 / 1.2)
    assert tau["panaderia"] == pytest.approx(1 / 3.5)
    rng = np.random.default_rng(0)
    real = rng.poisson(100, 3000).astype(float)
    pred = np.full(3000, 100.0)
    fam = pd.Series(np.repeat(FAMILIAS, 1000))
    q = calibrar_factores(real, pred, fam, COSTOS)
    assert q["abarrotes"] > 1.0 > q["panaderia"]
    assert q["abarrotes"] == pytest.approx(np.quantile(real[:1000] / 100, tau["abarrotes"]))


def test_cuantil_ignora_familias_con_pocas_filas_y_predicciones_cero():
    q = calibrar_factores(
        np.array([10.0, 12.0, 5.0]),
        np.array([10.0, 0.0, 5.0]),
        pd.Series(["abarrotes", "abarrotes", "lacteos"]),
        COSTOS,
    )
    assert q == {}


# ------------------------------------------------------------------------------- el modelo


def test_modelo_predice_contrato_en_orden_y_sin_fuga(historia):
    ctx = _ctx()
    receta = RecetaHGB(historia, ctx, COSTOS)
    d = historia["fecha"].max()
    modelo = receta.modelo(d)
    sol = _sol(historia, d).iloc[::-1]
    panel = historia[historia["fecha"] < d]
    out = modelo.predecir(sol, panel)
    assert list(out["familia_id"]) == list(sol["familia_id"])
    assert list(out["tienda_id"]) == list(sol["tienda_id"])
    validar_salida(out, len(sol))
    assert (out["venta_esperada"] > 0).all()
    # La venta del propio día (o posterior) no cambia la predicción.
    sucio = pd.concat([panel, historia[historia["fecha"] >= d].assign(ventas=1e6)])
    pd.testing.assert_frame_equal(out, modelo.predecir(sol, sucio))
    # Más promoción → más venta esperada (efecto monótono aprendido).
    alta = modelo.predecir(sol.assign(en_promocion=12), panel)["venta_esperada"].sum()
    baja = modelo.predecir(sol.assign(en_promocion=0), panel)["venta_esperada"].sum()
    assert alta > baja


def test_cantidad_sugerida_es_prediccion_por_q_f_hacia_arriba(historia):
    modelo = RecetaHGB(historia, _ctx(), COSTOS).modelo(historia["fecha"].max())
    assert modelo.factores["abarrotes"] > modelo.factores["panaderia"]
    d = historia["fecha"].max()
    out = modelo.predecir(_sol(historia, d), historia[historia["fecha"] < d])
    q = out["familia_id"].map(modelo.factores)
    esperado = np.ceil(out["venta_esperada"].astype(float) * q - 0.01)
    assert (out["cantidad_sugerida"] - esperado).abs().max() <= 1


def test_corte_del_modelo_y_filas_de_entrenamiento(historia):
    receta = RecetaHGB(historia, _ctx(), COSTOS)
    corte = INICIO + pd.Timedelta(days=150)
    assert receta.modelo(corte).fecha_corte == corte
    with pytest.raises(ValueError):
        receta.modelo(INICIO)


def test_supera_al_baseline_en_costo_y_reduce_costo_frente_a_sugerir_la_prediccion(historia):
    ctx = _ctx()
    corte = historia["fecha"].max() - pd.Timedelta(days=20)
    dias = sorted(historia.loc[historia["fecha"] >= corte, "fecha"].unique())
    modelo = RecetaHGB(historia, ctx, COSTOS).modelo(corte)
    p = prediccion_rodante(modelo, historia, dias)
    pb = prediccion_rodante(ajustar_baseline(historia, COSTOS, antes_de=corte), historia, dias)
    assert costo_total(p, COSTOS) < costo_total(pb, COSTOS)
    sin_q = p.assign(cantidad_sugerida=np.ceil(p["venta_esperada"].astype(float)))
    assert costo_total(p, COSTOS) < costo_total(sin_q, COSTOS)


def test_robustez_sin_lags_no_es_peor_que_el_baseline_sin_panel(historia):
    """F5.6: sin ningún dato reciente, el respaldo de calendario no es peor que el baseline."""
    ctx = _ctx()
    corte = historia["fecha"].max() - pd.Timedelta(days=13)
    dias = sorted(historia.loc[historia["fecha"] >= corte, "fecha"].unique())
    modelo = RecetaHGB(historia, ctx, COSTOS).modelo(corte)
    base = ajustar_baseline(historia, COSTOS, antes_de=corte)
    reales = historia[historia["fecha"].isin(dias)]
    sol = reales[["tienda_id", "fecha", "familia_id", "en_promocion"]]
    vacio = historia.iloc[:0]
    p = modelo.predecir(sol, vacio).assign(ventas=reales["ventas"].to_numpy())
    pb = base.predecir(sol, vacio).assign(ventas=reales["ventas"].to_numpy())
    assert np.isfinite(p["venta_esperada"].astype(float)).all()
    assert costo_total(p, COSTOS) <= costo_total(pb, COSTOS) * 1.05


def test_pyfunc_con_modelo_hgb_y_humo(historia, tmp_path):
    modelo = RecetaHGB(historia, _ctx(), COSTOS).modelo(historia["fecha"].max())
    guardar_local(modelo, tmp_path / "m")
    cargado = mlflow.pyfunc.load_model(str(tmp_path / "m"))
    d = historia["fecha"].max()
    panel = historia[historia["fecha"] < d]
    salida = cargado.predict(armar_entrada(_sol(historia, d), panel))
    validar_salida(salida, len(_sol(historia, d)))
    validar_salida(cargado.predict(entrada_de_humo()), 2)  # familias desconocidas: no falla


# ----------------------------------------------------------------- validación con origen móvil


def test_ventanas_despues_del_corte_ultimos_21_en_bloques_de_7():
    fechas = list(pd.date_range("2026-01-01", periods=40, freq="D"))
    v = ventanas_evaluacion(fechas, None)
    assert [len(x) for x in v] == [7, 7, 7] and v[-1][-1] == fechas[-1]
    # Solo ventanas completas, contadas desde el final: de 10 días nuevos quedan los últimos 7.
    v = ventanas_evaluacion(fechas, fechas[-10])
    assert [len(x) for x in v] == [7] and v[0][0] == fechas[-7] and v[0][-1] == fechas[-1]
    v = ventanas_evaluacion(fechas, fechas[-16])
    assert [len(x) for x in v] == [7, 7] and v[0][0] == fechas[-14]
    assert ventanas_evaluacion(fechas, fechas[-6]) == []  # 6 días: ninguna ventana completa
    assert ventanas_evaluacion(fechas, fechas[-1] + pd.Timedelta(days=1)) == []
    con_hueco = fechas[:30] + fechas[35:]  # faltan días: solo cuentan los días con dato
    v = ventanas_evaluacion(con_hueco, fechas[25])
    assert sum(len(x) for x in v) == 7


def test_arranque_en_frio_evalua_contra_baseline_y_promueve(historia):
    res = entrenar_y_evaluar(historia, _ctx(), COSTOS)
    comp = res.comparacion
    assert comp.vigente is None and len(comp.retador) == 3 and comp.dias == 21
    assert res.modelo.fecha_corte == historia["fecha"].max() + pd.Timedelta(days=1)
    assert {"mae", "rmse", "wape", "sesgo"} <= set(res.tecnicas)
    d = decidir_reemplazo(comp)
    assert d.promover and "del baseline" in d.motivo


def test_vigente_se_evalua_solo_despues_de_su_corte(historia):
    ctx = _ctx()
    corte = historia["fecha"].max() - pd.Timedelta(days=15)
    vigente = RecetaHGB(historia[historia["fecha"] < corte], ctx, COSTOS).modelo(corte)
    res = entrenar_y_evaluar(historia, ctx, COSTOS, vigente=vigente, corte_vigente=corte)
    assert res.comparacion.dias == 14 and [v["dias"] for v in res.ventanas] == [7, 7]
    assert res.ventanas[0]["inicio"] == str((corte + pd.Timedelta(days=2)).date())
    assert all("costo_vigente" in v for v in res.ventanas)


def test_sin_dias_nuevos_no_se_promueve(historia):
    ctx = _ctx()
    fin = historia["fecha"].max() + pd.Timedelta(days=1)
    vigente = RecetaHGB(historia, ctx, COSTOS).modelo(fin)
    res = entrenar_y_evaluar(historia, ctx, COSTOS, vigente=vigente, corte_vigente=fin)
    d = decidir_reemplazo(res.comparacion)
    assert not d.promover and "0 días evaluables" in d.motivo


def test_cambio_de_demanda_el_retador_reentrenado_gana_al_vigente_congelado():
    ctx = _ctx()
    h = historia_sintetica(salto=1.6)
    corte = h["fecha"].min() + pd.Timedelta(days=DIAS * 3 // 4)  # justo antes del salto
    vigente = RecetaHGB(h[h["fecha"] < corte], ctx, COSTOS).modelo(corte)
    res = entrenar_y_evaluar(h, ctx, COSTOS, vigente=vigente, corte_vigente=corte)
    d = decidir_reemplazo(res.comparacion)
    assert d.promover, d.motivo


def test_tiempo_de_entrenamiento_acotado(historia):
    """F5.9 (escala de prueba): la evaluación completa hace pocos ajustes."""
    t0 = time.perf_counter()
    entrenar_y_evaluar(historia, _ctx(), COSTOS)
    assert time.perf_counter() - t0 < 60


# ----------------------------------------------------------------------- criterio de reemplazo


@pytest.mark.parametrize(
    ("retador", "vigente", "baseline", "dias", "promueve", "texto"),
    [
        ([90, 90, 90], [100, 100, 100], [200, 200, 200], 21, True, "gana 3/3 ventanas): promovido"),
        ([99.6, 99.6], [100, 100], [200, 200], 14, False, "mejora menor a 0.5 %"),
        ([99.5, 99.5], [100, 100], [200, 200], 14, True, "promovido"),
        ([80, 101, 101], [100, 100, 100], [200, 200, 200], 21, False, "menos de la mitad"),
        ([80, 101], [100, 100], [200, 200], 14, True, "gana 1/2"),
        ([90, 90], [100, 100], [85, 85], 14, False, "no mejora al baseline"),
        ([90], [100], [200], 6, False, "Solo 6 días evaluables"),
        ([], [], [], 0, False, "Solo 0 días"),
    ],
)
def test_criterio_de_reemplazo(retador, vigente, baseline, dias, promueve, texto):
    comp = Comparacion(retador=retador, baseline=baseline, vigente=vigente, dias=dias)
    d = decidir_reemplazo(comp)
    assert d.promover is promueve
    assert texto in d.motivo


def test_criterio_sin_vigente_solo_exige_mejorar_al_baseline():
    assert decidir_reemplazo(Comparacion(retador=[90], baseline=[100], dias=7)).promover
    assert not decidir_reemplazo(Comparacion(retador=[110], baseline=[100], dias=7)).promover
    assert not decidir_reemplazo(Comparacion(retador=[], baseline=[], dias=0)).promover


def test_motivo_legible_con_ambos_costos_y_umbral_configurable():
    comp = Comparacion(
        retador=[83948.0, 83948.0],
        baseline=[134979.5, 134979.5],
        vigente=[84566.0, 84566.0],
        dias=14,
    )
    d = decidir_reemplazo(comp)
    assert d.motivo == (
        "Costo en validación 167 896 vs 169 132 del vigente (−0.7 %, gana 2/2 ventanas): promovido"
    )
    assert not decidir_reemplazo(comp, mejora_minima=0.01).promover
    corto = Comparacion(retador=[1.0], baseline=[2.0], vigente=[3.0], dias=7)
    assert "mínimo 14" in decidir_reemplazo(corto).motivo  # una sola ventana no alcanza


def test_pesos_recencia_desde_el_corte_y_sin_ponderar_por_defecto():
    import pandas as pd

    from novamercado.modelo.hgb import VIDA_MEDIA_DIAS, pesos_recencia

    fechas = pd.Series(pd.to_datetime(["2026-01-31", "2025-02-01", "2026-02-05"]))
    corte = pd.Timestamp("2026-02-01")
    pesos = pesos_recencia(fechas, corte, 365)
    assert pesos is not None
    assert pesos[0] == pytest.approx(0.5 ** (1 / 365))
    assert pesos[1] == pytest.approx(0.5)
    assert pesos[2] == 1.0  # fechas posteriores al corte (no se usan al entrenar) no pesan más
    assert pesos_recencia(fechas, corte, None) is None
    assert VIDA_MEDIA_DIAS is None  # DECISIONES §4.4: no ponderar
