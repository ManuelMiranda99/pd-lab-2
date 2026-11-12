"""Features mínimas del baseline: una sola función para entrenamiento y servicio.

`media_mismo_dia` resume, para cada (tienda, familia), las últimas `k` ventas observadas del mismo
día de la semana que la fecha objetivo, usando solo filas con `fecha < objetivo` (defensa extra:
el filtro principal ya se hizo en SQL).
"""

from __future__ import annotations

import pandas as pd

K_SEMANAS = 4
DIAS_RESPALDO = 28


def media_mismo_dia(
    panel: pd.DataFrame, objetivo: pd.Timestamp, k: int = K_SEMANAS
) -> pd.DataFrame:
    """Devuelve columnas `tienda_id, familia_id, media_dia, media_reciente`.

    - `media_dia`: media de las últimas `k` observaciones del mismo día de la semana.
    - `media_reciente`: media de los últimos `DIAS_RESPALDO` días observados (respaldo).
    """
    objetivo = pd.Timestamp(objetivo)
    previo = panel[panel["fecha"] < objetivo]
    claves = ["tienda_id", "familia_id"]
    if previo.empty:
        return pd.DataFrame(columns=[*claves, "media_dia", "media_reciente"])
    ordenado = previo.sort_values("fecha", ascending=False)
    mismo_dia = ordenado[ordenado["fecha"].dt.dayofweek == objetivo.dayofweek]
    media_dia = mismo_dia.groupby(claves).head(k).groupby(claves)["ventas"].mean()
    reciente = ordenado[ordenado["fecha"] >= objetivo - pd.Timedelta(days=DIAS_RESPALDO)]
    media_reciente = reciente.groupby(claves)["ventas"].mean()
    out = pd.concat(
        [media_dia.rename("media_dia"), media_reciente.rename("media_reciente")], axis=1
    )
    return out.reset_index()
