"""Repositorios: consultas y escrituras de la BD del producto (sin lógica de negocio)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

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


# ---------------------------------------------------------------------------------------------
# Catálogo para la limpieza (derivado de los datos cargados, nunca del reloj)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FilasCatalogo:
    """Lo que necesita `limpieza.Catalogo`, leído de la BD."""

    tiendas: list[tuple[int, str, date]]
    familias: list[tuple[str, str]]
    fecha_inicio: date | None
    fecha_fin_cobertura: date | None


def leer_catalogo(conn: psycopg.Connection) -> FilasCatalogo:
    """Tiendas, familias, primera fecha del histórico y última de `feriados`."""
    tiendas = conn.execute(
        "SELECT tienda_id, nombre, fecha_apertura FROM tiendas ORDER BY tienda_id"
    ).fetchall()
    familias = conn.execute(
        "SELECT familia_id, nombre FROM familias ORDER BY familia_id"
    ).fetchall()
    inicio, fin = conn.execute(
        "SELECT (SELECT min(fecha) FROM historico_ventas), (SELECT max(fecha) FROM feriados)"
    ).fetchone()
    conn.commit()
    return FilasCatalogo(list(tiendas), list(familias), inicio, fin)


# ---------------------------------------------------------------------------------------------
# Solicitudes, predicciones, correcciones y observaciones
# ---------------------------------------------------------------------------------------------

_MAX_CUERPO = 100_000  # caracteres del cuerpo crudo que se guardan (el límite de lectura es 1 MB)


def texto_seguro(texto: str, largo: int | None = None) -> str:
    """Texto apto para una columna TEXT/JSONB de Postgres (sin NUL), opcionalmente truncado."""
    limpio = texto.replace("\x00", "\\u0000")
    return limpio if largo is None or len(limpio) <= largo else limpio[:largo] + "…"


def cuerpo_como_texto(cuerpo: bytes) -> str:
    """Cuerpo crudo legible para auditoría (no se usa para interpretar la solicitud)."""
    return texto_seguro(cuerpo.decode("utf-8", errors="replace"), _MAX_CUERPO)


@dataclass(frozen=True)
class Correccion:
    """Hallazgo de la limpieza tal como se registra en `correcciones`."""

    codigo: str
    campo: str
    accion: str
    detalle: str


@dataclass(frozen=True)
class FilaPrediccion:
    familia_id: str
    en_promocion: int
    promocion_explicita: bool
    venta_esperada: float
    cantidad_sugerida: int


def _insertar_solicitud(
    conn: psycopg.Connection,
    *,
    tipo: str,
    estado: str,
    cuerpo: bytes,
    normalizado: dict[str, Any] | None = None,
    tienda_id: int | None = None,
    fecha: date | None = None,
    calidad_estado: str | None = None,
    codigo_rechazo: str | None = None,
    detail: str | None = None,
    claves_ignoradas: tuple[str, ...] = (),
    version_modelo: str | None = None,
) -> int:
    fila = conn.execute(
        """
        INSERT INTO solicitudes (tipo, estado, cuerpo_crudo, normalizado, tienda_id, fecha,
                                 calidad_estado, codigo_rechazo, detail, claves_ignoradas,
                                 version_modelo)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            tipo,
            estado,
            cuerpo_como_texto(cuerpo),
            Jsonb(normalizado) if normalizado is not None else None,
            tienda_id,
            fecha,
            calidad_estado,
            codigo_rechazo,
            texto_seguro(detail, 2_000) if detail is not None else None,
            Jsonb([texto_seguro(c, 200) for c in claves_ignoradas]),
            version_modelo,
        ),
    ).fetchone()
    assert fila is not None
    return int(fila[0])


def _insertar_correcciones(
    conn: psycopg.Connection, solicitud_id: int, correcciones: list[Correccion]
) -> None:
    if not correcciones:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO correcciones (solicitud_id, codigo, campo, accion, detalle)"
            " VALUES (%s, %s, %s, %s, %s)",
            [
                (
                    solicitud_id,
                    c.codigo,
                    texto_seguro(c.campo, 500),
                    c.accion,
                    texto_seguro(c.detalle, 2_000),
                )
                for c in correcciones
            ],
        )


def registrar_rechazo(
    conn: psycopg.Connection, *, tipo: str, cuerpo: bytes, codigo: str, detail: str
) -> int:
    """Guarda una solicitud rechazada (422) con su `detail`. Devuelve el id."""
    with conn.transaction():
        return _insertar_solicitud(
            conn, tipo=tipo, estado="rechazada", cuerpo=cuerpo, codigo_rechazo=codigo, detail=detail
        )


def registrar_prediccion(
    conn: psycopg.Connection,
    *,
    cuerpo: bytes,
    normalizado: dict[str, Any],
    tienda_id: int,
    fecha: date,
    calidad_estado: str,
    claves_ignoradas: tuple[str, ...],
    version_modelo: str,
    correcciones: list[Correccion],
    filas: list[FilaPrediccion],
) -> int:
    """Solicitud aceptada + predicciones + correcciones, en **una** transacción."""
    with conn.transaction():
        id_ = _insertar_solicitud(
            conn,
            tipo="prediccion",
            estado="aceptada",
            cuerpo=cuerpo,
            normalizado=normalizado,
            tienda_id=tienda_id,
            fecha=fecha,
            calidad_estado=calidad_estado,
            claves_ignoradas=claves_ignoradas,
            version_modelo=version_modelo,
        )
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO predicciones (solicitud_id, tienda_id, fecha, familia_id, en_promocion,
                                          promocion_explicita, venta_esperada, cantidad_sugerida,
                                          version_modelo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        id_,
                        tienda_id,
                        fecha,
                        f.familia_id,
                        f.en_promocion,
                        f.promocion_explicita,
                        f.venta_esperada,
                        f.cantidad_sugerida,
                        version_modelo,
                    )
                    for f in filas
                ],
            )
        _insertar_correcciones(conn, id_, correcciones)
    return id_


_SQL_UPSERT_OBSERVACIONES = """
INSERT INTO observaciones AS o (tienda_id, fecha, familia_id, ventas, solicitud_id)
SELECT %(tienda)s, %(fecha)s, e.familia_id, e.ventas, %(solicitud)s
  FROM unnest(%(familias)s::text[], %(ventas)s::integer[]) AS e (familia_id, ventas)
    ON CONFLICT (tienda_id, fecha, familia_id) DO UPDATE
   SET ventas = EXCLUDED.ventas, solicitud_id = EXCLUDED.solicitud_id, actualizado_en = now()
 WHERE o.ventas IS DISTINCT FROM EXCLUDED.ventas
RETURNING o.familia_id, o.ventas
"""


def registrar_observaciones(
    conn: psycopg.Connection,
    *,
    cuerpo: bytes,
    normalizado: dict[str, Any],
    tienda_id: int,
    fecha: date,
    calidad_estado: str,
    claves_ignoradas: tuple[str, ...],
    correcciones: list[Correccion],
    ventas: list[tuple[str, int]],
) -> int:
    """Upsert idempotente de las ventas reales. Devuelve cuántas filas se insertaron o cambiaron.

    Un reenvío idéntico no escribe nada (`WHERE … IS DISTINCT FROM`) y devuelve 0. Cada alta o
    cambio queda en `observaciones_historial` con el valor anterior.
    """
    with conn.transaction():
        id_ = _insertar_solicitud(
            conn,
            tipo="observaciones",
            estado="aceptada",
            cuerpo=cuerpo,
            normalizado=normalizado,
            tienda_id=tienda_id,
            fecha=fecha,
            calidad_estado=calidad_estado,
            claves_ignoradas=claves_ignoradas,
        )
        _insertar_correcciones(conn, id_, correcciones)
        if not ventas:
            return 0
        familias = [f for f, _ in ventas]
        anteriores = dict(
            conn.execute(
                "SELECT familia_id, ventas FROM observaciones"
                " WHERE tienda_id = %s AND fecha = %s AND familia_id = ANY(%s) FOR UPDATE",
                (tienda_id, fecha, familias),
            ).fetchall()
        )
        cambiadas = conn.execute(
            _SQL_UPSERT_OBSERVACIONES,
            {
                "tienda": tienda_id,
                "fecha": fecha,
                "solicitud": id_,
                "familias": familias,
                "ventas": [v for _, v in ventas],
            },
        ).fetchall()
        if cambiadas:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO observaciones_historial (solicitud_id, tienda_id, fecha,"
                    " familia_id, ventas_anterior, ventas_nueva) VALUES (%s, %s, %s, %s, %s, %s)",
                    [(id_, tienda_id, fecha, f, anteriores.get(f), v) for f, v in cambiadas],
                )
    return len(cambiadas)
