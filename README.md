# NovaMercado — Servicio de ML en producción (Laboratorio 2)

Servicio que, cada mañana y por tienda, recibe las familias de productos con sus promociones del día
y devuelve por familia la **venta esperada** y la **cantidad sugerida** (calculada a partir de los costos
de faltante y sobrante). Al día siguiente recibe las ventas reales, se reentrena con Airflow y cambia
de modelo sin reiniciarse.

| Componente | Qué hace | URL |
|---|---|---|
| `api` | FastAPI con el contrato del Anexo (`/v1/prediccion`, `/v1/observaciones`, `/admin/reentrenar`, `/modelo`, `/health`) | <http://localhost:8000> (documentación en `/docs`) |
| `db` | PostgreSQL 17.6 del producto: solicitudes, predicciones, correcciones, observaciones y reentrenos | interno |
| `mlflow` | Tracking y Model Registry (modelo `novamercado`, alias `champion`) | <http://localhost:5001> |
| `airflow-*` | Airflow 3.3.2 (LocalExecutor) con el DAG `reentrenamiento` y su propio PostgreSQL | <http://localhost:8080> (usuario `airflow` / `airflow`) |
| `bootstrap` | Tarea de una sola vez: carga los datos y entrena y registra el primer modelo | — |

Las decisiones de diseño están en [`DECISIONES.md`](DECISIONES.md); la evidencia de funcionamiento, en
[`evidencia/`](evidencia/README.md).

## Requisitos

- Docker con Docker Compose v2 y al menos **6 GB de RAM** asignados a Docker (el stack usa ~2 GB).
- Puertos libres: **8000** (API), **5001** (MLflow), **8080** (Airflow).
- Conexión a internet la primera vez, para descargar las imágenes base y las dependencias.

## Levantar desde cero

### 1. Colocar los datos publicados

Los datos **no están en el repositorio ni en las imágenes**. Descomprime el archivo publicado en el
GES dentro de la carpeta **`data/`** de la raíz del repositorio (créala si no existe), de modo que
los CSV queden directamente dentro de ella:

```
pd-lab-2/
└── data/
    ├── historico_ventas.csv
    ├── tiendas.csv
    ├── familias.csv
    ├── feriados.csv
    ├── costos.csv
    ├── catalogo_errores.md      (opcional, no lo usa el servicio)
    └── ejemplos/                (opcional, no lo usa el servicio)
```

La carpeta se monta en los contenedores en `/datos`, **en solo lectura**. Para usar otra carpeta,
define `DATOS_DIR=/ruta/absoluta` en `.env`.

### 2. Configuración y arranque

```bash
cp .env.example .env
```

```bash
docker compose up
```

`.env.example` funciona **sin editar** (sus valores son de desarrollo local, no credenciales reales).
En Linux, si los logs de Airflow dan errores de permisos, pon en `.env` `AIRFLOW_UID=$(id -u)`.

`docker compose up` construye las imágenes la primera vez (unos minutos) y luego:

1. `bootstrap` espera a MLflow, carga los CSV en la BD, entrena el modelo, lo registra en MLflow y
   le asigna el alias `champion`. Termina y queda en `Exited (0)`: es lo esperado.
2. `api` arranca de inmediato con `/health` en **503** (aún sin modelo) y carga el modelo en cuanto
   aparece el alias.
3. `/health` pasa a `ok` normalmente **entre 1 y 2.5 minutos** después del `up` (sin contar la
   construcción de las imágenes); el límite exigido es 10 minutos.

Al volver a levantar (`docker compose up` tras un `stop` o un `down` sin `-v`), el bootstrap detecta
que los datos y el modelo ya existen (por la huella de los archivos) y **no repite el trabajo**; la
API carga el modelo de su caché en el volumen y queda en `ok` en segundos.

### 3. Verificar

```bash
curl -s localhost:8000/health
```

```bash
curl -s localhost:8000/modelo
```

```bash
curl -s -X POST localhost:8000/v1/prediccion -H 'Content-Type: application/json' \
  -d '{"tienda_id": 5, "fecha": "2026-03-01", "familias": [{"familia_id": "bebidas", "en_promocion": 8}, {"familia_id": "LÁCTEOS", "en_promocion": 0}]}'
```

La última responde 200 con las **12 familias** (las que faltan se predicen con `en_promocion = 0`)
y `calidad.estado = "advertencia"`, informando que se reparó `LÁCTEOS` y que faltaban familias.

```bash
curl -s -X POST localhost:8000/v1/observaciones -H 'Content-Type: application/json' \
  -d '{"tienda_id": 5, "fecha": "2026-03-01", "ventas": [{"familia_id": "bebidas", "ventas": 1292}]}'
```

Reenviar la misma observación responde `registros_guardados = 0` (idempotente).

### 4. Reentrenar

```bash
curl -s -X POST localhost:8000/admin/reentrenar
```

Responde **202** con `{"id": "..."}` (o 503 si MLflow, Airflow o la BD no están disponibles). Si
ya hay una ejecución en curso, devuelve el `id` de esa misma ejecución. El estado se consulta con:

```bash
curl -s localhost:8000/admin/reentrenar/<id>
```

`estado` pasa de `en_curso` a `exitoso` o `fallido`; al terminar incluye `promovido`, `version` (la
vigente) y `motivo`. Un reentreno tarda normalmente menos de 1 minuto. El DAG usa el histórico y
las observaciones registradas en la BD, compara al retador con el vigente en las mismas ventanas
recientes y solo mueve el alias `champion` si cumple el criterio (DECISIONES §4). La API consulta el
alias cada 20 s y cambia de modelo **sin reiniciarse**. Las ejecuciones se ven en Airflow (DAG
`reentrenamiento`) y los experimentos y versiones, en MLflow (experimento `novamercado`).

### 5. Detener

```bash
docker compose down
```

Conserva los volúmenes (BD, MLflow, caché del modelo, Airflow). **No usar `docker compose down -v`**:
borra todo el estado y obliga a repetir el arranque en frío.

## Operación y resiliencia

- **MLflow caído:** la API sigue prediciendo con el modelo en memoria; `/health` pasa a
  `degradado` (con `motivo`) en ~10 s y vuelve a `ok` en ~10 s cuando MLflow regresa. Mientras
  tanto, reentrenar responde 503.
- **Reentreno fallido o que no gana:** el alias `champion` no se mueve; la API sigue con el vigente.
- **Fechas:** el servicio nunca usa el reloj del sistema; las fechas válidas salen del histórico y
  de `feriados.csv`. Al predecir la fecha `D` solo usa observaciones con `fecha < D` (filtro en SQL).

## Estructura del repositorio

```
api/                FastAPI: aplicación, routers delgados y dependencias
src/novamercado/
  contrato/         modelos Pydantic de entrada (tolerante) y salida (estricto, exacto al Anexo)
  limpieza/         normalizadores y tabla declarativa de políticas (reparar / advertir / rechazar)
  features/         una sola función de features (la misma al entrenar y al servir)
  modelo/           baseline, HistGradientBoosting, cuantil por familia, métricas, criterio
  db/               esquema, repositorios y migraciones
  servicio/         carga, caché y hot-reload del modelo, health, clientes de MLflow y Airflow
  bootstrap.py      carga inicial y primer modelo
dags/               DAG `reentrenamiento` (solo orquesta: llama a novamercado.modelo)
docker/             Dockerfiles de app, MLflow y Airflow
tests/              pytest (no necesitan MLflow ni Airflow corriendo)
scripts/            EDA, backtest, smoke, simulador y generador de evidencia
evidencia/          EDA, evaluación del modelo, smoke, simulación y evidencia de operación
```

Archivos de contexto del trabajo con IA: [`CLAUDE.md`](CLAUDE.md) (reglas del proyecto para el
agente), [`PLAN.md`](PLAN.md) (plan por fases), [`BITACORA.md`](BITACORA.md) (una entrada por
sesión) y [`.claude/`](.claude/) (comandos `/iniciar-fase` y `/cerrar-fase`).

## Desarrollo

```bash
git config core.hooksPath .githooks      # ganchos locales: bloquean datos/credenciales y push a main
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -e .
ruff check . && ruff format --check .
pytest -q
```

Las pruebas de integración con PostgreSQL se activan con `TEST_DATABASE_URL`; sin ella se omiten.
La CI (GitHub Actions) corre lint, pruebas, validación de Compose, el DAG con Airflow 3.3.2 y, en
`main` y en los tags `entrega-*`, el smoke de punta a punta con datos sintéticos. Todo cambio entra
a `main` por PR con la CI en verde (squash).

## Pruebas de punta a punta (smoke, simulador y evidencia)

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

# Evidencia de operación (respuestas válidas y defectuosas, reentrenos, MLflow, Airflow)
PYTHONPATH=src python scripts/evidencia.py --levantar --proyecto novamercado-evidencia
```

Los reportes quedan en `evidencia/`. `HISTORICO_HASTA` solo sirve para la primera carga de un
volumen nuevo y debe quedar vacío en la evaluación. Los scripts nunca borran volúmenes: para repetir
una corrida, usar otro `--proyecto` o borrar ese proyecto a mano (`docker compose -p <proyecto> down -v`).
