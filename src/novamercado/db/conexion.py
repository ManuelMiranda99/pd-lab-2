"""Conexiones a la base de datos del producto y bloqueo consultivo para tareas únicas."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

log = logging.getLogger(__name__)

# Clave fija del pg_advisory_lock que serializa bootstrap (y migraciones) entre contenedores.
CLAVE_BLOQUEO_BOOTSTRAP = 72_610_001


def conectar(db_url: str, *, autocommit: bool = False, timeout: int = 5) -> psycopg.Connection:
    """Abre una conexión con un tiempo máximo de conexión corto."""
    return psycopg.connect(db_url, autocommit=autocommit, connect_timeout=timeout)


def esperar_bd(db_url: str, *, intentos: int = 30, pausa: float = 2.0) -> None:
    """Espera a que la BD acepte conexiones; lanza la última excepción si no lo logra."""
    ultimo: Exception | None = None
    for _ in range(intentos):
        try:
            with conectar(db_url, autocommit=True) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError as exc:
            ultimo = exc
            log.info("BD no disponible todavía: %s", exc)
            time.sleep(pausa)
    assert ultimo is not None
    raise ultimo


@contextmanager
def bloqueo_consultivo(db_url: str, clave: int = CLAVE_BLOQUEO_BOOTSTRAP) -> Iterator[None]:
    """Toma un `pg_advisory_lock` de sesión; se libera al salir (o si el proceso muere)."""
    with conectar(db_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (clave,))
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (clave,))
