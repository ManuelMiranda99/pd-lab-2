# NovaMercado — Servicio de ML en producción (Laboratorio 2)

Servicio que, cada mañana y por tienda, recibe las familias de productos con sus promociones del día
y devuelve por familia la **venta esperada** y la **cantidad sugerida** (calculada a partir de los costos
de faltante y sobrante). Al día siguiente recibe las ventas reales, se reentrena con Airflow y cambia
de modelo sin reiniciarse.

> 🚧 En construcción. El avance está en [`PLAN.md`](PLAN.md) y [`BITACORA.md`](BITACORA.md); las
> decisiones, en [`DECISIONES.md`](DECISIONES.md).

## Requisitos

- Docker con Docker Compose v2 y al menos **6 GB de RAM** asignados.
- Puertos libres: 8000 (API), 5001 (MLflow), 8080 (Airflow).

## Datos

Los datos **no están en el repositorio**. Coloca los archivos publicados en el GES en la carpeta
`data/` de la raíz del repo (o define `DATOS_DIR` en `.env`):

```
data/
├── historico_ventas.csv
├── tiendas.csv
├── familias.csv
├── feriados.csv
├── costos.csv
└── ejemplos/            (opcional)
```

## Levantar el servicio

```bash
cp .env.example .env      # funciona sin editar
docker compose up
```

_(Las instrucciones de verificación y reentrenamiento se completan en la Fase 8.)_

## Desarrollo

```bash
git config core.hooksPath .githooks      # ganchos locales: bloquean datos/credenciales y push a main
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -e .
ruff check . && ruff format --check .
pytest -q
```

## Pruebas de punta a punta (smoke y simulador)

Escriben en la BD (observaciones, reentrenos): se corren en un **proyecto de Compose aparte**
(`-p`), con volúmenes propios, nunca contra el stack que se entrega. Comparten los puertos, así que
primero `docker compose stop` del principal (conserva sus volúmenes).

```bash
# Smoke con datos sintéticos (lo mismo que el job `smoke` de la CI)
python -m novamercado.sintetico /tmp/datos-sinteticos
DATOS_DIR=/tmp/datos-sinteticos docker compose -p novamercado-e2e up -d
PYTHONPATH=src python scripts/smoke.py --mlflow --proyecto novamercado-e2e

# Simulador sin fuga con los datos reales: carga el histórico hasta --corte (HISTORICO_HASTA)
# y simula los días retenidos con errores, choque de demanda, reentrenos, caída de MLflow y down/up
PYTHONPATH=src python scripts/simulador.py --levantar --proyecto novamercado-sim \
  --corte 2025-10-31 --inicio 2025-11-28 --fin 2026-02-28 --choque 1.2 --choque-desde 2026-02-01 \
  --reentrenar-cada 14 --caida-mlflow 2026-01-10 --reinicio 2026-01-24
```

El reporte queda en `evidencia/simulacion/`. `HISTORICO_HASTA` solo sirve para la primera carga de
un volumen nuevo y debe quedar vacío en la evaluación. Los scripts nunca borran volúmenes: para
repetir una corrida, usar otro `--proyecto` o borrar ese proyecto a mano
(`docker compose -p <proyecto> down -v`).
