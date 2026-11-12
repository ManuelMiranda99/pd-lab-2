"""Acceso al Model Registry de MLflow desde el servicio (solo lo usa el poller, nunca /predicción).

El cliente usa `MLFLOW_HTTP_REQUEST_MAX_RETRIES=0` y `MLFLOW_HTTP_REQUEST_TIMEOUT=5` (ver
`novamercado.config`), así que una caída de MLflow se detecta en segundos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import httpx

import novamercado.config  # noqa: F401 — fija las variables MLFLOW_HTTP_* antes de usar mlflow


class RegistroNoDisponible(RuntimeError):
    """MLflow no respondió o respondió con un error inesperado."""


class Registro(Protocol):
    def version_con_alias(self, nombre: str, alias: str) -> str | None: ...

    def descargar(self, nombre: str, version: str, destino: Path) -> Path: ...


class RegistroMlflow:
    """Implementación real sobre `MlflowClient`."""

    def __init__(self, uri: str) -> None:
        self.uri = uri

    def version_con_alias(self, nombre: str, alias: str) -> str | None:
        from mlflow import MlflowClient
        from mlflow.exceptions import MlflowException

        try:
            cliente = MlflowClient(tracking_uri=self.uri, registry_uri=self.uri)
            return str(cliente.get_model_version_by_alias(nombre, alias).version)
        except MlflowException as exc:
            if exc.error_code == "RESOURCE_DOES_NOT_EXIST":
                return None
            raise RegistroNoDisponible(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — conexión rechazada, timeout, DNS…
            raise RegistroNoDisponible(str(exc)) from exc

    def descargar(self, nombre: str, version: str, destino: Path) -> Path:
        import mlflow
        import mlflow.artifacts

        try:
            mlflow.set_tracking_uri(self.uri)
            mlflow.set_registry_uri(self.uri)
            ruta = mlflow.artifacts.download_artifacts(
                artifact_uri=f"models:/{nombre}/{version}",
                dst_path=str(destino),
                tracking_uri=self.uri,
            )
        except Exception as exc:  # noqa: BLE001
            raise RegistroNoDisponible(str(exc)) from exc
        return Path(ruta)


def mlflow_saludable(uri: str, *, timeout: float = 3.0) -> bool:
    """`GET /health` del servidor de MLflow con timeout corto."""
    try:
        r = httpx.get(f"{uri.rstrip('/')}/health", timeout=timeout)
        return r.status_code == 200
    except httpx.HTTPError:
        return False
