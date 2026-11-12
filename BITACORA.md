# Bitácora de sesiones — Laboratorio 2

> La mantienen `/iniciar-fase` y `/cerrar-fase`. La entrada más reciente va **al final**.
> Cada entrada cierra con "Próxima sesión", que es lo primero que lee `/iniciar-fase`.

---

## Sesión 2026-11-10 — Planificación
**Fase:** Previa a Fase 0
**Estado al cerrar:** planificación terminada

### Logrado
- Revisión completa del PDF del laboratorio y de los archivos de `data/`.
- `PLAN.md` con 9 fases (0–8), tareas con ID y matriz de cumplimiento contra el PDF.
- Comandos `/iniciar-fase` y `/cerrar-fase` en `.claude/skills/`.

### Hallazgos de los datos
- Histórico: 2024-03-01 → 2026-02-28; 12 familias en todas las tiendas.
- Tienda 10 abrió el 2025-03-15 (solo 351 días de historia).
- `feriados.csv` llega hasta 2026-12-31: el periodo de evaluación cae entre marzo y diciembre de 2026.

### Decisiones tomadas
- (ninguna técnica aún; ver "Riesgos y decisiones abiertas" en PLAN.md)

### Pendientes / bloqueos
- Ninguno.

### Revisión del plan (misma fecha, segunda parte)
- 4 revisiones independientes: cobertura de requisitos, arquitectura/operación, datos/modelo
  (con backtest), DVC/CI/privacidad. PLAN.md reescrito a la versión 2.
- Cambios grandes: esqueleto operativo de punta a punta como Fase 1; Fase 0 con CI y protección
  de `main` (luego se descartaron DVC/R2 y la privacidad del uso de IA; ver decisiones abajo).
- Backtest: HGB + cuantil τ del ratio → costo −27 % vs sugerir la predicción, −40 % vs baseline.
- Skills ajustadas: trabajo en ramas, commit/PR con permiso, nunca a `main`.

### Próxima sesión
- Iniciar **Fase 0** empezando por F0.1 (`git init`).
- Luego F0.2–F0.4 (ganchos, `CLAUDE.md`, `.gitignore`) y F0.8 (`data/` solo local).
- Decisión: **sin DVC ni R2** (el docente no lo necesita); DVC local solo si hiciera falta (F0.9 opcional).
- Decisión: el uso de IA queda **visible**: `CLAUDE.md` (archivo de contexto del PDF), `.claude/`,
  `PLAN.md` y `BITACORA.md` se versionan, y se mantienen las firmas de Claude en commits y PRs.
  Sin `AGENTS.md`.
- Decidido: Airflow **3.3.2** (F0.14, como en el Lab 1) y repositorio **`pd-lab-2`** en GitHub.

---

## Sesión 2026-11-10 — Fase 0 (inicio)
**Fase:** 0 — Repo y CI
**Estado al cerrar:** Fase 0 cerrada ✅ (14 de 14 tareas)

### Logrado
- F0.1/F0.4 `git init -b main`, `.gitignore` (datos, `*.zip`, credenciales, MLflow, Python, `.DS_Store`,
  `.Rhistory`, `.claude/settings.local.json`) y `.gitattributes`.
- F0.2 Ganchos `pre-commit` y `pre-push` en `.githooks/` (versionados; `git config core.hooksPath .githooks`
  por clon). El `pre-push` permite crear `main` solo en un remoto vacío.
- F0.3 `CLAUDE.md`: propósito, stack, flujo con plan/bitácora/skills, comandos, estructura, 9 reglas
  no negociables y convenciones.
- F0.5 Estructura `api/`, `src/novamercado/{contrato,limpieza,features,modelo,db,servicio}`, `dags/`,
  `docker/`, `tests/`, `scripts/`, `evidencia/`; prueba `tests/test_paquete.py`.
- F0.6 Dependencias fijadas: `requirements-ml.txt` (numpy 2.4.6, pandas 3.1.1, pyarrow 26.0.1,
  scikit-learn 1.9.1, mlflow-skinny 3.18.0) ⊂ `requirements.txt` (fastapi 0.162.4, uvicorn 0.54.0,
  pydantic 2.14.3, psycopg 3.3.8, httpx 0.28.1) ⊂ `requirements-dev.txt` (pytest 9.2.0, ruff 0.17.4);
  `pyproject.toml`; `.venv` local con Python 3.11.10.
- F0.7 Esqueletos de `README.md` (carpeta de datos), `DECISIONES.md` (§5 stack ya escrito) y `.env.example`.
- F0.8 `data/` ignorada; zip original respaldado en `../respaldos-lab-2/datos_lab2.zip` (sha256 `a8f5e5d2…`).
- F0.9 Descartada (sin DVC).
- F0.10 `.github/workflows/ci.yml` (job `lint-y-pruebas`: auditoría de datos, ruff, pytest con
  Postgres 17.6 de servicio y `MLFLOW_TRACKING_URI` a puerto cerrado) + plantilla de PR.
- F0.11 Commit inicial `079a624` y repo público https://github.com/ManuelMiranda99/pd-lab-2.
- F0.12 `main` protegida: check `lint-y-pruebas` strict, `enforce_admins`, historial lineal, sin
  force push ni borrado; repo solo squash (título = PR), borrar rama al fusionar, auto-merge.
- F0.13 PR #1 → CI verde (54 s) → auto-merge squash (`26d216c`).

### Verificación
- pytest: 2 pasan (local y CI); ruff check y format limpios.
- Ganchos probados en repo temporal: 7 de 7 casos (bloquea datos/`.env`, commit y push a `main`).
- Push directo a `main`: rechazado por el gancho y por GitHub (GH006) al saltarlo con `--no-verify`.
- `git log --all --name-only` sin `.csv`, `.pdf`, `.env` ni `data/`.

### Decisiones tomadas
- Airflow 3.3.2 con LocalExecutor → DECISIONES.md §5.1.
- Datos fuera del repo, sin DVC → DECISIONES.md §5.2.
- CI y flujo por PR con doble barrera (ganchos + protección) → DECISIONES.md §5.3.
- Ganchos en `.githooks/` en lugar de `.git/hooks/` (versionables y reproducibles en otro clon).

### Pendientes / bloqueos
- Ninguno. La versión de la imagen `postgres` de producción se fija en la Fase 1 (CI usa 17.6).

### Próxima sesión
- Empezar por: **F1.1** `docker-compose.yaml` en una rama `feat/esqueleto-compose`; primer paso: escribir
  los servicios `db` y `airflow-db` (Postgres con tag exacto) y validar con `docker compose config`.
- Luego: F1.2 (imagen de MLflow con `--serve-artifacts`, puerto 5001), F1.3 (Airflow 3.3.2:
  api-server, scheduler, dag-processor, JWT) y F1.4 (montaje `/datos:ro` y `.dockerignore` en lista blanca).
- Tener en cuenta: en Airflow 3 configurar `AIRFLOW__API_AUTH__JWT_SECRET` y
  `AIRFLOW__CORE__EXECUTION_API_SERVER_URL`; medir RAM pico (objetivo ≤ 6 GB); el venv del DAG usa
  `requirements-ml.txt` (sin constraints de Airflow).

---

## Sesión 2026-11-10 — Fases 1, 2 y 3 (inicio)
**Fase:** 1 — Esqueleto operativo · 2 — Exploración y decisiones · 3 — Contrato y limpieza
**Estado al cerrar:** Fase 2 y Fase 3 cerradas ✅ · Fase 1: 11 de 12 (falta que el job `compose` corra en GitHub y sea check requerido)

Trabajo delegado en paralelo a tres agentes, cada uno en su worktree y rama (`feat/esqueleto-operativo`,
`docs/decisiones-eda`, `feat/contrato-limpieza`); revisión e integración en la sesión principal.

### Logrado
- **Fase 1** — `docker-compose.yaml` (`db`, `mlflow`, `airflow-db`, `airflow-init`, `airflow-apiserver`,
  `airflow-scheduler`, `airflow-dag-processor`, `bootstrap`, `api`; tags exactos, LocalExecutor),
  `docker/{app,airflow,mlflow}`, `.dockerignore` en lista blanca, `.env.example` completo.
  Bootstrap idempotente con `pg_advisory_lock`; baseline (media del mismo dow, k=4) como pyfunc;
  `ModeloCargado` + poller + caché atómica; DAG `reentrenamiento` con `ExternalPythonOperator`;
  `/health`, `/modelo`, `/v1/prediccion` (esquemas provisionales), `POST/GET /admin/reentrenar`;
  job de CI `compose` (config, mlflow, 8000, sin `latest`, hadolint, build).
- **Fase 2** — `scripts/eda.py` (9 gráficas en `evidencia/eda/`), `scripts/backtest_cantidad.py`,
  `scripts/comun_datos.py`; DECISIONES §2, §3, §4 y §6; `matplotlib==3.11.2` en dev.
- **Fase 3** — `contrato/{entrada,salida}.py`, `limpieza/{lectura,normalizadores,catalogo,politicas,
  calidad,solicitudes}.py` (43 códigos en la tabla declarativa), fixtures propias, DECISIONES §1.

### Verificación
- pytest por rama: Fase 1 64 pasan con Postgres (55 + 9 omitidas sin BD); Fase 2 4 pasan; Fase 3 607 pasan
  (con `DATOS_DIR`, incluidos los ejemplos publicados).
- Las tres ramas combinadas en un worktree de prueba: sin conflictos de archivos, **662 pasan, 9 omitidas**
  (integración sin Postgres); ruff limpio. Sin `datetime.now`/`date.today` en `src/`.
- Docker (8 GB, 10 CPU): arranque en frío → `/health` ok ~40 s; bootstrap 8.5 s; reentreno ~9 s;
  modelo promovido en uso 7–9 s; `stop mlflow` → degradado 4–8 s; vuelta a ok 9 s; `down`/`up` sin `-v`
  ok en 28 s sin reentrenar (carga desde caché); predicción 90 ms; RAM pico 1.9 GB.
  Con MLflow caído: predicción 200, reentreno 503; reentreno que falla no mueve `champion`.
- Backtest (ene–feb 2026): baseline+cuantil 310 576 vs HGB Poisson+cuantil 179 224 (−42 %).

### Decisiones tomadas
- Política de datos (E05, E06 última aparición, fechas DD/MM, rango derivado de los datos) → §1.
- Información del modelo → §2; cantidad sugerida `ceil(pred × q_f)` y pérdida Poisson → §3;
  criterio de reemplazo (≥ 0.5 %, mayoría de ventanas, mejor que baseline) → §4; reglas operativas → §6.
- Backend MLflow en Postgres del producto, jobs GenAI desactivados, FAB+JWT, imagen única, entrada del
  pyfunc con `rol` (observación gana al histórico), arranque y salud, criterio provisional → §5.4–5.10.
- El EDA corrigió hechos de la sección 0 del PLAN (24/31-dic, días de pago, Semana Santa, peor WAPE).

### Pendientes / bloqueos
- F1.12: que el job `compose` corra en GitHub y agregarlo a los checks requeridos de `main`.
- Reentreno con uno en curso: Fase 1 encola un run nuevo vs §6.1 (id del run en curso) → unificar en F6.3.
- Criterio de promoción provisional (empate promueve) → reemplazar en F5.8.
- Fila `en_curso` huérfana si Airflow no vuelve (timeout de F6.3). `/v1/prediccion` aún no persiste (F4.1/F4.2).
- Umbral de 0.5 % apoyado en una sola comparación (sin bootstrap por días).

### Próxima sesión
- Empezar por: fusionar los PR de las tres ramas (orden: contrato-limpieza → decisiones-eda →
  esqueleto-operativo, resolviendo `DECISIONES.md` al rebasar) y agregar `compose` a los checks requeridos.
- Luego: **Fase 4** — F4.1/F4.2 conectar `procesar_prediccion`/`procesar_observaciones` en los routers
  (leer `await request.body()` sin mirar `Content-Type`), reemplazar `api/esquemas_provisionales.py`
  por `novamercado.contrato` y persistir solicitudes, predicciones y correcciones.
- Tener en cuenta: `code_paths` hace que MLflow recargue `novamercado` desde la copia del modelo.

---

## Sesión 2026-11-11 — Fase 4 (inicio)
**Fase:** 4 — API completa y persistencia
**Estado al cerrar:** Fase 4 cerrada ✅ (8 de 8) · Fase 1 cerrada al abrir (F1.12 verificada: `compose` en verde y check requerido)

Toda la fase en una pasada, en la rama `feat/api-persistencia` → PR #7 (squash, CI verde).

### Logrado
- F4.1 Migración 2 en `src/novamercado/db/esquema.py`: `solicitudes` (aceptadas y rechazadas con
  `detail` y código), `predicciones` (sin UNIQUE, con `promocion_explicita` para §6.3),
  `correcciones`, `observaciones_historial`; `observaciones.solicitud_id` y CHECK de ventas ≥ 0.
  Repositorios en `db/repos.py` (texto sin NUL, cuerpo crudo truncado a 100 000 caracteres).
- F4.2 `servicio/solicitudes.py` orquesta lectura → catálogo → limpieza → predicción → guardado en
  una transacción. `servicio/prediccion.py` usa `contrato.sanear_prediccion` y convierte un fallo
  del modelo en 503.
- F4.3 Upsert `ON CONFLICT … WHERE ventas IS DISTINCT FROM … RETURNING`; reenvío idéntico → 0.
- F4.4 Manejadores en `api/main.py`: validación → 422 con texto, 400 → 422, `psycopg.Error` → 503.
- F4.5/F4.6 `/modelo` y `/health` responden con los modelos de `novamercado.contrato`; se borró
  `api/esquemas_provisionales.py`. `/admin` también usa el contrato.
- F4.7 Endpoints `def`; solo la dependencia que lee el cuerpo es asíncrona.
- F4.8 `tests/test_api_persistencia.py` (32 casos) + ajustes en las pruebas del esqueleto
  (catálogo sintético inyectable).
- Catálogo de la limpieza leído de la BD con caché (`servicio/catalogo.py`).

### Verificación
- pytest con Postgres 17.6 (`TEST_DATABASE_URL`): **698 pasan, 0 omitidas**; ruff limpio. CI de #7 en verde.
- Docker: migración 2 aplicada sobre el volumen existente. Predicción ~75 ms, observación ~14 ms.
  E01–E10 con las políticas documentadas; cuerpo vacío, lista, JSON roto y `null` → 422.
  `stop mlflow` → `degradado` en ~10 s, predicción 200 en 59 ms; vuelve a `ok` ~15 s tras `start`.

### Decisiones tomadas
- Persistencia de solicitudes (también rechazadas), catálogo desde la BD y orden de los errores
  (422 de lectura antes que 503 de catálogo) → DECISIONES.md §5.11.

### Pendientes / bloqueos
- Reentreno con uno en curso (§6.1) y fila `en_curso` huérfana → F6.3/F6.4.
- Criterio de promoción provisional (empate promueve) → F5.8.
- Las claves desconocidas se guardan en `solicitudes.claves_ignoradas`, no en `correcciones`.
- El catálogo queda en caché de por vida del proceso: si el bootstrap carga datos distintos, hay
  que reiniciar la API (hoy los datos no cambian en caliente).

### Próxima sesión
- Empezar por: **Fase 5** — F5.1 `src/novamercado/features/` con la función única de variables de
  DECISIONES §2.2 (lags a la fecha del último dato, `dias_desde_ultimo_dato`, calendario, feriados
  con cruce exacto de ciudad), partiendo de `features/panel.py` y `features/baseline.py`.
- Luego: F5.2 validación rolling origin → F5.3 HistGradientBoosting (pérdida Poisson) → F5.4/F5.5
  métricas, costo y cuantil τ por familia.
- Tener en cuenta: el pyfunc recibe `rol` (§5.8) y `code_paths` recarga `novamercado`; usar
  `predicciones.promocion_explicita` para el `en_promocion` de entrenamiento (§6.3).

