"""Arranque del servidor de MLflow.

1. Crea la base `MLFLOW_DB` en el Postgres del producto si no existe (idempotente).
2. Reemplaza el proceso por `mlflow server` con artefactos servidos desde un volumen.
"""

import os
import sys
import time

import psycopg2
from psycopg2 import sql

usuario = os.environ["POSTGRES_USER"]
clave = os.environ["POSTGRES_PASSWORD"]
host = os.environ.get("POSTGRES_HOST", "db")
base = os.environ.get("MLFLOW_DB", "mlflow")

for intento in range(60):
    try:
        conn = psycopg2.connect(
            host=host, user=usuario, password=clave, dbname="postgres", connect_timeout=5
        )
        break
    except psycopg2.OperationalError as exc:
        print(f"Postgres no disponible ({exc}); reintento {intento + 1}/60", flush=True)
        time.sleep(2)
else:
    sys.exit("Postgres no respondió: no se puede iniciar MLflow")

conn.autocommit = True
with conn.cursor() as cur:
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (base,))
    if cur.fetchone() is None:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(base)))
        print(f"Base {base} creada", flush=True)
conn.close()

args = [
    "mlflow",
    "server",
    "--host",
    "0.0.0.0",
    "--port",
    "5000",
    "--workers",
    os.environ.get("MLFLOW_WORKERS", "2"),
    "--backend-store-uri",
    f"postgresql+psycopg2://{usuario}:{clave}@{host}:5432/{base}",
    "--serve-artifacts",
    "--artifacts-destination",
    "/mlflow/artefactos",
    "--default-artifact-root",
    "mlflow-artifacts:/",
    "--allowed-hosts",
    os.environ.get(
        "MLFLOW_ALLOWED_HOSTS", "mlflow,mlflow:5000,localhost,localhost:*,127.0.0.1,127.0.0.1:*"
    ),
]
os.execvp(args[0], args)
