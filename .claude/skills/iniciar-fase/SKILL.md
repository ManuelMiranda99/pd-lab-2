---
name: iniciar-fase
description: Inicia o retoma una sesión de trabajo del Laboratorio 2 (NovaMercado). Lee PLAN.md y BITACORA.md, detecta la fase actual o la indicada, muestra lo que quedó pendiente de la sesión anterior y propone el trabajo del día. Úsalo al comenzar a trabajar, con "/iniciar-fase" o "/iniciar-fase 3".
argument-hint: "[número de fase opcional, p. ej. 3]"
---

# /iniciar-fase — Iniciar o retomar una fase

Objetivo: que en menos de un minuto la persona sepa **dónde quedó, qué toca hoy y por dónde empezar**,
y que la sesión quede registrada. Responde siempre en español.

## Paso 1 — Reunir contexto (en paralelo, solo lectura)

Desde la raíz del proyecto (la carpeta que contiene `PLAN.md`):

1. Leer `PLAN.md` completo.
2. Leer `BITACORA.md`, en especial la **última entrada** y su sección "Próxima sesión".
3. Si existe, leer `CLAUDE.md` y la sección relevante de `DECISIONES.md`.
4. Si es un repo git: `git status --short`, `git log --oneline -10`, rama actual,
   y si hay commits sin subir (`git status -sb`).
5. Si la fase involucra contenedores (Fase 5 en adelante): `docker compose ps` (sin levantar nada).

Si `PLAN.md` o `BITACORA.md` no existen, avisar y detenerse.

## Paso 2 — Determinar la fase

- Si el usuario pasó un argumento (`$ARGUMENTS`, p. ej. `3`), esa es la fase.
- Si no, la fase actual es la **primera fase con alguna tarea `[ ]` o `[~]`**.
- Determinar si es **inicio** (todas las tareas de la fase en `[ ]` y la bitácora no la menciona)
  o **retoma** (hay tareas `[x]`/`[~]` o la última entrada de la bitácora es de esa fase).
- Si se pidió una fase cuyas fases previas tienen tareas pendientes que son prerrequisito
  (p. ej. Fase 6 sin Fase 5), advertirlo y preguntar si continuar.

## Paso 3 — Revisar el estado real

- Contrastar lo que dice la bitácora con lo que hay en disco/git: si una tarea figura `[x]`
  pero sus archivos no existen, o hay cambios sin commitear de la sesión anterior, señalarlo.
- Si existen pruebas (`tests/`), correr `pytest -q` rápido para saber si se parte de verde o rojo
  (si tarda más de ~2 min, correrlo en segundo plano o saltarlo y decirlo).

## Paso 4 — Mostrar el briefing

Formato (conciso, sin relleno):

```
## Fase N — <nombre>  ·  <Inicio | Retoma>
**Objetivo de la fase:** <una línea, del PLAN>
**Avance:** X de Y tareas  (▓▓▓░░░)

### Dónde quedamos
<2–4 viñetas: último "Logrado" y "Pendientes/bloqueos" de la bitácora; estado de git/tests>

### Pendiente en esta fase
- [~] FN.a ...   ← en progreso
- [ ] FN.b ...
(solo las no terminadas, con su ID)

### Propuesta para hoy
1. <tarea concreta, con ID>  — por qué primero
2. ...
(3–5 pasos, alcanzables en una sesión; basarse en "Próxima sesión" de la bitácora)

### Decisiones abiertas que afectan hoy
<solo si las hay, de "Riesgos y decisiones abiertas" o la bitácora>
```

Incluir siempre los requisitos del PDF que esa fase cubre (columna "Requisito" de la
matriz de cumplimiento) para no perder de vista lo que se evalúa.

## Paso 5 — Registrar el inicio

1. Marcar como `[~]` en `PLAN.md` la tarea con la que se va a empezar (si estaba `[ ]`).
2. Añadir al final de `BITACORA.md` el encabezado de la sesión abierta:

```
---

## Sesión AAAA-MM-DD — Fase N (<inicio|retoma>)
**Fase:** N — <nombre>
**Estado al cerrar:** _(sesión abierta)_
**Plan del día:** FN.a, FN.b, ...
```

   Usar la fecha real de hoy. Si ya existe una entrada de hoy marcada "_(sesión abierta)_",
   no duplicarla: retomarla.

## Paso 5b — Rama de trabajo (nunca se trabaja en `main`)

Si es un repo git y la Fase 0 ya dejó `main` protegida:
1. Si la rama actual es `main`: `git switch main && git pull --ff-only`, y crear una rama con
   nombre convencional según lo que se hará hoy: `feat/…`, `fix/…`, `test/…`, `docs/…`, `ci/…`,
   `chore/…`, `datos/…` (p. ej. `feat/contrato-limpieza`).
2. Si ya hay una rama de trabajo con un PR abierto de la sesión anterior (`gh pr status`), retomarla
   y mostrar el estado de sus checks (`gh pr checks`).

Durante la Fase 0, antes del primer commit, la única excepción es el commit inicial en `main` (F0.11).

## Paso 6 — Arrancar

Preguntar en una línea si se empieza con el paso 1 propuesto o se prefiere otro, y al recibir
respuesta comenzar a trabajar. Durante la sesión, mantener `[~]`/`[x]` en `PLAN.md` al día cuando
una tarea cambie de estado.

## Reglas

- No inventar avance: el estado sale de `PLAN.md`, la bitácora, git y los tests.
- No hacer commits ni push en este comando (solo crear/cambiar de rama).- Recordar las reglas duras del lab cuando apliquen a la fase: contrato exacto del Anexo,
  no usar el reloj del sistema, limpieza fuera de los endpoints, datos/credenciales fuera del repo,
  servicio `mlflow`, versiones fijas.
