"""Lectura del panel de ventas (histórico ∪ observaciones) desde la BD.

Regla de no fuga: al predecir la fecha `D` solo se leen filas con `fecha < D`, y el filtro va en
SQL. Si una fecha está en el histórico y en observaciones, gana la observación.

`en_promocion` (DECISIONES §6.3): la del histórico; para una observación, la de la última
solicitud de predicción aceptada que incluyó esa familia de forma explícita (orden por id, no
por reloj), o la del histórico si existe esa fila; si no, NULL (promoción desconocida).
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import psycopg

from novamercado.features.variables import VENTANA_DIAS, Contexto

COLUMNAS_PANEL = ["tienda_id", "fecha", "familia_id", "ventas", "en_promocion"]

# Días hacia atrás que se leen al predecir: los mismos que puede mirar cualquier variable.
DIAS_PANEL = VENTANA_DIAS

_SQL_PANEL = """
SELECT DISTINCT ON (tienda_id, fecha, familia_id) tienda_id, fecha, familia_id, ventas, en_promocion
  FROM (
        SELECT o.tienda_id, o.fecha, o.familia_id, o.ventas,
               COALESCE(p.en_promocion, h.en_promocion) AS en_promocion, 0 AS prioridad
          FROM observaciones o
          LEFT JOIN LATERAL (
                SELECT pr.en_promocion
                  FROM predicciones pr
                  JOIN solicitudes s ON s.id = pr.solicitud_id
                 WHERE pr.tienda_id = o.tienda_id AND pr.fecha = o.fecha
                   AND pr.familia_id = o.familia_id AND pr.promocion_explicita
                   AND s.estado = 'aceptada'
                 ORDER BY pr.solicitud_id DESC
                 LIMIT 1
               ) p ON true
          LEFT JOIN historico_ventas h
            ON h.tienda_id = o.tienda_id AND h.fecha = o.fecha AND h.familia_id = o.familia_id
         WHERE o.fecha < %(hasta)s AND o.fecha >= %(desde)s {filtro_obs}
        UNION ALL
        SELECT tienda_id, fecha, familia_id, ventas, en_promocion, 1 AS prioridad
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
    filtro_obs = "AND o.tienda_id = %(tienda)s" if tienda_id is not None else ""
    sql = _SQL_PANEL.format(filtro_tienda=filtro, filtro_obs=filtro_obs)
    with conn.cursor() as cur:
        cur.execute(sql, {"hasta": hasta, "desde": desde, "tienda": tienda_id})
        filas = cur.fetchall()
    conn.commit()
    return a_panel(pd.DataFrame(filas, columns=COLUMNAS_PANEL))


def leer_historia(conn: psycopg.Connection) -> pd.DataFrame:
    """Todo el panel disponible (para entrenar): histórico ∪ observaciones.

    Defensa en profundidad: las observaciones posteriores a la cobertura de `feriados.csv` (la
    API ya las rechaza) no entran al entrenamiento; una fecha absurda desplazaría el corte del
    modelo y las ventanas de evaluación.
    """
    fila = conn.execute(
        "SELECT GREATEST((SELECT max(fecha) FROM historico_ventas),"
        " (SELECT max(fecha) FROM observaciones"
        "   WHERE fecha <= COALESCE((SELECT max(fecha) FROM feriados), fecha)))"
    ).fetchone()
    conn.commit()
    if fila is None or fila[0] is None:
        return a_panel(pd.DataFrame(columns=COLUMNAS_PANEL))
    return leer_panel(conn, fila[0] + timedelta(days=1), dias=None)


def a_panel(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza tipos del panel: fecha datetime64, ids enteros, ventas y promoción float."""
    if "en_promocion" not in df:
        df = df.assign(en_promocion=float("nan"))
    out = df.loc[:, COLUMNAS_PANEL].copy()
    out["tienda_id"] = out["tienda_id"].astype("int64")
    out["fecha"] = pd.to_datetime(out["fecha"])
    out["familia_id"] = out["familia_id"].astype(str)
    out["ventas"] = out["ventas"].astype(float)
    out["en_promocion"] = pd.to_numeric(out["en_promocion"], errors="coerce").astype(float)
    return out.reset_index(drop=True)


def leer_contexto(conn: psycopg.Connection) -> Contexto:
    """Tiendas, feriados y familias de la BD: lo que necesitan las variables del modelo."""
    with conn.cursor() as cur:
        cur.execute("SELECT tienda_id, ciudad, tipo, fecha_apertura FROM tiendas")
        tiendas = pd.DataFrame(
            cur.fetchall(), columns=["tienda_id", "ciudad", "tipo", "fecha_apertura"]
        )
        cur.execute("SELECT fecha, nombre, tipo, ambito FROM feriados")
        feriados = pd.DataFrame(cur.fetchall(), columns=["fecha", "nombre", "tipo", "ambito"])
        cur.execute("SELECT familia_id FROM familias")
        familias = [f for (f,) in cur.fetchall()]
    conn.commit()
    return Contexto.desde_tablas(tiendas, feriados, familias)
