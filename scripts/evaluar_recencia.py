"""Ponderar lo reciente vs reentrenar ante cambios de demanda (evidencia de DECISIONES §4.4).

Para cada escenario (normal, y choque de nivel de −20 % y +20 % desde 2025-12-01) entrena la
receta HGB con distintas vidas medias del peso por recencia y mide el costo con pronóstico a un
paso después del corte. Con choque, también el del vigente congelado (entrenado antes del choque).

Uso:
    DATOS_DIR=/ruta/a/data python scripts/evaluar_recencia.py [salida.json]

Tarda ~8 min. No usa el reloj del sistema para decidir nada (solo mide duraciones).
"""

import json
import sys
import time
from pathlib import Path

import pandas as pd

from comun_datos import cargar, dir_datos
from novamercado.features.variables import Contexto
from novamercado.modelo.baseline import ajustar_baseline
from novamercado.modelo.hgb import RecetaHGB
from novamercado.modelo.metricas import costo_total
from novamercado.modelo.validacion import prediccion_rodante

d = cargar(dir_datos(None))
ctx = Contexto.desde_tablas(d.tiendas, d.feriados, d.familias["familia_id"].tolist())
costos = {r.familia_id: (r.costo_faltante, r.costo_sobrante) for r in d.costos.itertuples()}
H = d.historico
res = {}
escenarios = {
    "normal": (1.0, pd.Timestamp("2026-01-01")),
    "choque_-20": (0.8, pd.Timestamp("2026-01-15")),
    "choque_+20": (1.2, pd.Timestamp("2026-01-15")),
}
INICIO_CHOQUE = pd.Timestamp("2025-12-01")
for nombre, (factor, corte) in escenarios.items():
    h = H.copy()
    m = h["fecha"] >= INICIO_CHOQUE
    h.loc[m, "ventas"] = (h.loc[m, "ventas"] * factor).round()
    dias = sorted(h.loc[h["fecha"] >= corte, "fecha"].unique())
    fila = {}
    base = ajustar_baseline(h, costos, antes_de=corte)
    fila["baseline"] = round(costo_total(prediccion_rodante(base, h, dias), costos))
    if factor != 1.0:
        congelado = RecetaHGB(h, ctx, costos).modelo(INICIO_CHOQUE)
        fila["vigente_congelado"] = round(
            costo_total(prediccion_rodante(congelado, h, dias), costos)
        )
    for vm in (None, 730, 365, 180, 90):
        t = time.perf_counter()
        modelo = RecetaHGB(h, ctx, costos, vida_media_dias=vm).modelo(corte)
        fila[f"vm_{vm}"] = round(costo_total(prediccion_rodante(modelo, h, dias), costos))
        print(nombre, vm, fila[f"vm_{vm}"], f"{time.perf_counter() - t:.0f}s", flush=True)
    res[nombre] = fila
    print(nombre, fila, flush=True)
salida = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("evidencia/modelo/recencia.json")
salida.write_text(json.dumps(res, indent=2))
