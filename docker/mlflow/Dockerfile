# Servidor de MLflow: imagen oficial (misma versión que mlflow-skinny del cliente) + psycopg2
# para guardar el backend (experimentos y Model Registry) en Postgres.
FROM ghcr.io/mlflow/mlflow:v3.18.0

ENV PIP_DEFAULT_TIMEOUT=120
RUN pip install --no-cache-dir psycopg2-binary==2.9.11 \
    && groupadd --system --gid 10002 mlflow \
    && useradd --system --uid 10002 --gid mlflow --home-dir /mlflow mlflow \
    && mkdir -p /mlflow/artefactos \
    && chown -R mlflow:mlflow /mlflow

COPY docker/mlflow/entrypoint.py /usr/local/bin/mlflow-entrypoint.py

USER mlflow
WORKDIR /mlflow
EXPOSE 5000
ENTRYPOINT ["python", "/usr/local/bin/mlflow-entrypoint.py"]
