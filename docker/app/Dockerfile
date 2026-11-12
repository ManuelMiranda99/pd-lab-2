# Imagen del producto: la usan `api` (uvicorn) y `bootstrap` (python -m novamercado.bootstrap).
# Sin datos ni credenciales: los datos se montan en /datos (solo lectura) y la config llega por env.
FROM python:3.11.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app app

WORKDIR /app
COPY requirements.txt requirements-ml.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml ./
COPY src ./src
COPY api ./api
RUN pip install --no-deps . \
    && mkdir -p /var/cache/novamercado \
    && chown app:app /var/cache/novamercado

ARG NOVAMERCADO_COMMIT=desconocido
ENV NOVAMERCADO_COMMIT=${NOVAMERCADO_COMMIT}

USER app
EXPOSE 8000
# Un solo worker: el modelo en memoria, el poller y el monitor de salud viven en el proceso.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
