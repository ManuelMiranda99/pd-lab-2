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
**Estado al cerrar:** _(sesión abierta)_
**Plan del día:** F0.1, F0.4, F0.2, F0.3, F0.8 (y si alcanza F0.5–F0.7)
