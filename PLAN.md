# Plan por fases — Laboratorio 2: Servicio de ML en Producción (NovaMercado)

> Fuente: `Laboratorio_2_PD_26_c.pdf` + archivos en `data/`. **Versión 2** (2026-11-10): revisada
> por 4 revisiones independientes (cobertura de requisitos, arquitectura/operación, datos/modelo,
> CI/privacidad). Cambio principal: un **esqueleto operativo de punta a punta** va justo después
> del setup, para atacar primero lo que más pesa en la evaluación (la operación).
>
> Se versiona en el repo junto con `BITACORA.md`, `CLAUDE.md` y `.claude/`. Lo leen/actualizan `/iniciar-fase`
> y `/cerrar-fase`; cada sesión se registra en `BITACORA.md`.
>
> Convención: `- [ ]` pendiente · `- [~]` en progreso · `- [x]` terminado. IDs `F#.#` **solo** para
> uso interno: nunca en commits, ramas ni PRs.

---

## 0. Resumen del laboratorio

**Producto:** cada mañana, por tienda, recibe las familias con sus promociones del día y devuelve
por familia `venta_esperada` y `cantidad_sugerida`. Al día siguiente recibe las ventas reales.
Se reentrena vía endpoint + Airflow y cambia de modelo sin reinicio.

**Se evalúa (con un programa automático):** contrato exacto · políticas ante datos sucios **no
catalogados** coherentes con lo documentado · reentrenos periódicos · caída temporal de MLflow ·
`down`/`up` sin borrar volúmenes · cambios de demanda · **costo de las sugerencias**.

**Stack obligatorio:** FastAPI (puerto 8000) · Pydantic · PostgreSQL propio (separado del de
Airflow) · MLflow (servicio `mlflow`, Registry con alias) · Airflow · Docker Compose · pytest.

**Herramientas propias (como en el Lab 1):** GitHub Actions por PR
· `main` protegida, todo entra por PR (squash) · flujo con IA visible en el repo (CLAUDE.md, `.claude/`, plan, bitácora, firmas de Claude).

### Tiempos y reglas duras
| Regla | Límite |
|---|---|
| `/health` = ok tras `docker compose up` (sin build) | ≤ 10 min (objetivo < 5) |
| Duración de un reentrenamiento | ≤ 10 min |
| Modelo promovido en uso, sin reinicio | ≤ 2 min tras terminar el DAG |
| `/health` → `degradado` al caer MLflow / → `ok` al volver | < 1 min / < 2 min |
| Validar fechas | **nunca** con el reloj del sistema |
| Features al predecir `D` | solo observaciones `< D` (filtro en SQL) |
| Datos y credenciales | fuera de imágenes y del repo (`data/` ignorada por git) |

### Hechos verificados de los datos
- Histórico 2024-03-01 → 2026-02-28 (730 días, 83 052 filas), sin huecos ni duplicados; 12 familias en todas las tiendas.
- **Tienda 10** abre 2025-03-15 (351 días) con **rampa de ~4 meses** (379/día la 1.ª semana → ~868 en julio). WAPE con baseline 0.249; la peor es la tienda 9 (0.254) — EDA 2026-11-10.
- **Tienda 7 cerrada** 2024-09-09 → 2024-09-15 (todo en 0): excluir de entrenamiento y de lags.
- Ceros reales e intermitentes en bebé/mascotas en tiendas express (4, 9, 10).
- `feriados.csv`: 2024-03-28 → **2026-12-31** (el periodo evaluado cae en mar–dic 2026, posiblemente con huecos).
- 1-ene y 25-dic **abiertos** (~0.4× y ~0.48×); Semana Santa 0.89–1.07× según el día.
- `medio_dia` **sube** ventas: 24-dic 1.96×, 31-dic 2.32× (licores 4.3× y 5.4×, carnes 3–3.6×); 26–30 dic ~1.2×; diciembre ×1.19, enero ×0.91 (factores del EDA: día / mediana del mismo dow ±4 semanas).
- **Días de pago no están completos en feriados.csv**: día 1 (1.13×), 15 (1.18×), 16 (1.14×), último día (1.19×; el penúltimo no sube) → construir la variable.
- Feriados locales casi sin efecto salvo Asunción en Guatemala (0.85–0.90×). Cruce `ambito`=`ciudad` **exacto** (Mixco ≠ Guatemala).
- Tendencia +6–9 % interanual; promoción con efecto monótono (+2.3 % a +6.8 % por producto en log); `en_promocion` 0–19.

### Cantidad sugerida (newsvendor) — validado con backtest
Óptimo = cuantil `τ = c_f/(c_f+c_s)`:
abarrotes/bebidas/mascotas 0.833 · lácteos 0.455 · carnes 0.429 · panadería 0.286 · frutas_verduras 0.333 ·
limpieza 0.909 · cuidado_personal 0.909 · licores 0.952 · snacks 0.769 · bebé 0.882.

Backtest (validación 60 días, ene–feb 2026): costo de sugerir la predicción vs **predicción × cuantil τ del
ratio real/predicho por familia**: baseline 378 765 → 315 341 (−17 %); HistGradientBoosting 260 548 →
**190 252 (−27 %)**; HGB+cuantil es −40 % vs el mejor baseline. Cobertura empírica ≈ τ. El multiplicativo
supera al aditivo y a la regresión cuantílica directa.

---

## Fase 0 — Repo y CI  ✅ cerrada (2026-11-10)
**Objetivo:** repo publicado con `main` protegida, datos fuera de git y CI corriendo.

- [x] F0.1 `git init`. `PLAN.md`, `BITACORA.md`, `CLAUDE.md` y `.claude/` **se versionan**.
- [x] F0.2 Ganchos locales (versionados en `.githooks/`, activos con `core.hooksPath`): `pre-commit` (bloquea `data/`, `.csv`, `.pdf`, `.env` y
      commits en `main` salvo el inicial) y `pre-push` (rechaza push a `main`).
- [x] F0.3 `CLAUDE.md` del proyecto (archivo de contexto que exige el PDF): propósito, estructura,
      comandos, reglas del dominio (contrato exacto, sin reloj del sistema, limpieza fuera de los
      endpoints, solo datos `< D`), flujo de ramas/PR, y referencia a `PLAN.md`/`BITACORA.md` y a los
      comandos `/iniciar-fase` y `/cerrar-fase`.
- [x] F0.4 `.gitignore`: `/data/`, `*.csv`, `*.parquet`,
      `*.pkl`, `*.joblib`, `*.pdf`, `.env`, `.env.*`, `!.env.example`, Python, `mlruns/`,
      `mlartifacts/`, `logs/`, `.DS_Store`, `.Rhistory`, editores. `.gitattributes` con `*.sh text eol=lf`.
- [x] F0.5 Estructura:
  ```
  api/              # FastAPI: app, routers delgados, dependencias
  src/novamercado/
    contrato/       # Pydantic de entrada (tolerante) y salida (estricto)
    limpieza/       # normalizadores + tabla declarativa de políticas
    features/       # una sola función de features (entrenamiento = servicio)
    modelo/         # baseline, ML, cuantiles, métricas, pyfunc, criterio de promoción
    db/             # esquema, repositorios, migraciones
    servicio/       # carga/caché/hot-reload del modelo, health, clientes MLflow/Airflow
    sintetico.py    # generador de datos sintéticos (CI y smoke)
  dags/             # DAG reentrenamiento
  docker/           # Dockerfiles y entrypoints (bootstrap, airflow-init)
  tests/            # pytest + fixtures propias (sin datos publicados)
  scripts/          # simulador, smoke
  evidencia/        # capturas y respuestas de ejemplo
  ```
- [x] F0.6 Entorno Python 3.11 con dependencias fijadas: `requirements.txt` (servicio),
      `requirements-ml.txt` (compartido API/bootstrap/DAG), `requirements-dev.txt` (ruff, pytest),
      `pyproject.toml` con ruff y pytest.
- [x] F0.7 Esqueletos de `README.md`, `DECISIONES.md` y `.env.example`.
- [x] F0.8 Datos: `data/` queda solo en local e ignorada por git (los publica el docente en el GES y
      él mismo los coloca en la carpeta del README). Copia del zip original en
      `../respaldos-lab-2/datos_lab2.zip` (sha256 `a8f5e5d2…`; los archivos vienen en la raíz del zip).
- [x] F0.9 _(descartada: sin DVC, ver DECISIONES.md 5.2)_ _(opcional, solo si hace falta versionar datos derivados)_ DVC con remoto **local**
      (`dvc remote add -d local <carpeta fuera del repo>`), sin nube. Por defecto: **no usar DVC**.
- [x] F0.10 `.github/workflows/ci.yml` con job `lint-y-pruebas`: chequeo "sin datos ni credenciales
      versionados", ruff check + format, pytest con Postgres de servicio y `MLFLOW_TRACKING_URI`
      apuntando a un puerto cerrado. Plantilla de PR (`.github/pull_request_template.md`).
- [x] F0.11 Primer commit directo a `main` (única excepción) + `gh repo create pd-lab-2 --public --source . --push`.
      Auditar antes: `git ls-files` sin datos ni `.env`.
- [x] F0.12 Proteger `main`: check requerido `lint-y-pruebas`, `strict`, `enforce_admins`, historial
      lineal, sin force push ni borrado. Repo: solo squash, título = PR, borrar rama
      al mergear, auto-merge.
- [x] F0.13 Probar el flujo: rama `chore/…` → PR → CI verde → squash. Confirmar que `git push origin main` es rechazado.
- [x] F0.14 **Versión de Airflow decidida: 3.3.2** (`apache/airflow:3.3.2-python3.11`, la misma del
      Lab 1), LocalExecutor. Anotada en DECISIONES.md §5.1.

**Criterio de terminado:** clon limpio + `pytest` en verde en CI; `git log --all --name-only` sin datos ni credenciales.

---

## Fase 1 — Esqueleto operativo de punta a punta (walking skeleton)  ✅ cerrada (2026-11-11)
**Objetivo:** con un modelo **trivial** (baseline), que todo el circuito operativo funcione en Docker.
Aquí se resuelven los riesgos más caros: Airflow, artefactos de MLflow, dependencias, RAM, arranque.

- [x] F1.1 `docker-compose.yaml`: `db` (Postgres producto), `mlflow`, `airflow-db`, `airflow-init`
      (one-shot, idempotente), componentes de Airflow con **LocalExecutor** (sin Celery/Redis),
      `bootstrap` (one-shot), `api`. Imágenes con **tag exacto**; `restart: unless-stopped`.
- [x] F1.2 MLflow: imagen propia (oficial + psycopg2), `--serve-artifacts`,
      `--artifacts-destination` en volumen, `--default-artifact-root mlflow-artifacts:/`, backend en
      Postgres (BD `mlflow`) o SQLite en volumen; verificar `--allowed-hosts` si la versión lo exige.
      Puerto host **5001** (5000 lo ocupa AirPlay en macOS). No publicar puertos de Postgres.
- [x] F1.3 Airflow: `DAGS_ARE_PAUSED_AT_CREATION=false`, `LOAD_EXAMPLES=false`,
      `DAG_DIR_LIST_INTERVAL=30`, auth de API configurada (JWT en 3.x / basic en 2.x), logs en
      volumen nombrado, `AIRFLOW_UID` en `.env.example`, pocos workers del webserver.
- [x] F1.4 Montaje de datos `${DATOS_DIR:-./data}:/datos:ro`; `.dockerignore` en **lista blanca**
      (`*` + `!api/ !src/ !dags/ !docker/ !pyproject.toml !requirements*.txt`).
- [x] F1.5 Baseline (media del mismo día de semana, últimas k semanas) empaquetado como **pyfunc**
      con `code_paths=[src/novamercado]`; entrada = solicitud limpia + panel de la BD.
- [x] F1.6 `bootstrap` idempotente bajo `pg_advisory_lock`: migrar esquema → cargar CSV en **una
      transacción** con marcador (hash, filas) → consultar alias: existe → salir; no existe → entrenar
      y registrar v1 con alias `champion`; **MLflow inalcanzable → salir con error, nunca entrenar**;
      versión sin alias → asignar alias en vez de reentrenar. Loader tolera subcarpeta extra y BOM y
      falla con mensaje claro si faltan archivos.
- [x] F1.7 API mínima: `/health`, `/modelo`, `/v1/prediccion` con baseline. La API **no** espera al
      bootstrap: arranca con 503 y carga el modelo en cuanto aparece el alias.
- [x] F1.8 Carga del modelo: `ModeloCargado` inmutable (pyfunc, nombre, alias, versión); poller en
      hilo cada 20–30 s; caché atómica en volumen (`tmp` → validar con predicción de humo →
      `os.replace` + `actual.json`); arranque alias → caché → 503. Cliente MLflow con
      `MLFLOW_HTTP_REQUEST_MAX_RETRIES=0..1`, `TIMEOUT=5`. **El camino de predicción nunca llama a MLflow.**
      Un solo worker de uvicorn.
- [x] F1.9 DAG `reentrenamiento` trivial (reentrena baseline, mueve alias) con `schedule=None`,
      `catchup=False`, `max_active_runs=1`, `dagrun_timeout=9 min`, imports pesados dentro de las
      tareas; entrenamiento en venv aislado con `requirements-ml.txt` (`ExternalPythonOperator`).
- [x] F1.10 `POST /admin/reentrenar` (id `uuid4().hex` generado por la API = `dag_run_id`; fila en
      `reentrenamientos` antes de disparar; 202 / 503) y `GET /admin/reentrenar/{id}` (404 por BD).
      Encapsular en `AirflowClient`.
- [x] F1.11 Medir: arranque en frío hasta `/health` ok, RAM pico (`docker stats`), `stop mlflow`
      → degradado, `down`/`up` sin `-v` conserva y no reentrena. Anotar números en la bitácora.
- [x] F1.12 Job de CI `compose`: `docker compose config`, servicio `mlflow` presente, puerto 8000,
      sin `latest`, hadolint, `docker compose build`. Agregarlo a los checks requeridos.

**Criterio de terminado:** desde un clon limpio + `data/` + `cp .env.example .env` → `docker compose up`
→ `/health` ok, una predicción, un reentreno que promueve y el modelo nuevo en uso sin reinicio.

---

## Fase 2 — Exploración y decisiones de diseño
**Objetivo:** fijar por escrito las decisiones grandes (los 4 temas obligatorios de DECISIONES.md).

- [x] F2.1 EDA reproducible en `scripts/` (confirmar los hechos de la sección 0 y graficar los principales).
- [x] F2.2 **Información que usa el modelo al predecir** (solo `< D`): calendario (dow, día del mes,
      día 15/16/fin de mes, mes), feriados nacional/local por ciudad exacta, eventos especiales
      (24/25/31-dic, 1-ene, Semana Santa, 31-oct, 26–30 dic), promo del día, lags "a la fecha del
      último dato disponible", días desde el último dato, medias móviles, tienda/familia/tipo,
      días desde apertura.
- [x] F2.3 **Política de datos**: reglas generales R1–R9 (ver Anexo A) + tabla E01–E10 + no
      catalogados. Decidir: E05 (predecir las 12 con promo 0 y advertir, recomendado), E06 (advertir
      con regla determinista vs rechazar), fechas ambiguas (DD/MM con advertencia), fecha > 2026-12-31
      (advertir), antes de apertura de tienda (rechazar).
- [x] F2.4 **Cantidad sugerida**: predicción × cuantil τ del ratio por familia, calibrado fuera de muestra
      en ventana reciente y recalibrado en cada reentreno; corrección de sesgo de `venta_esperada`
      (smearing o pérdida Poisson/Tweedie).
- [x] F2.5 **Criterio de reemplazo**: retador y vigente evaluados en las **mismas** ventanas recientes
      (varias, rolling origin; cuidar 31-dic/1-ene), umbral mínimo de mejora de costo, `motivo` legible
      con ambos costos.
- [x] F2.6 Reglas operativas: qué hace `POST /admin/reentrenar` con un run en curso (202 en cola vs 503)
      y con MLflow caído (503); qué `en_promocion` se usa para entrenar si hubo varias solicitudes del
      mismo día (la última válida) o ninguna (NULL + flag).
- [x] F2.7 Borrador de `DECISIONES.md` con decisión · alternativas · justificación para F2.2–F2.6 y
      F0.14, manejo de datos fuera del repo, CI y flujo por PR.

---

## Fase 3 — Contrato y limpieza (núcleo de la defensa de datos)
**Objetivo:** módulos puros (sin FastAPI) que convierten cualquier entrada en "solicitud limpia +
observaciones de calidad" o en un rechazo con motivo.

- [x] F3.1 Pydantic de **salida** exacto al Anexo (`PrediccionResponse`, `ObservacionesResponse`,
      `Calidad`, `ObservacionCalidad`, `ReentrenoStatus`, `ModeloInfo`, `Health`); sin `exclude_none`
      que borre campos obligatorios; `ok` siempre con `"observaciones": []`.
- [x] F3.2 Lectura tolerante del cuerpo crudo: BOM, `Content-Type` incorrecto, claves duplicadas en el
      JSON (`object_pairs_hook`), `NaN`/`Infinity`, booleanos donde va un número (Pydantic lax convierte
      `True` en 1: bloquear), enteros enormes antes de Postgres.
- [x] F3.3 Normalizadores generales: ids (`"T05"`, `"5"`, `5.0`, `"Tienda 5"`), familias (NFKC, acentos,
      mayúsculas, `_ - y &`, contra id **y** nombre del catálogo; difusa → advertir), enteros (`"3"`,
      `2.0`, `"1,234"`, `"812,0"`; 2.5 → advertir), fechas (ISO con hora, `DD/MM/AAAA`, `AAAA/MM/DD`,
      `AAAAMMDD`, ambigüedad), alias y capitalización de claves.
- [x] F3.4 **Tabla declarativa de políticas** en código (problema → acción → mensaje) como única fuente;
      un test verifica que la tabla de DECISIONES.md coincide.
- [x] F3.5 Motor de calidad: `campo` con **índice original** y clave recibida; `accion` solo
      `reparar`/`advertir` (rechazo = 422); estado **derivado**: algún advertir → advertencia, solo
      reparar → corregido, nada → ok. Campos extra se ignoran sin observación.
- [x] F3.6 Fechas sin reloj del sistema: rango `[2024-03-01, máx(feriados)]` + advertencia fuera de
      cobertura; **no** rechazar fechas pasadas (el ejemplo del Anexo, 2025-11-17, está dentro del
      histórico); tienda antes de su apertura.
- [x] F3.7 Observaciones: negativos (E08 → advertir y no guardar la familia), formato numérico (E10),
      decimales no enteros, duplicados (idénticos → reparar; distintos → no guardar y advertir),
      familias faltantes (guardar las recibidas y advertir), familias desconocidas.
- [x] F3.8 Fixtures **propias** en `tests/fixtures/` (sin copiar el material publicado) + generador
      `novamercado.sintetico`; tests extra sobre `data/ejemplos/` con `skip` si no existe.
- [x] F3.9 pytest: E01–E10 + ≥ 25 casos no catalogados (Anexo A) + propiedades del contrato
      (int ≥ 0, finitos, versión texto, estado coherente). Nada depende de MLflow ni de la BD.

**Criterio de terminado:** `pytest tests/test_contrato* tests/test_limpieza*` verde en CI.

---

## Fase 4 — API completa y persistencia  ✅ cerrada (2026-11-11)
**Objetivo:** reemplazar la API mínima por la completa sobre el esqueleto.

- [x] F4.1 Esquema: `solicitudes` (crudo, normalizado, estado, **también rechazadas** con `detail`),
      `predicciones` (por familia, `version_modelo`, `en_promocion`; **sin UNIQUE** por tienda/fecha),
      `correcciones` (de predicción **y** de observaciones), `observaciones` (PK tienda/fecha/familia,
      `actualizado_en`, flags de exclusión), `reentrenamientos`, histórico, marcador de bootstrap.
      Fechas como `DATE`. Índice `(tienda_id, fecha)`.
- [x] F4.2 `POST /v1/prediccion`: limpiar → panel `histórico ∪ observaciones` filtrado `fecha < D` en
      SQL → predecir con `m = estado.modelo` → saneamiento de salida (sin NaN, `ceil` a int ≥ 0,
      versión como texto) → guardar todo en una transacción **antes** de responder. Latencia < 1 s.
- [x] F4.3 `POST /v1/observaciones`: dedupe en limpieza → upsert
      `ON CONFLICT … DO UPDATE … WHERE observaciones.ventas IS DISTINCT FROM EXCLUDED.ventas RETURNING`;
      `registros_guardados` = filas devueltas (reenvío idéntico → 0); historial de cambios.
- [x] F4.4 422 con `detail` legible para todo: validación, JSON inválido, cuerpo vacío, array en raíz,
      Content-Type incorrecto. Ningún input termina en 500/400.
- [x] F4.5 `GET /modelo` desde el modelo **en memoria** (no del Registry).
- [x] F4.6 `GET /health`: tarea de fondo cada ~15 s con timeouts de 2–3 s y resultado cacheado; BD
      (`SELECT 1`), MLflow (`GET /health` con httpx), Airflow (health + DAG existe y no pausado), modelo;
      `ok` / `degradado` + `motivo`; 503 sin modelo.
- [x] F4.7 Endpoints con BD síncrona como `def` (no `async def`); nada bloqueante en el event loop.
- [x] F4.8 Tests de API con `TestClient` y `TEST_DATABASE_URL` (sin MLflow): idempotencia, solicitudes
      repetidas, rechazos registrados, test de **no fuga** (alterar la venta de `D` no cambia la
      predicción de `D`).

---

## Fase 5 — Modelo ML, cantidad sugerida y criterio de reemplazo
**Objetivo:** reemplazar el baseline por un modelo que lo mejore en costo, dentro del mismo pyfunc.

- [ ] F5.1 `features/` único (F2.2), robusto a huecos: lags a la fecha del último dato, días desde el
      último dato, **enmascarado aleatorio de lags al entrenar**; excluir cierre de tienda 7 y rampa de
      tienda 10 (o feature días desde apertura).
- [ ] F5.2 Validación temporal con varias ventanas (rolling origin).
- [ ] F5.3 Modelo ML (HistGradientBoosting / LightGBM global; con LightGBM agregar `libgomp1` a la imagen).
- [ ] F5.4 Métricas técnicas (MAE, RMSE, WAPE, sesgo) + **costo** con `costos.csv`; baseline y ML
      registrados como runs en MLflow (evidencia de mejora).
- [ ] F5.5 Cuantil τ del ratio por familia (F2.4), recalibrado en cada entrenamiento; verificar que
      reduce costo vs sugerir la predicción (objetivo ≈ −27 %).
- [ ] F5.6 Prueba de robustez: sin lags el modelo **no** es peor que el baseline (si no, respaldo de calendario).
- [ ] F5.7 Linaje en MLflow: params, métricas, sha256 de los datos, rango de fechas, nº de
      observaciones, commit; alias `champion`.
- [ ] F5.8 Criterio de reemplazo (F2.5) como función pura con tests; `motivo` legible
      (p. ej. "Costo en validación 18 210 vs 19 940 del vigente").
- [ ] F5.9 Tiempo de entrenamiento < 3 min (deja margen para la cola y el límite de 10 min).

---

## Fase 6 — Reentrenamiento, hot-reload y resiliencia (endurecimiento)
**Objetivo:** el ciclo de mejora continua completo y a prueba de fallos.

- [ ] F6.1 DAG completo: extraer histórico + observaciones (unidas con `en_promocion` de la última
      solicitud válida; F2.6) desde la BD del producto (`AIRFLOW_CONN_…` desde `.env`) → entrenar
      retador → evaluar vs vigente en la misma ventana → registrar → mover alias solo si gana.
- [ ] F6.2 Orden de escritura: registrar versión → resultado provisional → mover alias → `exitoso`;
      `on_failure_callback`/tarea `all_done` escribe `fallido` con motivo. Fallo o no-promoción **no
      toca el alias**.
- [ ] F6.3 `GET /admin/reentrenar/{id}`: fila terminal → devolverla; no terminal → consultar Airflow
      (queued/running/up_for_retry → `en_curso`; failed → `fallido`; success sin resultado → `fallido`;
      Airflow caído → `en_curso`); timeout → `fallido`. `promovido`/`version`/`motivo` en `exitoso` **y**
      `fallido`, omitidos en `en_curso`. **`version` = alias vigente al terminar** aunque no se promueva.
- [ ] F6.4 Políticas de F2.6 implementadas (run en curso, MLflow caído → 503).
- [ ] F6.5 Ponderar lo reciente / recalibrar cuantiles para cambios de demanda.
- [ ] F6.6 Hot-reload verificado ≤ 2 min (poll + swap atómico + humo; opcional aviso del DAG a la API).
- [ ] F6.7 Resiliencia MLflow verificada: predicciones siguen, degradado < 1 min, ok < 2 min; reinicio
      con MLflow caído arranca desde la caché.
- [ ] F6.8 Job de CI `dags` (`tests/test_dags.py`: sin import errors, `reentrenamiento`, sin schedule,
      `max_active_runs=1`, orden de tareas); agregarlo a los checks requeridos.

---

## Fase 7 — Simulador, smoke y pruebas end-to-end
**Objetivo:** reproducir lo que hará el evaluador antes de que lo haga.

- [ ] F7.1 `scripts/smoke.py`: health ≤ 10 min, contrato, E01 sintético, 422, idempotencia, reentreno
      ≤ 10 min, id inventado → 404, `stop/start mlflow`.
- [ ] F7.2 Job `smoke` en CI con datos **sintéticos** (en main, tag y PRs con etiqueta; luego requerido).
- [ ] F7.3 `scripts/simulador.py` sin fuga: bootstrap con **histórico truncado** (variable de entorno)
      y simular los días retenidos: observaciones de ayer → solicitudes de hoy de 10 tiendas, errores
      aleatorios (catálogo + inventados), solicitudes repetidas.
- [ ] F7.4 Escenarios: hueco de varias semanas antes de la primera solicitud; choque de nivel ±20 %;
      reentrenos periódicos; `stop mlflow` a mitad; `down`/`up` sin `-v` a mitad.
- [ ] F7.5 Validar cada respuesta contra el contrato; costo total vs baseline; reporte en `evidencia/`.
- [ ] F7.6 Ensayo desde cero en carpeta nueva (clon limpio + datos como vengan del GES), cronometrado;
      Docker con la RAM que pide el README.
- [ ] F7.7 Corregir lo que falle.

---

## Fase 8 — Documentación, evidencia y entrega
- [ ] F8.1 `README.md`: requisitos (Docker ≥ 6 GB), **carpeta donde colocar los archivos publicados**,
      `cp .env.example .env` (funciona sin editar), `docker compose up`, URLs (API 8000, MLflow 5001,
      Airflow 8080), cómo verificar y reentrenar.
- [ ] F8.2 `DECISIONES.md` final: política por tipo de problema · información que usa el modelo ·
      cantidad sugerida · criterio de reemplazo · stack, Airflow, datos fuera del repo, CI, flujo por PR.
- [ ] F8.3 `CLAUDE.md` actualizado con el estado final.
- [ ] F8.4 Evidencia en `evidencia/`: experimentos y versiones en MLflow, ejecuciones del DAG (promoción
      y no-promoción), respuestas de la API válidas y defectuosas (E01–E10 + no catalogadas).
- [ ] F8.5 **Auditoría de datos y credenciales** (vacío):
      `git log --all --name-only | grep -E '\.csv$|\.pdf$|(^|/)\.env$|^data/'`.
- [ ] F8.6 Repo accesible al equipo docente.
- [ ] F8.7 Ensayo final: `git clone --branch entrega-lab2` en carpeta nueva y levantar.
- [ ] F8.8 `git tag -a entrega-lab2` sobre `main` tras el último squash, **antes de la fecha límite**;
      `git push origin entrega-lab2`; verificar con `gh api …/git/refs/tags/entrega-lab2`.
- [ ] F8.9 Entregar en el GES solo el enlace.
- [ ] F8.10 Repaso personal: poder explicar y justificar cada módulo y decisión.

---

## Matriz de cumplimiento (requisito del PDF → tarea)

| Requisito del PDF | Tareas |
|---|---|
| Pronóstico por familia: venta esperada + cantidad sugerida | F1.5, F4.2, F5.3, F5.5 |
| Defensa del contrato: reparar / advertir / rechazar e informar | F2.3, F3.3–F3.7 |
| Reglas generales para problemas no catalogados | F2.3, F3.2–F3.4, F3.9, F7.3 |
| Contrato exacto (campos, tipos, 200/202/404/422/503) | F3.1, F3.5, F4.2–F4.6, F6.3, F7.5 |
| `calidad.estado` coherente; `ok` sin observaciones | F3.5, F3.9 |
| `cantidad_sugerida` entero ≥ 0; `version_modelo` texto | F3.1, F4.2 |
| Pydantic; limpieza en módulos con pruebas, no en endpoints | F3.1–F3.9 |
| `version_modelo` en cada respuesta | F4.2 |
| pytest sin MLflow corriendo | F0.10, F3.9, F4.8 |
| Varias solicitudes de la misma fecha | F4.1, F4.2, F2.6 |
| Registro de solicitudes, predicciones, correcciones, observaciones | F4.1–F4.3 |
| Observaciones idempotentes; `registros_guardados` | F4.3, F4.8 |
| Postgres propio, separado del de Airflow | F1.1 |
| Persistencia tras detener/levantar | F1.11, F7.4 |
| Baseline + modelo ML que lo mejore | F1.5, F5.3, F5.4 |
| Métrica de negocio + métricas técnicas | F5.4 |
| Cantidad sugerida coherente con costos | F2.4, F5.5 |
| Experimentos, modelos, linaje; alias en Registry | F1.6, F5.7 |
| Criterio automatizado de reemplazo | F2.5, F5.8, F6.1 |
| DAG con histórico + observaciones de la BD (promos de solicitudes) | F6.1, F2.6 |
| Reentreno por endpoint admin; estado consultable; `version` vigente | F1.10, F6.3 |
| Modelo nuevo en uso ≤ 2 min sin reinicio | F1.8, F6.6 |
| Fallo / no promoción no afecta al vigente | F6.2 |
| Todo con `docker compose up` + README; servicio `mlflow` | F1.1, F1.2, F8.1 |
| Arranque en frío entrena el primero; reinicio no repite | F1.6, F1.11 |
| Imágenes con versión fija; sin datos/credenciales; `.env.example` | F1.1, F1.4, F0.4, F0.7, F1.12 |
| API sigue sin MLflow; `/health` < 1 min / < 2 min | F1.8, F4.6, F6.7 |
| Listo ≤ 10 min; reentreno ≤ 10 min | F1.11, F5.9, F7.6 |
| No usar reloj del sistema | F3.6 |
| Solo observaciones `< D` al predecir | F4.2, F4.8, F5.1 |
| Repo GitHub con estructura clara + archivo de contexto (CLAUDE.md) | F0.5, F0.7, F8.3 |
| README con carpeta de datos | F8.1 |
| DECISIONES.md con los 4 temas | F2.7, F8.2 |
| Evidencia (MLflow, DAG, respuestas API) | F8.4 |
| Tag `entrega-lab2` antes de la fecha límite; repo accesible; enlace en GES | F8.6–F8.9 |

---

## Anexo A — Políticas de datos propuestas (para validar en F2.3)

| Regla | Qué cubre | Acción |
|---|---|---|
| R1 | Normalización léxica de familias contra id y nombre | Única coincidencia → reparar; difusa → advertir |
| R2 | Coerción de tipos | Sin pérdida (`"3"`, `3.0`, `"T05"`) → reparar; con pérdida (2.5) → advertir; bool/lista/objeto → inválido |
| R3 | Alias o capitalización de claves | Reparar; canónica y alias con valores distintos → rechazar (identidad) / advertir (ítem) |
| R4 | Identidad (tienda, fecha) inexistente, imposible o ilegible | Rechazar 422 |
| R5 | Ítem inválido (familia desconocida/nula) | Descartar y advertir; sin ítems válidos → rechazar |
| R6 | Valor fuera de dominio | Predicción: valor por defecto documentado y advertir. Observación: **no guardar** y advertir |
| R7 | Duplicados | Idénticos → reparar; en conflicto → advertir con regla determinista (observación: no guardar) |
| R8 | Fechas | Fuera de cobertura de feriados → advertir; antes de apertura de la tienda → rechazar |
| R9 | Claves desconocidas | Ignorar sin observación; registrar en BD |

| Caso | Política |
|---|---|
| E01 `"T05"`, `"5"` | Reparar |
| E02 `LÁCTEOS`, `Frutas y Verduras` | Reparar |
| E03 `"9"`, `8.0` | Reparar (2.5 → advertir) |
| E04 `17/11/2025`, `…T00:00:00` | Reparar (ambigua `05/06/2026` → advertir, DD/MM) |
| E05 faltan familias | Advertir; predecir las 12 con promo 0 |
| E06 familia repetida distinta | Advertir con regla determinista (o rechazar: decidir) |
| E07 `tienda` en vez de `tienda_id` | Reparar |
| E08 ventas negativas | Advertir; no guardar esa familia |
| E09 envío repetido | Upsert idempotente; `registros_guardados = 0`, estado ok |
| E10 `"1,292"`, `"50,0"` | Reparar (`"50,5"` → advertir y no guardar) |

---

## Riesgos y decisiones abiertas

- ~~Versión de Airflow~~ → **resuelto: 3.3.2** (F0.14). Implica API REST `/api/v2` con token JWT,
  componentes `api-server` + `scheduler` + `dag-processor`, y `AIRFLOW__API_AUTH__JWT_SECRET` /
  `AIRFLOW__CORE__EXECUTION_API_SERVER_URL` configurados. Misma versión en imagen y CI (constraints 3.11).
- **Huecos en el periodo evaluado**: el modelo debe tolerar lags faltantes (F5.1, F5.6).
- **Diciembre 2026** (24/31-dic hasta 4.8× en licores) es el mayor riesgo de costo si se evalúa (F2.2).
- ~~RAM del stack~~ → **medido: 1.9 GB pico** con LocalExecutor y jobs de MLflow desactivados (F1.11, DECISIONES §5.5).
- **Reentreno con uno en curso**: la Fase 1 responde 202 y encola un run nuevo; DECISIONES §6.1 dice 202 con el id del run en curso. Unificar en F6.3.
- ~~Persistencia de la API~~ → **resuelto en Fase 4** (DECISIONES §5.11).
- **Criterio de promoción provisional** (empate promueve, DECISIONES §5.10): reemplazar por §4 en F5.8.
- **Checks requeridos progresivos**: agregar a la protección solo los jobs que ya existen.
- ~~Protección de rama en repo privado~~ → **resuelto**: el repo `pd-lab-2` es público y `main` quedó protegida (F0.12).
