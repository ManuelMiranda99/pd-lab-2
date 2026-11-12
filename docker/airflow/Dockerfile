# Airflow 3.3.2 (LocalExecutor) con un venv aislado para entrenar (ExternalPythonOperator).
# El entorno de Airflow no recibe pandas/mlflow: el DAG solo orquesta.
FROM apache/airflow:3.3.2-python3.11

USER root
ENV PIP_DEFAULT_TIMEOUT=120
COPY requirements-ml.txt /tmp/novamercado/requirements-ml.txt
COPY pyproject.toml /tmp/novamercado/pyproject.toml
COPY src /tmp/novamercado/src
# La imagen oficial fija PIP_USER=true; dentro del venv debe ser false.
RUN python -m venv /opt/venv-ml \
    && PIP_USER=false /opt/venv-ml/bin/pip install --no-cache-dir \
        -r /tmp/novamercado/requirements-ml.txt \
    && PIP_USER=false /opt/venv-ml/bin/pip install --no-cache-dir --no-deps /tmp/novamercado \
    && rm -rf /tmp/novamercado

USER airflow
COPY --chown=airflow:root dags/ /opt/airflow/dags/
