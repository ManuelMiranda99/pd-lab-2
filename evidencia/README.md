# Evidencia de funcionamiento

Todo lo de esta carpeta sale de un script del repositorio y puede regenerarse (ver el README
principal, sección "Pruebas de punta a punta"). Ningún archivo contiene los datos publicados: solo
métricas, gráficas agregadas y solicitudes/respuestas construidas por los scripts.

| Qué pide el enunciado | Dónde está |
|---|---|
| Respuestas de la API ante solicitudes **válidas y defectuosas** (E01–E10 y problemas no catalogados) | [`evidencia.md` §2](evidencia.md) · [`api/respuestas.json`](api/respuestas.json) |
| **Experimentos y versiones del modelo en MLflow** (runs, métricas, linaje, alias `champion`) | [`evidencia.md` §5](evidencia.md) · [`mlflow/mlflow.json`](mlflow/mlflow.json) |
| **Ejecuciones del DAG** de reentrenamiento (una que promueve y una que no) | [`evidencia.md` §3 y §6](evidencia.md) · [`airflow/dag_runs.json`](airflow/dag_runs.json) · [`reentrenamiento/reentrenos.json`](reentrenamiento/reentrenos.json) |
| Caída temporal de MLflow (`/health` degradado → ok, la API sigue prediciendo) | [`evidencia.md` §4](evidencia.md) |
| Operación diaria simulada sin fuga: errores, choque de demanda, reentrenos periódicos, caída de MLflow, `down`/`up` y **costo vs baseline** | [`simulacion/simulacion.md`](simulacion/simulacion.md) (choque +20 %, −32.5 % de costo) · [`simulacion/choque_negativo/simulacion.md`](simulacion/choque_negativo/simulacion.md) (choque −20 %, −22.8 %) |
| Smoke de punta a punta (lo mismo que corre la CI) | [`smoke/smoke_sintetico.json`](smoke/smoke_sintetico.json) |
| Modelo: baseline vs ML, métrica de negocio y técnicas, recencia | [`modelo/evaluacion_modelo.json`](modelo/evaluacion_modelo.json) · [`modelo/recencia.json`](modelo/recencia.json) |
| EDA y backtest de la cantidad sugerida | [`eda/`](eda/) (gráficas, `resumen_eda.json`, `backtest_cantidad.json`) |

| Carpeta | Script |
|---|---|
| `evidencia.md`, `api/`, `mlflow/`, `airflow/`, `reentrenamiento/` | `scripts/evidencia.py` |
| `simulacion/` | `scripts/simulador.py` |
| `smoke/` | `scripts/smoke.py` |
| `modelo/` | `scripts/evaluar_modelo.py`, `scripts/evaluar_recencia.py` |
| `eda/` | `scripts/eda.py`, `scripts/backtest_cantidad.py` |
