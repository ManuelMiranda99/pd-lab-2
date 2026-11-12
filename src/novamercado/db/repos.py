"""Repositorios: consultas y escrituras de la BD del producto (sin lógica de negocio)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import psycopg

ESTADOS_TERMINALES = ("exitoso", "fallido")


@dataclass(frozen=True)
class Reentrenamiento:
    id: str
    estado: str
    promovido: bool | None
    version: str | None
    motivo: str | None


def crear_reentrenamiento(conn: psycopg.Connection, id_: str) -> None:
    with conn.transaction():
        conn.execute(
            "INSERT INTO reentrenamientos (id, estado) VALUES (%s, 'en_curso')",
            (id_,),
        )


def obtener_reentrenamiento(conn: psycopg.Connection, id_: str) -> Reentrenamiento | None:
    fila = conn.execute(
        "SELECT id, estado, promovido, version, motivo FROM reentrenamientos WHERE id = %s",
        (id_,),
    ).fetchone()
    conn.commit()
    return Reentrenamiento(*fila) if fila else None


def terminar_reentrenamiento(
    conn: psycopg.Connection,
    id_: str,
    *,
    estado: str,
    promovido: bool,
    version: str | None,
    motivo: str,
    detalle: dict[str, Any] | None = None,
) -> bool:
    """Marca una ejecución como terminada si aún no lo estaba. Devuelve si escribió."""
    if estado not in ESTADOS_TERMINALES:
        raise ValueError(f"Estado terminal inválido: {estado}")
    with conn.transaction():
        cur = conn.execute(
            """
            UPDATE reentrenamientos
               SET estado = %s, promovido = %s, version = %s, motivo = %s,
                   detalle = %s, actualizado_en = now()
             WHERE id = %s AND estado = 'en_curso'
            """,
            (estado, promovido, version, motivo, json.dumps(detalle or {}), id_),
        )
        return cur.rowcount > 0


def costos_por_familia(conn: psycopg.Connection) -> dict[str, tuple[float, float]]:
    """{familia_id: (costo_faltante, costo_sobrante)}."""
    filas = conn.execute("SELECT familia_id, costo_faltante, costo_sobrante FROM costos").fetchall()
    conn.commit()
    return {f: (float(cf), float(cs)) for f, cf, cs in filas}


def tienda_existe(conn: psycopg.Connection, tienda_id: int) -> bool:
    fila = conn.execute("SELECT 1 FROM tiendas WHERE tienda_id = %s", (tienda_id,)).fetchone()
    return fila is not None


def familias_catalogo(conn: psycopg.Connection) -> list[str]:
    return [f for (f,) in conn.execute("SELECT familia_id FROM familias ORDER BY familia_id")]


def huella_datos(conn: psycopg.Connection) -> str | None:
    fila = conn.execute("SELECT sha256 FROM carga_datos WHERE id = 1").fetchone()
    return fila[0] if fila else None
