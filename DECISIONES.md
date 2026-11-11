# Decisiones de diseño

Cada decisión: **decisión · alternativas consideradas · justificación**. Los cuatro temas que exige
el laboratorio van primero; luego las decisiones de stack y de operación.

## 1. Política por tipo de problema en los datos

_Pendiente (Fase 2)._

## 2. Información que usa el modelo al predecir

_Pendiente (Fase 2)._

## 3. Cálculo de la cantidad sugerida

_Pendiente (Fase 2)._

## 4. Criterio de reemplazo del modelo vigente

_Pendiente (Fase 2)._

## 5. Stack y operación

### 5.1 Airflow 3.3.2 con LocalExecutor
- **Decisión:** `apache/airflow:3.3.2-python3.11`, LocalExecutor.
- **Alternativas:** Airflow 2.x; CeleryExecutor con Redis.
- **Justificación:** es la versión usada en el Lab 1 (ya conocida); LocalExecutor basta para un único
  DAG con `max_active_runs=1` y evita Redis y workers extra (~3–4 GB de RAM frente a > 6 GB).

### 5.2 Datos fuera del repositorio, sin DVC
- **Decisión:** `data/` está ignorada por git; los archivos se colocan a mano según el README y se
  montan en solo lectura en los contenedores. No se usa DVC.
- **Alternativas:** DVC con remoto en la nube (como en el Lab 1); DVC con remoto local.
- **Justificación:** el docente aporta los datos desde el GES, así que no hace falta distribuirlos;
  un remoto agrega credenciales y puntos de falla sin beneficio para la evaluación.

### 5.3 CI y flujo por PR
- **Decisión:** `main` protegida; todo entra por PR con squash y el check `lint-y-pruebas` en verde.
  Los ganchos locales (`.githooks/`) bloquean datos, credenciales y push a `main`.
- **Alternativas:** commits directos a `main`; solo ganchos locales sin protección en GitHub.
- **Justificación:** doble barrera (local y remota) contra filtraciones de datos y un historial
  lineal y revisable.
