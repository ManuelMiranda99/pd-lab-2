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

import psycopg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.dependencias import Servicio
from api.routers import admin, prediccion, salud
from novamercado.config import Config
from novamercado.db.conexion import conectar
from novamercado.db.esquema import migrar
from novamercado.servicio.airflow import AirflowClient
from novamercado.servicio.cache import CacheModelos
from novamercado.servicio.cargador import GestorModelo
from novamercado.servicio.registro import RegistroMlflow, RegistroNoDisponible, mlflow_saludable
from novamercado.servicio.salud import MonitorSalud

log = logging.getLogger("novamercado.api")


def _texto_validacion(exc: RequestValidationError) -> str:
    """Errores de validación de FastAPI (ruta o query) como un texto legible."""
    partes = []
    for err in exc.errors():
        ubicacion = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body", "path"))
        partes.append(
            f"{ubicacion}: {err.get('msg', 'valor inválido')}" if ubicacion else err.get("msg", "")
        )
    return "; ".join(p for p in partes if p) or "Solicitud inválida"


def registrar_manejadores(app: FastAPI) -> None:
    """Todo error sale como `{"detail": "<texto>"}`: 422 para validación (nunca 400), 503 si
    la BD falla en cualquier endpoint y 503 ante cualquier error inesperado (nunca 500)."""

    @app.exception_handler(RequestValidationError)
    async def _validacion(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _texto_validacion(exc)})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        codigo = 422 if exc.status_code == 400 else exc.status_code
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse(status_code=codigo, content={"detail": detail}, headers=exc.headers)

    @app.exception_handler(psycopg.Error)
    async def _bd(_: Request, exc: psycopg.Error) -> JSONResponse:
        log.warning("Error de base de datos: %s", exc)
        return JSONResponse(status_code=503, content={"detail": "Base de datos no disponible"})

    @app.exception_handler(Exception)
    async def _inesperado(request: Request, exc: Exception) -> JSONResponse:
        # Última red: ninguna entrada termina en 500. El error queda en el log para corregirlo.
        log.exception("Error inesperado en %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=503,
            content={"detail": f"Error interno inesperado ({type(exc).__name__}); reintente"},
        )


def _bd_ok(cfg: Config) -> bool:
    with conectar(cfg.db_url, timeout=3) as conn:
        conn.execute("SELECT 1")
    return True


def _airflow_ok(cliente: AirflowClient, dag_id: str) -> bool:
    return cliente.salud() and cliente.dag_activo(dag_id)


def _mlflow_ok(cfg: Config, registro: RegistroMlflow) -> bool:
    """El servidor responde **y** el Registry (que vive en la BD del producto, §5.4) también."""
    if not mlflow_saludable(cfg.mlflow_uri):
        return False
    try:
        registro.version_con_alias(cfg.modelo_nombre, cfg.modelo_alias)
    except RegistroNoDisponible:
        return False
    return True


def construir_servicio(cfg: Config) -> Servicio:
    airflow = AirflowClient(cfg.airflow_url, cfg.airflow_usuario, cfg.airflow_password, timeout=3)
    registro = RegistroMlflow(cfg.mlflow_uri)
    gestor = GestorModelo(
        registro,
        CacheModelos(cfg.cache_dir),
        nombre=cfg.modelo_nombre,
        alias=cfg.modelo_alias,
        poll_segundos=cfg.poll_segundos,
    )
    monitor = MonitorSalud(
        {
            "base_de_datos": partial(_bd_ok, cfg),
            "mlflow": partial(_mlflow_ok, cfg, registro),
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
    registrar_manejadores(app)
    app.include_router(salud.router)
    app.include_router(prediccion.router)
    app.include_router(admin.router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = crear_app()
