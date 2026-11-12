"""Variables de calendario (DECISIONES §2.2 y §2.3): días de pago, feriados y eventos fijos.

Solo dependen de la fecha objetivo y de `feriados.csv`; nunca del reloj del sistema. Si la fecha
cae fuera de la cobertura de feriados, sus indicadores quedan en 0 (los eventos fijos y los días
de pago se calculan con la fecha y no dependen del CSV).
"""

from __future__ import annotations

import pandas as pd

TIPOS_FERIADO_NACIONAL = ("feriado", "comercial", "pago", "medio_dia")

COLUMNAS_CALENDARIO = [
    "dow",
    "dia_mes",
    "mes",
    "dias_a_fin_mes",
    "dia_1",
    "dia_15",
    "dia_16",
    "ultimo_dia",
    *[f"nac_{t}" for t in TIPOS_FERIADO_NACIONAL],
    "feriado_local",
    "ev_24dic",
    "ev_25dic",
    "ev_31dic",
    "ev_1ene",
    "ev_26_30dic",
    "ev_semana_santa",
]


def dias_semana_santa(feriados: pd.DataFrame) -> set[pd.Timestamp]:
    """Jueves, Viernes y Sábado Santo según los nombres de `feriados.csv`."""
    nombres = feriados["nombre"].astype(str)
    es_santo = nombres.str.contains("Santo", case=False) & nombres.str.contains(
        "Jueves|Viernes|Sábado|Sabado", case=False
    )
    return set(pd.to_datetime(feriados.loc[es_santo, "fecha"]))


def variables_calendario(
    fechas: pd.Series, ciudades: pd.Series, feriados: pd.DataFrame
) -> pd.DataFrame:
    """Calendario para pares (fecha, ciudad), con el mismo índice que `fechas`.

    El feriado local exige coincidencia **exacta** entre `ambito` y la ciudad de la tienda
    (Mixco ≠ Guatemala).
    """
    indice = fechas.index
    f = pd.to_datetime(pd.Series(fechas.to_numpy(), index=indice))
    ciudad = pd.Series(ciudades.to_numpy(), index=indice).astype(str)
    out = pd.DataFrame(index=indice)
    out["dow"] = f.dt.day_of_week
    out["dia_mes"] = f.dt.day
    out["mes"] = f.dt.month
    out["dias_a_fin_mes"] = f.dt.days_in_month - f.dt.day
    out["dia_1"] = (out["dia_mes"] == 1).astype(int)
    out["dia_15"] = (out["dia_mes"] == 15).astype(int)
    out["dia_16"] = (out["dia_mes"] == 16).astype(int)
    out["ultimo_dia"] = (out["dias_a_fin_mes"] == 0).astype(int)  # el penúltimo no sube

    fer = feriados.assign(fecha=pd.to_datetime(feriados["fecha"]))
    ambito = fer["ambito"].astype(str)
    nac = fer[ambito == "nacional"]
    for tipo in TIPOS_FERIADO_NACIONAL:
        dias = set(nac.loc[nac["tipo"] == tipo, "fecha"])
        out[f"nac_{tipo}"] = f.isin(dias).astype(int)
    loc = fer[ambito != "nacional"]
    pares = set(zip(loc["fecha"], loc["ambito"].astype(str), strict=True))
    out["feriado_local"] = [int((a, b) in pares) for a, b in zip(f, ciudad, strict=True)]

    md = f.dt.month * 100 + f.dt.day
    out["ev_24dic"] = (md == 1224).astype(int)
    out["ev_25dic"] = (md == 1225).astype(int)
    out["ev_31dic"] = (md == 1231).astype(int)
    out["ev_1ene"] = (md == 101).astype(int)
    out["ev_26_30dic"] = md.between(1226, 1230).astype(int)
    out["ev_semana_santa"] = f.isin(dias_semana_santa(fer)).astype(int)
    return out[COLUMNAS_CALENDARIO]
