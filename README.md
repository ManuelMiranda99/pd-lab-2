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
