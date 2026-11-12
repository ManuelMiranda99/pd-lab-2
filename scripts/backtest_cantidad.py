"""Backtest de la cantidad sugerida y del criterio de reemplazo (apoya DECISIONES.md §3 y §4).

Qué hace (todo con pronóstico a un paso: al predecir el día D solo se usan ventas con fecha < D):

1. Validación ene–feb 2026 (59 días): baseline (media del mismo día de semana, 4 semanas) vs
   HistGradientBoosting con tres formas de tratar el sesgo (Poisson; log1p + smearing; log1p sin
   corrección). Métricas técnicas (WAPE, sesgo) y costo con `costos.csv` sugiriendo:
   a) la predicción, b) predicción × cuantil τ del ratio real/predicho por familia, calibrado
   fuera de muestra en las semanas previas al corte (varias longitudes de ventana).
2. Rolling origin (4 ventanas de 14 días): "vigente congelado" (entrenado antes del 2025-11-01)
   vs "retador" reentrenado justo antes de cada ventana, y un placebo (mismos datos, otra semilla)
   para estimar el ruido de la comparación y fijar el umbral de mejora.

Uso:
    DATOS_DIR=/ruta/a/data python scripts/backtest_cantidad.py [--rapido]

Solo usa `requirements-ml.txt`. Los resultados (agregados) se guardan en
`evidencia/eda/backtest_cantidad.json`. No usa el reloj del sistema.
"""

from __future__ import annotations

import argparse
import copy
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from comun_datos import (
    Datos,
    calendario,
    cargar,
    costo,
    dir_datos,
    es_cierre_tienda,
    tau_por_familia,
)

RAIZ = Path(__file__).resolve().parents[1]
INICIO_VAL = pd.Timestamp("2026-01-01")
FIN_VAL = pd.Timestamp("2026-02-28")  # inclusive
CORTE_VIGENTE = pd.Timestamp("2025-11-01")
LAGS = (1, 2, 7, 14, 21, 28)


# ------------------------------------------------------------------------------ features
def construir_panel(d: Datos) -> pd.DataFrame:
    """Panel con features que solo miran fechas < D (lags por desplazamiento de la serie)."""
    h = d.historico.copy()
    h.loc[es_cierre_tienda(h), "ventas"] = np.nan  # cierre de tienda 7: fuera de target y lags
    h = h.sort_values(["tienda_id", "familia_id", "fecha"]).reset_index(drop=True)
    g = h.groupby(["tienda_id", "familia_id"])["ventas"]
    for k in LAGS:
        h[f"lag_{k}"] = g.shift(k)
    h["media_dow_4"] = h[["lag_7", "lag_14", "lag_21", "lag_28"]].mean(axis=1)
    h["media_7"] = g.transform(lambda s: s.shift(1).rolling(7, min_periods=3).mean())
    h["media_28"] = g.transform(lambda s: s.shift(1).rolling(28, min_periods=7).mean())
    h["media_91"] = g.transform(lambda s: s.shift(1).rolling(91, min_periods=14).mean())
    h["ratio_7_28"] = h["media_7"] / h["media_28"]
    # Promoción relativa a lo habitual de la serie (también solo con pasado).
    h["promo_media_28"] = h.groupby(["tienda_id", "familia_id"])["en_promocion"].transform(
        lambda s: s.shift(1).rolling(28, min_periods=7).mean()
    )
    tiendas = d.tiendas.set_index("tienda_id")
    ciudad = h["tienda_id"].map(tiendas["ciudad"])
    cal = calendario(h["fecha"], ciudad, d.feriados)
    h = pd.concat([h, cal], axis=1)
    h["dias_desde_apertura"] = (
        h["fecha"] - h["tienda_id"].map(tiendas["fecha_apertura"])
    ).dt.days.clip(upper=400)
    h["tipo_tienda"] = h["tienda_id"].map(tiendas["tipo"]).astype("category").cat.codes
    h["familia"] = h["familia_id"].astype("category").cat.codes
    return h


FEATURES = [
    *[f"lag_{k}" for k in LAGS],
    "media_dow_4",
    "media_7",
    "media_28",
    "media_91",
    "ratio_7_28",
    "en_promocion",
    "promo_media_28",
    "dow",
    "dia_mes",
    "mes",
    "dias_a_fin_mes",
    "dia_15",
    "dia_16",
    "fin_mes",
    "inicio_mes",
    "nac_feriado",
    "nac_comercial",
    "nac_pago",
    "nac_medio_dia",
    "feriado_local",
    "ev_24dic",
    "ev_25dic",
    "ev_31dic",
    "ev_1ene",
    "ev_26_30dic",
    "ev_semana_santa",
    "dias_desde_apertura",
    "tipo_tienda",
    "familia",
    "tienda_id",
]
CATEGORICAS = ["tipo_tienda", "familia", "tienda_id", "dow"]


# ------------------------------------------------------------------------------- modelos
class Modelo:
    """HGB global con una de tres formas de tratar el sesgo de la media."""

    def __init__(self, variante: str, semilla: int = 0) -> None:
        self.variante = variante
        self.semilla = semilla
        self.smear = 1.0

    def fit(self, df: pd.DataFrame) -> Modelo:
        df = df[df["ventas"].notna() & df["media_28"].notna()]
        x, y = df[FEATURES], df["ventas"].to_numpy()
        comunes = {
            "max_iter": 400,
            "learning_rate": 0.06,
            "max_leaf_nodes": 63,
            "min_samples_leaf": 40,
            "l2_regularization": 1.0,
            "categorical_features": [FEATURES.index(c) for c in CATEGORICAS],
            "random_state": self.semilla,
            "early_stopping": self.semilla != 0,  # semilla 0: determinista; otras: placebo
        }
        if self.variante == "poisson":
            self.m = HistGradientBoostingRegressor(loss="poisson", **comunes).fit(x, y)
        else:
            self.m = HistGradientBoostingRegressor(loss="squared_error", **comunes).fit(
                x, np.log1p(y)
            )
            # Factor de Duan (smearing) para pasar de la media en log a la media en unidades.
            resid = np.log1p(y) - self.m.predict(x)
            self.smear = float(np.mean(np.exp(resid)))
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        p = self.m.predict(df[FEATURES])
        if self.variante != "poisson":
            factor = self.smear if self.variante == "log_smearing" else 1.0
            p = np.expm1(p) * factor
        return np.clip(p, 0, None)


_AJUSTES: dict[tuple, Modelo] = {}


def entrenar(panel: pd.DataFrame, corte: pd.Timestamp, variante: str, semilla: int = 0) -> Modelo:
    """Entrena con fecha < corte; reutiliza ajustes idénticos (las dos variantes log comparten)."""
    base = "poisson" if variante == "poisson" else "log"
    clave = (base, corte, semilla)
    if clave not in _AJUSTES:
        _AJUSTES[clave] = Modelo(base if base == "poisson" else "log_smearing", semilla).fit(
            panel[panel["fecha"] < corte]
        )
    m = copy.copy(_AJUSTES[clave])
    m.variante = variante
    return m


def baseline(df: pd.DataFrame) -> np.ndarray:
    return df["media_dow_4"].fillna(df["media_28"]).fillna(0).to_numpy()


# ------------------------------------------------------------------- cuantil del ratio
def cuantiles_ratio(
    real: np.ndarray, pred: np.ndarray, fam: pd.Series, tau: pd.Series
) -> pd.Series:
    """q_f = cuantil τ_f de real/pred por familia (pred > 0)."""
    df = pd.DataFrame({"r": real / np.maximum(pred, 1e-6), "f": fam.to_numpy()})
    df = df[np.isfinite(df["r"]) & (pred > 0)]
    return df.groupby("f")["r"].apply(lambda s: float(np.quantile(s, tau[s.name])))


def evaluar(df: pd.DataFrame, pred: np.ndarray, q: pd.Series | None, cs: Datos) -> dict:
    c = cs.costos.set_index("familia_id")
    cf = df["familia_id"].map(c["costo_faltante"]).to_numpy()
    cso = df["familia_id"].map(c["costo_sobrante"]).to_numpy()
    real = df["ventas"].to_numpy()
    out = {
        "wape": float(np.abs(real - pred).sum() / real.sum()),
        "sesgo": float(pred.sum() / real.sum() - 1),
        "costo_pred": float(costo(real, np.ceil(pred), cf, cso).sum()),
    }
    if q is not None:
        sug = np.ceil(pred * df["familia_id"].map(q).to_numpy())
        out["costo_cuantil"] = float(costo(real, sug, cf, cso).sum())
        tau = df["familia_id"].map(tau_por_familia(cs.costos)).to_numpy()
        out["cobertura_vs_tau"] = float(np.mean((real <= sug) - tau))
    return out


def ajustar_con_calibracion(
    panel: pd.DataFrame, corte: pd.Timestamp, dias_calib: int, variante: str, d: Datos, semilla=0
) -> tuple[object, pd.Series]:
    """Receta de entrenamiento: modelo A (< corte − C) → ratios fuera de muestra en [corte − C,
    corte) → q_f; modelo final B entrenado con todo lo anterior al corte."""
    tau = tau_por_familia(d.costos)
    ini_cal = corte - pd.Timedelta(days=dias_calib)
    cal = panel[(panel["fecha"] >= ini_cal) & (panel["fecha"] < corte) & panel["ventas"].notna()]
    if variante == "baseline":
        q = cuantiles_ratio(cal["ventas"].to_numpy(), baseline(cal), cal["familia_id"], tau)
        return "baseline", q
    a = entrenar(panel, ini_cal, variante, semilla)
    q = cuantiles_ratio(cal["ventas"].to_numpy(), a.predict(cal), cal["familia_id"], tau)
    b = entrenar(panel, corte, variante, semilla)
    return b, q


def predecir(m, df: pd.DataFrame) -> np.ndarray:
    return baseline(df) if m == "baseline" else m.predict(df)


# --------------------------------------------------------------------------- experimentos
def experimento_validacion(panel: pd.DataFrame, d: Datos, rapido: bool) -> dict:
    val = panel[(panel["fecha"] >= INICIO_VAL) & (panel["fecha"] <= FIN_VAL)]
    val = val[val["ventas"].notna()]
    res: dict = {"dias": int(val["fecha"].nunique()), "filas": len(val)}
    variantes = ["baseline", "poisson", "log_smearing", "log_sin_correccion"]
    if rapido:
        variantes = variantes[:2]
    for v in variantes:
        t0 = time.perf_counter()
        m, q = ajustar_con_calibracion(panel, INICIO_VAL, 56, v, d)
        p = predecir(m, val)
        res[v] = evaluar(val, p, q, d)
        res[v]["segundos"] = round(time.perf_counter() - t0, 1)
        print(f"  {v}: {res[v]}")
    # Sensibilidad a la ventana de calibración (solo Poisson).
    sens = {}
    for dias in [28, 56, 91] if not rapido else [28]:
        m, q = ajustar_con_calibracion(panel, INICIO_VAL, dias, "poisson", d)
        sens[dias] = evaluar(val, predecir(m, val), q, d)["costo_cuantil"]
    res["poisson_costo_cuantil_por_dias_calibracion"] = sens
    print("  sensibilidad calibración:", sens)
    # Cuantiles q_f (Poisson, 56 días) y costo por familia.
    m, q = ajustar_con_calibracion(panel, INICIO_VAL, 56, "poisson", d)
    res["q_por_familia_poisson_56d"] = {k: round(float(v), 3) for k, v in q.items()}
    p = predecir(m, val)
    c = d.costos.set_index("familia_id")
    sug = np.ceil(p * val["familia_id"].map(q).to_numpy())
    fam_cost = (
        pd.DataFrame(
            {
                "f": val["familia_id"],
                "pred": costo(
                    val["ventas"].to_numpy(),
                    np.ceil(p),
                    val["familia_id"].map(c["costo_faltante"]).to_numpy(),
                    val["familia_id"].map(c["costo_sobrante"]).to_numpy(),
                ),
                "cuantil": costo(
                    val["ventas"].to_numpy(),
                    sug,
                    val["familia_id"].map(c["costo_faltante"]).to_numpy(),
                    val["familia_id"].map(c["costo_sobrante"]).to_numpy(),
                ),
            }
        )
        .groupby("f")[["pred", "cuantil"]]
        .sum()
    )
    res["costo_por_familia_poisson"] = fam_cost.round(0).to_dict(orient="index")
    # Alternativa aditiva: pred + cuantil τ del residuo real − pred.
    ini_cal = INICIO_VAL - pd.Timedelta(days=56)
    cal = panel[
        (panel["fecha"] >= ini_cal) & (panel["fecha"] < INICIO_VAL) & panel["ventas"].notna()
    ]
    a = entrenar(panel, ini_cal, "poisson")
    resid = pd.DataFrame({"e": cal["ventas"].to_numpy() - a.predict(cal), "f": cal["familia_id"]})
    tau = tau_por_familia(d.costos)
    qa = resid.groupby("f")["e"].apply(lambda s: float(np.quantile(s, tau[s.name])))
    sug_a = np.clip(np.ceil(p + val["familia_id"].map(qa).to_numpy()), 0, None)
    res["poisson_costo_aditivo"] = float(
        costo(
            val["ventas"].to_numpy(),
            sug_a,
            val["familia_id"].map(c["costo_faltante"]).to_numpy(),
            val["familia_id"].map(c["costo_sobrante"]).to_numpy(),
        ).sum()
    )
    print("  aditivo:", res["poisson_costo_aditivo"])
    return res


def costo_ventana(m, q, df: pd.DataFrame, d: Datos) -> float:
    return evaluar(df, predecir(m, df), q, d)["costo_cuantil"]


def experimento_rolling(panel: pd.DataFrame, d: Datos, rapido: bool) -> dict:
    n_vent = 2 if rapido else 4
    ventanas = [INICIO_VAL + pd.Timedelta(days=14 * i) for i in range(n_vent)]
    vigente, q_vig = ajustar_con_calibracion(panel, CORTE_VIGENTE, 56, "poisson", d)
    base_q = ajustar_con_calibracion(panel, CORTE_VIGENTE, 56, "baseline", d)[1]
    filas = []
    for ini in ventanas:
        fin = ini + pd.Timedelta(days=14)
        w = panel[(panel["fecha"] >= ini) & (panel["fecha"] < fin) & panel["ventas"].notna()]
        ret, q_ret = ajustar_con_calibracion(panel, ini, 56, "poisson", d)
        plac, q_plac = ajustar_con_calibracion(panel, ini, 56, "poisson", d, semilla=7)
        fila = {
            "inicio": ini.date().isoformat(),
            "vigente_congelado": costo_ventana(vigente, q_vig, w, d),
            "retador": costo_ventana(ret, q_ret, w, d),
            "placebo_otra_semilla": costo_ventana(plac, q_plac, w, d),
            "baseline": costo_ventana("baseline", base_q, w, d),
        }
        filas.append(fila)
        print("  ventana", fila)
    tab = pd.DataFrame(filas).set_index("inicio")
    tot = tab.sum()
    # Ruido: bootstrap por días de la diferencia relativa retador vs placebo (mismos datos).
    return {
        "ventanas": tab.round(0).to_dict(orient="index"),
        "totales": tot.round(0).to_dict(),
        "mejora_retador_vs_vigente": round(float(1 - tot["retador"] / tot["vigente_congelado"]), 4),
        "diferencia_placebo_vs_retador": round(
            float(tot["placebo_otra_semilla"] / tot["retador"] - 1), 4
        ),
        "dif_rel_por_ventana_retador_vs_vigente": (
            (1 - tab["retador"] / tab["vigente_congelado"]).round(4).tolist()
        ),
        "dif_rel_por_ventana_placebo": (tab["placebo_otra_semilla"] / tab["retador"] - 1)
        .round(4)
        .tolist(),
    }


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--datos", default=None)
    ap.add_argument("--salida", default=str(RAIZ / "evidencia" / "eda"))
    ap.add_argument("--rapido", action="store_true", help="menos variantes y ventanas")
    args = ap.parse_args(argv)
    t0 = time.perf_counter()
    d = cargar(dir_datos(args.datos))
    panel = construir_panel(d)
    print("== Validación ene–feb 2026")
    res = {"validacion": experimento_validacion(panel, d, args.rapido)}
    print("== Rolling origin (criterio de reemplazo)")
    res["rolling"] = experimento_rolling(panel, d, args.rapido)
    res["segundos_totales"] = round(time.perf_counter() - t0, 1)
    print("== Total:", res["segundos_totales"], "s")
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "backtest_cantidad.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    return res


if __name__ == "__main__":
    main()
