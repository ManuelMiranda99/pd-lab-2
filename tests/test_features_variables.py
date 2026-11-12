"""Función única de variables: sin fuga temporal, igual al entrenar y servir, robusta a huecos."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from novamercado.features.calendario import variables_calendario
from novamercado.features.variables import (
    CATEGORICAS,
    FEATURES,
    Contexto,
    variables_entrenamiento,
    variables_prediccion,
)

FAMILIAS = ["abarrotes", "licores"]
INICIO = pd.Timestamp("2025-01-01")
DIAS = 150


def _ctx() -> Contexto:
    tiendas = pd.DataFrame(
        {
            "tienda_id": [1, 2],
            "ciudad": ["Guatemala", "Mixco"],
            "tipo": ["hipermercado", "express"],
            "fecha_apertura": ["2019-01-01", "2025-02-01"],
        }
    )
    feriados = pd.DataFrame(
        {
            "fecha": ["2025-04-17", "2025-04-18", "2025-05-01", "2025-05-02", "2025-05-15"],
            "nombre": ["Jueves Santo", "Viernes Santo", "Día del Trabajo", "Comercial", "Local"],
            "tipo": ["feriado", "feriado", "feriado", "comercial", "feriado"],
            "ambito": ["nacional", "nacional", "nacional", "nacional", "Guatemala"],
        }
    )
    return Contexto.desde_tablas(tiendas, feriados, FAMILIAS)


def _historia(semilla: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(semilla)
    filas = []
    for t in (1, 2):
        desde = INICIO if t == 1 else pd.Timestamp("2025-02-01")
        for f in pd.date_range(desde, periods=DIAS - (desde - INICIO).days, freq="D"):
            for i, fam in enumerate(FAMILIAS):
                filas.append((t, f, fam, float(rng.integers(50, 150) * (i + 1)), int(f.day % 3)))
    return pd.DataFrame(
        filas, columns=["tienda_id", "fecha", "familia_id", "ventas", "en_promocion"]
    )


def _numericas(df: pd.DataFrame) -> pd.DataFrame:
    return df[[c for c in FEATURES if c not in CATEGORICAS]].astype(float)


def _sol(historia: pd.DataFrame, d: pd.Timestamp) -> pd.DataFrame:
    return historia[historia["fecha"] == d][["tienda_id", "fecha", "familia_id", "en_promocion"]]


def test_entrenar_y_servir_dan_las_mismas_variables():
    h, ctx = _historia(), _ctx()
    tr = variables_entrenamiento(h, ctx, enmascarar=False)
    for d in [INICIO + pd.Timedelta(days=k) for k in (40, 120, 149)]:
        panel = h[(h["fecha"] < d) & (h["fecha"] >= d - pd.Timedelta(days=112))]
        vp = variables_prediccion(_sol(h, d), panel, ctx).set_index(["tienda_id", "familia_id"])
        ref = tr[tr["fecha"] == d].set_index(["tienda_id", "familia_id"]).loc[vp.index]
        pd.testing.assert_frame_equal(_numericas(vp), _numericas(ref))
        for c in CATEGORICAS:
            assert list(vp[c].astype(str)) == list(ref[c].astype(str))


def test_sin_fuga_la_venta_del_dia_y_las_posteriores_no_cambian_las_variables():
    h, ctx = _historia(), _ctx()
    d = INICIO + pd.Timedelta(days=100)
    alterada = h.copy()
    alterada.loc[alterada["fecha"] >= d, "ventas"] = 99_999.0
    a = variables_entrenamiento(h, ctx, enmascarar=False)
    b = variables_entrenamiento(alterada, ctx, enmascarar=False)
    fa, fb = a[a["fecha"] == d], b[b["fecha"] == d]
    pd.testing.assert_frame_equal(_numericas(fa), _numericas(fb))
    # Al servir, una fila del panel con la misma fecha objetivo se ignora.
    panel = h[h["fecha"] < d]
    sucio = pd.concat([panel, h[h["fecha"] >= d].assign(ventas=99_999.0)])
    pd.testing.assert_frame_equal(
        _numericas(variables_prediccion(_sol(h, d), panel, ctx)),
        _numericas(variables_prediccion(_sol(h, d), sucio, ctx)),
    )


def test_lags_a_la_fecha_del_ultimo_dato_con_huecos():
    h, ctx = _historia(), _ctx()
    d = INICIO + pd.Timedelta(days=120)  # un día cualquiera
    serie = (h["tienda_id"] == 1) & (h["familia_id"] == "abarrotes")
    # Faltan los 3 días previos y el mismo día de la semana de hace 1 y 2 semanas.
    quitar = [d - pd.Timedelta(days=k) for k in (1, 2, 3, 7, 14)]
    panel = h[(h["fecha"] < d) & ~(serie & h["fecha"].isin(quitar))]
    vp = variables_prediccion(_sol(h, d), panel, ctx)
    fila = vp[(vp["tienda_id"] == 1) & (vp["familia_id"] == "abarrotes")].iloc[0]
    v = h[serie].set_index("fecha")["ventas"]
    assert fila["dias_desde_ultimo_dato"] == 4
    assert fila["ultimo_dato"] == v[d - pd.Timedelta(days=4)]
    assert np.isnan(fila["lag_1"]) and np.isnan(fila["lag_7"])
    assert fila["ultimo_mismo_dia"] == v[d - pd.Timedelta(days=21)]
    esperado = np.mean([v[d - pd.Timedelta(days=7 * k)] for k in (3, 4, 5, 6)])
    assert fila["media_mismo_dia_4"] == pytest.approx(esperado)
    previos = [d - pd.Timedelta(days=k) for k in range(4, 7)]  # el día −7 también falta
    assert fila["media_7"] == pytest.approx(v[previos].mean())


def test_sin_panel_las_variables_de_nivel_quedan_vacias_pero_hay_fila():
    h, ctx = _historia(), _ctx()
    d = INICIO + pd.Timedelta(days=60)
    vp = variables_prediccion(_sol(h, d), h.iloc[:0], ctx)
    assert len(vp) == len(_sol(h, d))
    assert vp["ultimo_dato"].isna().all() and vp["media_28"].isna().all()
    assert vp["dia_mes"].notna().all()


def test_cierre_de_tienda_es_dato_faltante_no_venta_cero():
    h, ctx = _historia(), _ctx()
    cierre = [INICIO + pd.Timedelta(days=k) for k in range(50, 57)]
    h.loc[(h["tienda_id"] == 1) & h["fecha"].isin(cierre), "ventas"] = 0.0
    tr = variables_entrenamiento(h, ctx, enmascarar=False)
    t1 = tr[tr["tienda_id"] == 1]
    assert not t1["fecha"].isin(cierre).any()  # fuera del objetivo
    despues = t1[t1["fecha"] == cierre[-1] + pd.Timedelta(days=1)].iloc[0]
    assert despues["dias_desde_ultimo_dato"] == 8
    assert despues["ultimo_dato"] > 0 and np.isnan(despues["media_7"])
    # Una sola familia en 0 es un cero real (no cierre).
    h2 = _historia()
    h2.loc[(h2["tienda_id"] == 1) & (h2["familia_id"] == "licores"), "ventas"] = 0.0
    tr2 = variables_entrenamiento(h2, ctx, enmascarar=False)
    assert (tr2[(tr2["tienda_id"] == 1) & (tr2["familia_id"] == "licores")]["ventas"] == 0).any()


def test_dias_antes_de_la_apertura_no_entran_y_dias_desde_apertura_con_tope():
    h, ctx = _historia(), _ctx()
    previo = pd.DataFrame(
        [(2, INICIO, f, 10.0, 0) for f in FAMILIAS], columns=h.columns
    )  # tienda 2 abre el 2025-02-01
    tr = variables_entrenamiento(pd.concat([h, previo]), ctx, enmascarar=False)
    t2 = tr[tr["tienda_id"] == 2]
    assert t2["fecha"].min() == pd.Timestamp("2025-02-01")
    assert t2["dias_desde_apertura"].min() == 0
    assert tr["dias_desde_apertura"].max() == 400


def test_promocion_desconocida_queda_nan_con_indicador():
    h, ctx = _historia(), _ctx()
    h["en_promocion"] = h["en_promocion"].astype(float)
    h.loc[h.index[::5], "en_promocion"] = np.nan
    tr = variables_entrenamiento(h, ctx, enmascarar=False)
    assert (tr["promo_desconocida"] == tr["en_promocion"].isna().astype(float)).all()
    assert tr["promo_desconocida"].sum() > 0
    # Sin columna de promoción en el panel: la media de promoción queda vacía, sin error.
    d = INICIO + pd.Timedelta(days=90)
    sol = _sol(h, d).fillna({"en_promocion": 1})  # al servir la promoción siempre viene
    vp = variables_prediccion(sol, h[h["fecha"] < d].drop(columns="en_promocion"), ctx)
    assert vp["promo_media_28"].isna().all() and vp["promo_desconocida"].eq(0).all()


def test_enmascarado_es_determinista_y_no_toca_objetivo_ni_calendario():
    h, ctx = _historia(), _ctx()
    limpio = variables_entrenamiento(h, ctx, enmascarar=False)
    a = variables_entrenamiento(h, ctx, semilla=3)
    b = variables_entrenamiento(h, ctx, semilla=3)
    c = variables_entrenamiento(h, ctx, semilla=4)
    pd.testing.assert_frame_equal(a, b)
    assert not _numericas(a).equals(_numericas(c))
    pd.testing.assert_series_equal(a["ventas"], limpio["ventas"])
    assert a["dia_mes"].equals(limpio["dia_mes"])
    cambiadas = (a["ultimo_dato"].fillna(-1) != limpio["ultimo_dato"].fillna(-1)).mean()
    assert 0.01 < cambiadas < 0.3
    assert a["dias_desde_ultimo_dato"].max() > 1


def test_calendario_dias_de_pago_eventos_y_feriado_local_exacto():
    ctx = _ctx()
    fechas = pd.Series(
        pd.to_datetime(
            [
                "2025-01-31",
                "2025-01-30",
                "2025-01-15",
                "2025-01-16",
                "2025-01-01",
                "2025-12-24",
                "2025-12-31",
                "2025-12-27",
                "2025-04-18",
                "2025-05-15",
                "2025-05-15",
            ]
        )
    )
    ciudades = pd.Series(["Guatemala"] * 10 + ["Mixco"])
    cal = variables_calendario(fechas, ciudades, ctx.feriados)
    assert cal["ultimo_dia"].tolist()[:2] == [1, 0]  # el penúltimo día no cuenta
    assert cal.loc[2, "dia_15"] == 1 and cal.loc[3, "dia_16"] == 1
    assert cal.loc[4, "dia_1"] == 1 and cal.loc[4, "ev_1ene"] == 1
    assert cal.loc[5, "ev_24dic"] == 1 and cal.loc[6, "ev_31dic"] == 1
    assert cal.loc[7, "ev_26_30dic"] == 1
    assert cal.loc[8, "ev_semana_santa"] == 1 and cal.loc[8, "nac_feriado"] == 1
    assert (
        cal.loc[9, "feriado_local"] == 1 and cal.loc[10, "feriado_local"] == 0
    )  # Mixco ≠ Guatemala
    assert cal.loc[9, "nac_feriado"] == 0


def test_fecha_fuera_de_cobertura_de_feriados_deja_indicadores_en_cero():
    ctx = _ctx()
    cal = variables_calendario(
        pd.Series(pd.to_datetime(["2030-05-01", "2030-12-24"])),
        pd.Series(["Guatemala"] * 2),
        ctx.feriados,
    )
    assert cal.loc[0, ["nac_feriado", "nac_comercial", "feriado_local"]].sum() == 0
    assert cal.loc[1, "ev_24dic"] == 1  # los eventos fijos no dependen del CSV


def test_categorias_fijas_y_desconocidas_como_nan():
    h, ctx = _historia(), _ctx()
    d = INICIO + pd.Timedelta(days=90)
    sol = pd.DataFrame(
        {
            "tienda_id": [1, 1],
            "fecha": [d, d],
            "familia_id": ["licores", "nueva"],
            "en_promocion": [0, 2],
        }
    )
    vp = variables_prediccion(sol, h[h["fecha"] < d], ctx)
    assert list(vp["familia"].cat.categories) == sorted(FAMILIAS)
    assert vp.loc[0, "familia"] == "licores" and pd.isna(vp.loc[1, "familia"])
    assert list(vp["tipo_tienda"].cat.categories) == ["express", "hipermercado"]
    assert vp["en_promocion"].tolist() == [0.0, 2.0]
