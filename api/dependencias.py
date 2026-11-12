"""Contenedor de dependencias del servicio (se arma en el arranque y se inyecta en los routers)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException, Request

from novamercado.config import Config
from novamercado.limpieza import Catalogo
from novamercado.servicio.airflow import AirflowClient
from novamercado.servicio.cargador import GestorModelo
from novamercado.servicio.catalogo import ProveedorCatalogo
from novamercado.servicio.salud import MonitorSalud


@dataclass
class Servicio:
    config: Config
    gestor: GestorModelo
    monitor: MonitorSalud
    airflow: AirflowClient
    conectar: Callable[[], psycopg.Connection]
    #: Catálogo de la limpieza; por defecto se lee de la BD una vez y queda en caché.
    catalogo: Callable[[], Catalogo] | None = field(default=None)

    def __post_init__(self) -> None:
        if self.catalogo is None:
            self.catalogo = ProveedorCatalogo(self.conectar)


async def obtener_servicio(request: Request) -> Servicio:
    """`async`: una dependencia síncrona ocuparía un hilo del pool incluso en `/health`."""
    return request.app.state.servicio


async def leer_cuerpo_crudo(request: Request) -> bytes:
    """Cuerpo tal como llegó, sin mirar `Content-Type` (la limpieza decide si es JSON).

    Es la única parte asíncrona: los endpoints que usan la BD son `def` y FastAPI los corre en
    su pool de hilos, así nada bloqueante toca el event loop.
    """
    return await request.body()


@contextmanager
def abrir_conexion(servicio: Servicio) -> Iterator[psycopg.Connection]:
    """Conexión para una solicitud; si la BD no responde, 503 (nunca 500).

    Se abre dentro del endpoint (no como dependencia) para que un cuerpo inválido responda 422
    aunque la BD esté caída.
    """
    try:
        conn = servicio.conectar()
    except psycopg.OperationalError as exc:
        raise HTTPException(status_code=503, detail="Base de datos no disponible") from exc
    try:
        yield conn
    finally:
        conn.close()


ServicioDep = Annotated[Servicio, Depends(obtener_servicio)]
CuerpoCrudo = Annotated[bytes, Depends(leer_cuerpo_crudo)]
