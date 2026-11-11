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
