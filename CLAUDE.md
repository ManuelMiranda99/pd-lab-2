# CLAUDE.md — Reglas del proyecto

Servicio de ML en producción para **NovaMercado** (Lab 2, Product Development). Cada mañana, por
tienda, recibe las familias con sus promociones del día y devuelve por familia `venta_esperada` y
`cantidad_sugerida`. Al día siguiente recibe las ventas reales. Se reentrena con un endpoint de
administración y un DAG de Airflow, y cambia de modelo sin reiniciarse.

Stack: Python 3.11 · FastAPI (puerto 8000) · Pydantic · PostgreSQL propio (separado del de Airflow) ·
MLflow (servicio `mlflow`, Model Registry con alias `champion`) · Airflow 3.3.2 (LocalExecutor) ·
Docker Compose · pytest · ruff · GitHub Actions.

## Estado

Producto completo (fases 0–8): contrato, limpieza, persistencia, modelo HGB + cuantil por familia,
reentrenamiento por DAG con criterio de reemplazo, hot-reload, resiliencia, smoke/simulador en CI y
evidencia en `evidencia/`. La versión entregada se identifica con el tag `entrega-lab2` sobre `main`.
Cualquier cambio posterior debe mantener verde la CI (incluido el job `smoke`) y no romper las
reglas de abajo; si cambia una política o el criterio, se actualiza `DECISIONES.md` en el mismo PR.

## Cómo se trabaja (flujo con IA, visible en el repo)

- `PLAN.md`: plan por fases (0–8) con IDs de tarea `F#.#` y matriz de cumplimiento contra el PDF.
  Los IDs son **solo internos**: nunca aparecen en commits, ramas ni PRs.
- `BITACORA.md`: una entrada por sesión; la última sección "Próxima sesión" dice por dónde seguir.
- `/iniciar-fase [N]` y `/cerrar-fase` (en `.claude/skills/`) abren y cierran cada sesión.
- `DECISIONES.md`: cada decisión con alternativas y justificación.

## Comandos

```bash
git config core.hooksPath .githooks       # una vez por clon: activa los ganchos locales
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt -e .
ruff check . && ruff format --check .     # lint (la CI falla si no pasa)
pytest -q                                 # pruebas (no necesitan MLflow corriendo)

cp .env.example .env                      # funciona sin editar
docker compose up                         # API :8000 · MLflow :5001 · Airflow :8080
docker compose down                       # conserva volúmenes; nunca usar -v en la evaluación
```

## Estructura

```
api/              FastAPI: app, routers delgados, dependencias
src/novamercado/
  contrato/       Pydantic de entrada (tolerante) y salida (estricto, exacto al Anexo)
  limpieza/       normalizadores + tabla declarativa de políticas (única fuente)
  features/       una sola función de features (entrenamiento = servicio)
  modelo/         baseline, ML, cuantiles, métricas, pyfunc, criterio de promoción
  db/             esquema, repositorios, migraciones
  servicio/       carga/caché/hot-reload del modelo, health, clientes MLflow/Airflow
  bootstrap.py    carga inicial de datos y primer modelo (idempotente por huella)
  sintetico.py    datos sintéticos para CI y smoke
dags/             DAG `reentrenamiento` (solo orquesta)
docker/           Dockerfiles y entrypoints
tests/            pytest + fixtures propias (sin datos publicados)
scripts/          EDA, backtest, smoke, simulador y generador de evidencia (proyecto -p aparte)
evidencia/        EDA, modelo, smoke, simulación y evidencia de operación (evidencia.md)
```

## Reglas no negociables

1. **Contrato exacto del Anexo del PDF**: nombres de campos, tipos y códigos (200/202/404/422/503).
   `cantidad_sugerida` entero ≥ 0, `version_modelo` como texto, `calidad.estado` coherente
   (`ok` siempre con `"observaciones": []`). Ninguna entrada termina en 500.
2. **Limpieza fuera de los endpoints**: vive en `src/novamercado/limpieza/` como funciones puras con
   pruebas. Los routers solo llaman. Las políticas (reparar / advertir / rechazar) salen de una tabla
   declarativa que coincide con `DECISIONES.md`.
3. **Nunca usar el reloj del sistema** para validar o decidir (`datetime.now()`, `date.today()`).
   Las fechas válidas se derivan de los datos (histórico y `feriados.csv`).
4. **Sin fuga temporal**: al predecir la fecha `D` solo se usan observaciones con `fecha < D`, y el
   filtro se hace en SQL.
5. **Datos y credenciales fuera del repo y de las imágenes**: `data/` está ignorada, los datos se
   montan en `/datos:ro`, las credenciales viven en `.env` (solo `.env.example` se versiona). Los
   ganchos de `.githooks/` y la CI lo verifican.
6. **El camino de predicción nunca llama a MLflow**: el modelo se carga en memoria (con caché en
   volumen) y un poller cambia de versión sin reinicio. Si MLflow cae, la API sigue prediciendo y
   `/health` pasa a `degradado`.
7. **Un fallo o un retador que no gana no toca el alias `champion`.**
8. **Versiones fijas**: imágenes con tag exacto (nunca `latest`) y dependencias con `==`.
9. **Idempotencia**: `bootstrap` y observaciones pueden repetirse sin duplicar ni reentrenar.

## Convenciones

- Git: `main` protegida; nunca commit ni push directo (lo bloquean los ganchos). Ramas `feat/…`,
  `fix/…`, `test/…`, `docs/…`, `ci/…`, `chore/…`, `datos/…`; PR con CI en verde y squash merge.
  Commits estilo Conventional Commits, en español, con la firma de Claude cuando intervino.
- Commits, push y PRs solo con permiso explícito de la persona.
- Idioma: documentación, mensajes y docstrings en español.
- Estilo: ruff (line-length 100), type hints en funciones públicas.
- Dependencias nuevas con versión fijada en `requirements.txt` (servicio), `requirements-ml.txt`
  (entrenamiento, compartido API/bootstrap/DAG) o `requirements-dev.txt`.
