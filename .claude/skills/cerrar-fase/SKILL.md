---
name: cerrar-fase
description: Cierra la sesión de trabajo del Laboratorio 2 (NovaMercado). Revisa lo hecho (git, tests, archivos), actualiza los checkboxes de PLAN.md, registra la sesión en BITACORA.md y entrega un resumen de lo obtenido más el plan para el día siguiente. Úsalo al terminar de trabajar, con "/cerrar-fase".
argument-hint: "[notas opcionales sobre la sesión]"
---

# /cerrar-fase — Cerrar la sesión

Objetivo: dejar **registrado y verificable** lo logrado hoy y que mañana `/iniciar-fase` sepa
exactamente por dónde seguir. Responde siempre en español.

## Paso 1 — Reunir evidencia (en paralelo, solo lectura)

1. `PLAN.md` y la entrada abierta de hoy en `BITACORA.md` (la marcada "_(sesión abierta)_";
   si no existe, tomar la última entrada como referencia y crear una nueva al final).
2. Si es repo git: `git status --short`, `git diff --stat`, y `git log --oneline` de los commits
   hechos desde el inicio de la sesión (comparar con la fecha de la entrada / el último commit
   mencionado en la bitácora anterior).
3. Lo trabajado en esta conversación: archivos creados/modificados, comandos ejecutados,
   decisiones tomadas, problemas encontrados.
4. `$ARGUMENTS`: notas que el usuario quiera dejar.

## Paso 2 — Verificar (no confiar solo en la memoria de la conversación)

- Si hay `tests/`: correr `pytest -q` y anotar el resultado real (pasan / fallan / cuáles).
- Si la fase tiene contenedores levantados: `docker compose ps` y, si la API está arriba,
  `curl -s localhost:8000/health`.
- Para cada tarea que se va a marcar `[x]`, confirmar que su criterio se cumple (el archivo existe,
  el test pasa, el endpoint responde). Si no se pudo verificar, dejarla `[~]` y decirlo.

## Paso 3 — Actualizar PLAN.md

- `[x]` las tareas terminadas y verificadas; `[~]` las empezadas; dejar `[ ]` el resto.
- Si surgieron tareas nuevas necesarias, añadirlas a la fase correspondiente con el siguiente ID libre.
- Si se resolvió una decisión de "Riesgos y decisiones abiertas", marcarla como resuelta y
  apuntar dónde quedó documentada.
- Si **todas** las tareas de la fase quedaron `[x]`, la fase está cerrada: indicarlo en el resumen.

## Paso 4 — Actualizar DECISIONES.md (si aplica)

Si hoy se tomó una decisión técnica o de producto (política de datos, features, cantidad sugerida,
criterio de reemplazo, versiones, arquitectura), agregarla o actualizarla en `DECISIONES.md` con
**decisión · alternativas · justificación**. Si el archivo aún no existe, anotarla en la bitácora
como pendiente de pasar a `DECISIONES.md`.

## Paso 5 — Escribir la entrada en BITACORA.md

Reemplazar la entrada abierta de hoy (o crear una al final) con:

```
## Sesión AAAA-MM-DD — Fase N (<inicio|retoma>)
**Fase:** N — <nombre>
**Estado al cerrar:** <Fase N cerrada | Fase N en progreso: X de Y tareas>

### Logrado
- FN.a <qué se hizo, archivos clave>
- ...

### Verificación
- pytest: <N pasan / M fallan | sin tests aún>
- <otras verificaciones: health, compose ps, tiempos medidos>

### Decisiones tomadas
- <decisión> → documentada en DECISIONES.md §<sección>  (o "ninguna")

### Pendientes / bloqueos
- <lo que quedó a medias y por qué; errores sin resolver con su mensaje resumido>

### Próxima sesión
- Empezar por: <tarea con ID y primer paso concreto>
- Luego: <2–3 tareas más>
- Tener en cuenta: <riesgo, decisión abierta o algo que se debe revisar>
```

La sección "Próxima sesión" debe ser **accionable** (primer comando o archivo a abrir), porque es
lo primero que leerá `/iniciar-fase`. Si la fase quedó cerrada, la próxima sesión apunta a la
siguiente fase del `PLAN.md`.

## Paso 6 — Commit, push y PR (con permiso; nunca a `main`)

Si es un repo git y hay cambios:
1. Confirmar que la rama actual **no** es `main` (si lo es, crear una rama convencional y mover ahí
   los cambios antes de commitear).
2. Auditar lo que entra: `git status --short` y `git diff --cached --name-only` sin `data/`, `.csv`,
   `.pdf` ni `.env`. `PLAN.md`, `BITACORA.md`, `CLAUDE.md` y `.claude/` **sí** se versionan: incluir
   sus cambios en el commit. Nunca `git add -f`.
3. Proponer un mensaje convencional en español (`feat: contrato y limpieza de solicitudes`); puede
   citar los IDs del plan (p. ej. `(F3.3, F3.4)`). Mantener la firma de Claude (`Co-Authored-By`).
4. **Preguntar** antes de commitear. Con el sí: commit → `git push -u origin <rama>`.
5. Si la rama no tiene PR: proponer `gh pr create --base main --title "<igual al commit>"` con la
   plantilla del repo (Qué cambia / Pruebas) y crearlo solo con permiso. Si el trabajo de la
   rama está terminado, ofrecer `gh pr merge --auto --squash --delete-branch` (se fusiona solo cuando
   el CI pasa). Nunca merge local ni push a `main`.
6. Mostrar el estado de los checks (`gh pr checks`) sin quedarse esperando; si fallan, anotarlo en
   "Pendientes" de la bitácora.

## Paso 7 — Resumen final al usuario

Mostrar en el chat:

```
## Cierre de sesión — AAAA-MM-DD
**Fase N — <nombre>:** <cerrada ✅ | X de Y tareas (▓▓▓░░░)>

### Lo que se obtuvo hoy
- ...

### Estado verificado
- tests, servicios, commits, rama, PR y estado del CI

### Pendiente
- ...

### Mañana
1. <primera tarea, con ID y primer paso>
2. ...
3. ...

**Avance global del laboratorio:** <tareas [x] totales> / <tareas totales> · fases cerradas: <lista>
```

Cuando quede poco para la entrega, recordar las tareas de la Fase 8 que dependen de fecha
(tag `entrega-lab2` antes de la fecha límite, repo accesible, enlace en el GES).

## Reglas

- Reportar resultados reales: si un test falla o algo no se verificó, decirlo tal cual.
- No marcar `[x]` sin evidencia.
- No borrar entradas anteriores de la bitácora.