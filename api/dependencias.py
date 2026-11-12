"""Contenedor de dependencias del servicio (se arma en el arranque y se inyecta en los routers)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException, Request

from novamercado.config import Config
from novamercado.servicio.airflow import AirflowClient
from novamercado.servicio.cargador import GestorModelo
from novamercado.servicio.salud import MonitorSalud


@dataclass
class Servicio:
    config: Config
    gestor: GestorModelo
    monitor: MonitorSalud
    airflow: AirflowClient
    conectar: Callable[[], psycopg.Connection]


def obtener_servicio(request: Request) -> Servicio:
    return request.app.state.servicio


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
