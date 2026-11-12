"""Lectura del panel de ventas (histórico ∪ observaciones) desde la BD.

Regla de no fuga: al predecir la fecha `D` solo se leen filas con `fecha < D`, y el filtro va en
SQL. Si una fecha está en el histórico y en observaciones, gana la observación.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import psycopg

COLUMNAS_PANEL = ["tienda_id", "fecha", "familia_id", "ventas"]

# Días hacia atrás que se leen al predecir (cubre k semanas del mismo día aunque haya huecos).
DIAS_PANEL = 7 * 16

_SQL_PANEL = """
SELECT DISTINCT ON (tienda_id, fecha, familia_id) tienda_id, fecha, familia_id, ventas
  FROM (
        SELECT tienda_id, fecha, familia_id, ventas, 0 AS prioridad
          FROM observaciones
         WHERE fecha < %(hasta)s AND fecha >= %(desde)s {filtro_tienda}
        UNION ALL
        SELECT tienda_id, fecha, familia_id, ventas, 1 AS prioridad
          FROM historico_ventas
         WHERE fecha < %(hasta)s AND fecha >= %(desde)s {filtro_tienda}
       ) AS u
 ORDER BY tienda_id, fecha, familia_id, prioridad
"""


def leer_panel(
    conn: psycopg.Connection,
    hasta: date,
    *,
    tienda_id: int | None = None,
    dias: int | None = DIAS_PANEL,
) -> pd.DataFrame:
    """Ventas con `fecha < hasta` (y `>= hasta - dias` si `dias` no es None)."""
    desde = date.min if dias is None else hasta - timedelta(days=dias)
    filtro = "AND tienda_id = %(tienda)s" if tienda_id is not None else ""
    sql = _SQL_PANEL.format(filtro_tienda=filtro)
    with conn.cursor() as cur:
        cur.execute(sql, {"hasta": hasta, "desde": desde, "tienda": tienda_id})
        filas = cur.fetchall()
    conn.commit()
    return a_panel(pd.DataFrame(filas, columns=COLUMNAS_PANEL))


def leer_historia(conn: psycopg.Connection) -> pd.DataFrame:
    """Todo el panel disponible (para entrenar): histórico ∪ observaciones."""
    fila = conn.execute(
        "SELECT GREATEST((SELECT max(fecha) FROM historico_ventas),"
        " (SELECT max(fecha) FROM observaciones))"
    ).fetchone()
    conn.commit()
    if fila is None or fila[0] is None:
        return a_panel(pd.DataFrame(columns=COLUMNAS_PANEL))
    return leer_panel(conn, fila[0] + timedelta(days=1), dias=None)


def a_panel(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza tipos del panel: fecha como datetime64, ids enteros, ventas float."""
    out = df.loc[:, COLUMNAS_PANEL].copy()
    out["tienda_id"] = out["tienda_id"].astype("int64")
    out["fecha"] = pd.to_datetime(out["fecha"])
    out["familia_id"] = out["familia_id"].astype(str)
    out["ventas"] = out["ventas"].astype(float)
    return out.reset_index(drop=True)
