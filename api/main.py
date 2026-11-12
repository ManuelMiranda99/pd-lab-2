"""Aplicación FastAPI. Arranca sin esperar al bootstrap: responde 503 hasta tener modelo.

Correr con un solo worker (el modelo y los hilos viven en el proceso):
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 1
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from api.dependencias import Servicio
from api.routers import admin, prediccion, salud
from novamercado.config import Config
from novamercado.db.conexion import conectar
from novamercado.db.esquema import migrar
from novamercado.servicio.airflow import AirflowClient
from novamercado.servicio.cache import CacheModelos
from novamercado.servicio.cargador import GestorModelo
from novamercado.servicio.registro import RegistroMlflow, mlflow_saludable
from novamercado.servicio.salud import MonitorSalud

log = logging.getLogger("novamercado.api")


def _bd_ok(cfg: Config) -> bool:
    with conectar(cfg.db_url, timeout=3) as conn:
        conn.execute("SELECT 1")
    return True


def _airflow_ok(cliente: AirflowClient, dag_id: str) -> bool:
    return cliente.salud() and cliente.dag_activo(dag_id)


def construir_servicio(cfg: Config) -> Servicio:
    airflow = AirflowClient(cfg.airflow_url, cfg.airflow_usuario, cfg.airflow_password, timeout=3)
    gestor = GestorModelo(
        RegistroMlflow(cfg.mlflow_uri),
        CacheModelos(cfg.cache_dir),
        nombre=cfg.modelo_nombre,
        alias=cfg.modelo_alias,
        poll_segundos=cfg.poll_segundos,
    )
    monitor = MonitorSalud(
        {
            "base_de_datos": partial(_bd_ok, cfg),
            "mlflow": partial(mlflow_saludable, cfg.mlflow_uri),
            "airflow": partial(_airflow_ok, airflow, cfg.dag_id),
        },
        intervalo=cfg.salud_segundos,
    )
    return Servicio(cfg, gestor, monitor, airflow, partial(conectar, cfg.db_url, timeout=3))


def _migrar_en_fondo(cfg: Config) -> None:
    """Asegura el esquema (idempotente) para que /admin funcione aunque el bootstrap no haya
    corrido todavía. Reintenta hasta que la BD responda."""
    import time

    for _ in range(60):
        try:
            with conectar(cfg.db_url) as conn:
                migrar(conn)
            return
        except Exception:  # noqa: BLE001
            log.info("BD no disponible para migrar; reintento en 5 s")
            time.sleep(5)


def crear_app(servicio: Servicio | None = None, *, arrancar_hilos: bool = True) -> FastAPI:
    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        svc = servicio or construir_servicio(Config.desde_entorno())
        app.state.servicio = svc
        if arrancar_hilos:
            threading.Thread(
                target=_migrar_en_fondo, args=(svc.config,), name="migracion", daemon=True
            ).start()
            # La primera carga (alias → caché) corre en un hilo: la API acepta solicitudes ya.
            threading.Thread(
                target=svc.gestor.arrancar, name="arranque-modelo", daemon=True
            ).start()
            svc.monitor.arrancar()
        yield
        if arrancar_hilos:
            svc.monitor.detener()
            svc.gestor.detener()
        svc.airflow.cerrar()

    app = FastAPI(
        title="NovaMercado",
        version="0.1.0",
        description="Venta esperada y cantidad sugerida por tienda y familia.",
        lifespan=ciclo_de_vida,
    )
    app.include_router(salud.router)
    app.include_router(prediccion.router)
    app.include_router(admin.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = crear_app()
