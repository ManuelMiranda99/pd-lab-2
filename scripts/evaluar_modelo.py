"""Evaluación del modelo de producción con los datos reales (evidencia de la Fase 5).

Usa exactamente el código del servicio (`novamercado.modelo`), no el del backtest:

1. Validación ene–feb 2026: receta HGB entrenada con `fecha < 2026-01-01` (q_f fuera de muestra)
   contra el baseline, con pronóstico a un paso (`predecir(solicitud, panel < D)`). Costo con y
   sin el cuantil por familia, y métricas técnicas.
2. Robustez (F5.6): las mismas fechas sin ningún dato reciente (panel vacío), HGB vs baseline.
3. Ciclo de reentrenamiento (F5.2/F5.9): arranque en frío y retador vs vigente congelado
   (corte 2026-02-08) con origen móvil y el criterio de §4.2; tiempo de cada uno.

Uso:
    DATOS_DIR=/ruta/a/data python scripts/evaluar_modelo.py

Guarda agregados en `evidencia/modelo/evaluacion_modelo.json`. No usa el reloj del sistema
para decidir nada (solo mide duraciones).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from comun_datos import cargar, dir_datos
from novamercado.features.variables import Contexto
from novamercado.modelo.baseline import ajustar_baseline
from novamercado.modelo.criterio import decidir_reemplazo
from novamercado.modelo.hgb import RecetaHGB
from novamercado.modelo.metricas import costo_total, metricas_tecnicas
from novamercado.modelo.validacion import entrenar_y_evaluar, prediccion_rodante

RAIZ = Path(__file__).resolve().parents[1]
CORTE_VAL = pd.Timestamp("2026-01-01")
CORTE_VIGENTE = pd.Timestamp("2026-02-08")


def _resumen(p: pd.DataFrame, costos: dict) -> dict:
    sin_q = p.assign(cantidad_sugerida=np.ceil(p["venta_esperada"].astype(float)))
    return {
        "costo_con_cuantil": round(costo_total(p, costos)),
        "costo_sugiriendo_prediccion": round(costo_total(sin_q, costos)),
        **{k: round(v, 4) for k, v in metricas_tecnicas(p).items()},
        "sesgo_relativo": round(float(p["venta_esperada"].sum() / p["ventas"].sum() - 1), 4),
    }


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--datos", default=None)
    ap.add_argument("--salida", default=str(RAIZ / "evidencia" / "modelo"))
    args = ap.parse_args(argv)
    d = cargar(dir_datos(args.datos))
    ctx = Contexto.desde_tablas(d.tiendas, d.feriados, d.familias["familia_id"].tolist())
    costos = {r.familia_id: (r.costo_faltante, r.costo_sobrante) for r in d.costos.itertuples()}
    h = d.historico
    res: dict = {}

    print("== Validación ene–feb 2026")
    t0 = time.perf_counter()
    receta = RecetaHGB(h, ctx, costos)
    hgb = receta.modelo(CORTE_VAL)
    t_hgb = time.perf_counter() - t0
    base = ajustar_baseline(h, costos, antes_de=CORTE_VAL)
    dias = sorted(h.loc[h["fecha"] >= CORTE_VAL, "fecha"].unique())
    p_hgb = prediccion_rodante(hgb, h, dias)
    p_base = prediccion_rodante(base, h, dias)
    res["validacion"] = {
        "dias": len(dias),
        "filas": len(p_hgb),
        "hgb": _resumen(p_hgb, costos),
        "baseline": _resumen(p_base, costos),
        "q_por_familia": {k: round(v, 3) for k, v in sorted(hgb.factores.items())},
        "segundos_receta_y_modelo": round(t_hgb, 1),
    }
    v = res["validacion"]
    v["mejora_hgb_vs_baseline"] = round(
        v["hgb"]["costo_con_cuantil"] / v["baseline"]["costo_con_cuantil"] - 1, 4
    )
    v["efecto_cuantil_hgb"] = round(
        v["hgb"]["costo_con_cuantil"] / v["hgb"]["costo_sugiriendo_prediccion"] - 1, 4
    )
    print(json.dumps(v, indent=1, ensure_ascii=False))

    print("== Robustez sin datos recientes (panel vacío)")
    reales = h[h["fecha"].isin(dias)]
    sol = reales[["tienda_id", "fecha", "familia_id", "en_promocion"]]
    vacio = h.iloc[:0]
    sin = {}
    for nombre, m in (("hgb", hgb), ("baseline", base)):
        p = m.predecir(sol, vacio).assign(ventas=reales["ventas"].to_numpy())
        sin[nombre] = _resumen(p, costos)
    sin["hgb_vs_baseline"] = round(
        sin["hgb"]["costo_con_cuantil"] / sin["baseline"]["costo_con_cuantil"] - 1, 4
    )
    res["sin_lags"] = sin
    print(json.dumps(sin, indent=1, ensure_ascii=False))

    print("== Ciclo: arranque en frío")
    frio = entrenar_y_evaluar(h, ctx, costos)
    res["arranque_en_frio"] = {
        "segundos": round(frio.segundos, 1),
        "ventanas": frio.ventanas,
        "motivo": decidir_reemplazo(frio.comparacion).motivo,
    }
    print(res["arranque_en_frio"]["segundos"], "s ·", res["arranque_en_frio"]["motivo"])

    print("== Ciclo: retador vs vigente congelado")
    vigente = entrenar_y_evaluar(h[h["fecha"] < CORTE_VIGENTE], ctx, costos).modelo
    ret = entrenar_y_evaluar(h, ctx, costos, vigente=vigente, corte_vigente=CORTE_VIGENTE)
    res["retador_vs_vigente"] = {
        "corte_vigente": str(CORTE_VIGENTE.date()),
        "segundos": round(ret.segundos, 1),
        "ventanas": ret.ventanas,
        "motivo": decidir_reemplazo(ret.comparacion).motivo,
    }
    print(res["retador_vs_vigente"]["segundos"], "s ·", res["retador_vs_vigente"]["motivo"])

    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "evaluacion_modelo.json").write_text(
        json.dumps(res, indent=2, ensure_ascii=False, default=float)
    )
    return res


if __name__ == "__main__":
    main()
