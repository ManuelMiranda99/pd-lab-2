"""Catálogo de la limpieza leído de la BD del producto, con caché en memoria.

El catálogo (tiendas, familias, rango de fechas) sale de los datos que cargó el bootstrap, así
que la API y el entrenamiento usan la misma fuente. Se lee una vez y se reutiliza: los datos
publicados no cambian mientras el servicio corre (las observaciones no agregan tiendas ni
familias y no mueven el inicio del histórico ni la cobertura de feriados).
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import psycopg

from novamercado.db import repos
from novamercado.limpieza.catalogo import Catalogo, Tienda


class CatalogoNoDisponible(RuntimeError):
    """La BD no responde o todavía no tiene los datos cargados (bootstrap pendiente)."""


def construir_catalogo(filas: repos.FilasCatalogo) -> Catalogo:
    """Arma el `Catalogo` de la limpieza a partir de las filas leídas de la BD."""
    if not filas.tiendas or not filas.familias or filas.fecha_inicio is None:
        raise CatalogoNoDisponible("La BD todavía no tiene los datos cargados")
    fin = filas.fecha_fin_cobertura or filas.fecha_inicio
    return Catalogo(
        tiendas={t: Tienda(tienda_id=t, nombre=n, fecha_apertura=a) for t, n, a in filas.tiendas},
        familias=dict(filas.familias),
        fecha_inicio=filas.fecha_inicio,
        fecha_fin_cobertura=max(fin, filas.fecha_inicio),
    )


class ProveedorCatalogo:
    """Lee el catálogo la primera vez que se pide y lo conserva (seguro entre hilos)."""

    def __init__(self, conectar: Callable[[], psycopg.Connection]) -> None:
        self._conectar = conectar
        self._catalogo: Catalogo | None = None
        self._candado = threading.Lock()

    def __call__(self) -> Catalogo:
        if self._catalogo is not None:
            return self._catalogo
        with self._candado:
            if self._catalogo is None:
                try:
                    with self._conectar() as conn:
                        filas = repos.leer_catalogo(conn)
                except psycopg.Error as exc:
                    raise CatalogoNoDisponible(f"Base de datos no disponible: {exc}") from exc
                self._catalogo = construir_catalogo(filas)
            return self._catalogo
