"""Configuración leída del entorno (una sola fuente para API, bootstrap y DAG).

Los valores por defecto sirven para pruebas locales; en Docker Compose llegan desde `.env`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# El cliente de MLflow lee estas variables en cada llamada: sin reintentos largos ni esperas de
# minutos cuando el servidor está caído (el camino de predicción nunca depende de MLflow).
os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "5")
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


def _entero(nombre: str, defecto: int) -> int:
    valor = os.environ.get(nombre, "").strip()
    return int(valor) if valor else defecto


def _decimal(nombre: str, defecto: float) -> float:
    valor = os.environ.get(nombre, "").strip()
    return float(valor) if valor else defecto


@dataclass(frozen=True)
class Config:
    """Parámetros del servicio. Se construye con `Config.desde_entorno()`."""

    db_url: str = "postgresql://novamercado:novamercado@localhost:5432/novamercado"
    mlflow_uri: str = "http://localhost:5001"
    modelo_nombre: str = "novamercado"
    modelo_alias: str = "champion"
    experimento: str = "novamercado"
    datos_dir: Path = Path("/datos")
    cache_dir: Path = Path("/var/cache/novamercado")
    poll_segundos: int = 20
    salud_segundos: int = 10
    airflow_url: str = "http://localhost:8080"
    airflow_usuario: str = "airflow"
    airflow_password: str = "airflow"
    dag_id: str = "reentrenamiento"
    mejora_minima: float = 0.0
    espera_mlflow_segundos: int = 180

    @classmethod
    def desde_entorno(cls) -> Config:
        e = os.environ.get
        return cls(
            db_url=e("NOVAMERCADO_DB_URL", cls.db_url),
            mlflow_uri=e("MLFLOW_TRACKING_URI", cls.mlflow_uri),
            modelo_nombre=e("MODELO_NOMBRE", cls.modelo_nombre),
            modelo_alias=e("MODELO_ALIAS", cls.modelo_alias),
            experimento=e("MLFLOW_EXPERIMENTO", cls.experimento),
            datos_dir=Path(e("DATOS_RUTA", str(cls.datos_dir))),
            cache_dir=Path(e("CACHE_MODELO_DIR", str(cls.cache_dir))),
            poll_segundos=_entero("POLL_MODELO_SEGUNDOS", cls.poll_segundos),
            salud_segundos=_entero("SALUD_SEGUNDOS", cls.salud_segundos),
            airflow_url=e("AIRFLOW_URL", cls.airflow_url),
            airflow_usuario=e("AIRFLOW_USUARIO", cls.airflow_usuario),
            airflow_password=e("AIRFLOW_PASSWORD", cls.airflow_password),
            dag_id=e("AIRFLOW_DAG_ID", cls.dag_id),
            mejora_minima=_decimal("CRITERIO_MEJORA_MINIMA", cls.mejora_minima),
            espera_mlflow_segundos=_entero("ESPERA_MLFLOW_SEGUNDOS", cls.espera_mlflow_segundos),
        )
