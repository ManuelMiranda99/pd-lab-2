# Decisiones de diseño

Cada decisión: **decisión · alternativas consideradas · justificación**. Los cuatro temas que exige
el laboratorio van primero; luego las decisiones de stack y de operación.

## 1. Política por tipo de problema en los datos

### 1.1 Principio general
- **Decisión:** cada problema detectado se clasifica con un código y la acción sale de una
  **tabla declarativa en código** (`src/novamercado/limpieza/politicas.py`), única fuente; los
  normalizadores solo informan el código y los routers solo llaman a la limpieza. Criterio:
  - **reparar** si la corrección es inequívoca (no hay otra lectura razonable del dato);
  - **advertir** si se responde con un supuesto (valor por defecto, interpretación elegida,
    familia descartada o no guardada);
  - **rechazar (422)** solo si falla la identidad de la solicitud (`tienda_id`, `fecha`), la
    estructura (cuerpo, JSON, raíz) o no queda ninguna familia reconocible;
  - **ignorar** lo que no afecta al resultado (claves desconocidas, BOM, `Content-Type`).
- **Alternativas:** reglas escritas dentro de cada endpoint; rechazar todo lo que no sea exacto;
  reparar todo "a la mejor interpretación" sin informar.
- **Justificación:** el enunciado pide que el servicio no se detenga pero que no invente datos y
  que la política sea general y coherente con lo documentado. Una tabla única evita que el código
  y este documento diverjan (un test compara ambas tablas) y permite tratar problemas no
  catalogados con las mismas reglas generales (tipos, alias, duplicados, dominio, fechas).

### 1.2 Reglas generales (aplican a casos no catalogados)
- **Tipos sin pérdida → reparar; con pérdida → advertir.** `"3"`, `8.0`, `"1,292"`, `"812,0"`,
  `"T05"`, `5.0` se reparan; `2.5` se advierte. Un booleano nunca es un número (se bloquea la
  conversión laxa de Pydantic: los modelos canónicos son `strict`). `NaN`/`Infinity` y enteros
  que no caben en un `integer` de PostgreSQL son inválidos. `"1.234"` se lee como decimal (punto
  decimal JSON); `"1,234"` como miles (coma seguida de tres dígitos).
- **Claves:** alias, mayúsculas, espacios y camelCase se reparan (`tienda` → `tienda_id`). Si la
  misma clave llega dos veces (repetida en el JSON, leída con `object_pairs_hook`, o canónica y
  alias) con el mismo valor normalizado → reparar; con valores distintos → **rechazar** si es
  `tienda_id`/`fecha` (no se puede saber a qué tienda o día se refiere) y **advertir** si es de un
  ítem (predicción: último valor; observación: no se guarda).
- **Identidad irreparable → 422:** tienda ausente, ilegible o inexistente; fecha ausente,
  ilegible, imposible, anterior al inicio del histórico o a la apertura de la tienda.
- **Ítem inválido → descartar y advertir;** sin ítems válidos → 422.
- **Fuera de dominio:** en predicción se usa un valor por defecto documentado (`en_promocion = 0`)
  y se advierte; en observaciones **no se guarda** esa familia y se advierte (una venta inventada
  contaminaría el reentrenamiento, una predicción con supuesto no).
- **Duplicados:** idénticos → reparar; en conflicto → advertir con regla determinista (predicción:
  la última aparición, como hace JSON con claves repetidas; observación: no se guarda).
- **Fechas sin reloj del sistema:** el rango válido se deriva de los datos: inicio = primera fecha
  de `historico_ventas.csv`; fin de cobertura = última de `feriados.csv` (después se advierte que
  se asume día sin feriado). Las fechas pasadas dentro del histórico **no** se rechazan (el
  servicio opera con fechas simuladas y el ejemplo del Anexo es de 2025).
- **Claves desconocidas:** se ignoran sin observación (el contrato admite campos adicionales); se
  devuelven a la API para registrarlas en la BD.

### 1.3 Decisiones sobre casos discutibles
- **E05 (faltan familias en la predicción):** se predicen **todas** las familias del catálogo; las
  faltantes con `en_promocion = 0`, y se advierte. *Alternativas:* predecir solo las recibidas;
  rechazar. *Justificación:* la tienda necesita preparar inventario de todas sus familias; 0
  promociones es el valor más frecuente del histórico y el supuesto queda informado.
- **E06 (familia repetida con valores distintos):** se usa la **última aparición** y se advierte.
  *Alternativas:* rechazar; usar la primera; sumar. *Justificación:* determinista, coincide con la
  semántica de JSON ante claves repetidas (gana la última) y no detiene la operación de la
  tienda. En observaciones, en cambio, la familia en conflicto **no se guarda**.
- **Fecha ambigua `05/06/2026`:** se interpreta **día/mes** (convención de Guatemala) y se
  advierte. Si un número es > 12 la lectura es única y se repara (`17/11/2025`, `11/17/2025`). Año
  de dos dígitos → 422 (siglo y orden ambiguos a la vez).
- **Fecha posterior a la cobertura de feriados:** advertir, no rechazar (el periodo de evaluación
  está cubierto; fuera de él el servicio sigue respondiendo con un supuesto explícito).
- **Familia aproximada (`lacteo`, `mascota`):** si una sola familia tiene similitud ≥ 0.8 se usa y
  se advierte; si no hay o hay varias candidatas, se descarta el ítem y se advierte.
- **E08 / E10 (`"50,5"`):** ventas negativas o con decimales no se guardan: no se puede saber la
  venta real (una devolución no es una venta negativa del día).
- **E09 (reenvío idéntico):** no es un problema de limpieza: la BD hace upsert idempotente y la
  respuesta es 200 con `registros_guardados = 0` y estado `ok`.
- **Rechazo:** 422 con `detail` de texto legible (`"tienda_id: la tienda 99 no existe en el
  catálogo"`). En `calidad` la acción solo puede ser `reparar` o `advertir`; el estado se deriva
  (algún `advertir` → `advertencia`; solo `reparar` → `corregido`; nada → `ok` con
  `"observaciones": []`). El `campo` usa el índice original y la clave tal como llegó
  (`familias[6].familia_id`, `tienda`).

### 1.4 Tabla de políticas (generada desde el código; un test verifica que coincide)

<!-- politicas:inicio -->
| Código | Problema | Acción | Efecto | Ejemplo |
|---|---|---|---|---|
| `CUERPO_VACIO` | Cuerpo vacío o solo espacios | rechazar | 422 |  |
| `CUERPO_GRANDE` | Cuerpo mayor al límite (1 MB) | rechazar | 422 |  |
| `JSON_INVALIDO` | JSON mal formado, ilegible o demasiado anidado | rechazar | 422 |  |
| `RAIZ_NO_OBJETO` | La raíz del JSON no es un objeto (lista, texto, número) | rechazar | 422 |  |
| `CODIFICACION` | Texto en codificación distinta de UTF-8 (Latin-1, UTF-16) | reparar | se decodifica y se continúa | bytes Latin-1 |
| `JSON_ANIDADO` | Objeto JSON enviado como texto JSON (doble codificación) | reparar | se decodifica una vez más |  |
| `FORMA_TRANSPORTE` | BOM UTF-8 o `Content-Type` distinto de JSON | ignorar | se lee el cuerpo como JSON, sin observación |  |
| `CLAVE_RENOMBRADA` | Clave con alias, mayúsculas, espacios o camelCase | reparar | se usa la clave canónica | E07 `tienda` |
| `CLAVE_DUPLICADA_IGUAL` | Clave repetida (o canónica y alias) con el mismo valor | reparar | se conserva un valor |  |
| `CLAVE_DUPLICADA_IDENTIDAD` | `tienda_id` o `fecha` repetida (o canónica y alias) con valores distintos | rechazar | 422 |  |
| `CLAVE_DUPLICADA_ITEM` | Clave de un ítem repetida con valores distintos | advertir | predicción: se usa el último valor; observación: no se guarda la familia |  |
| `CLAVE_DESCONOCIDA` | Clave que no pertenece al contrato | ignorar | se ignora sin observación (se registra en BD) |  |
| `TIENDA_FORMATO` | Id de tienda como texto, con prefijo, decimal entero o nombre | reparar | se usa el id numérico | E01 `"T05"`, `"5"`, `5.0` |
| `TIENDA_FALTANTE` | Falta `tienda_id` o es nulo | rechazar | 422 |  |
| `TIENDA_ILEGIBLE` | Id de tienda no interpretable (booleano, lista, `5.5`, texto) | rechazar | 422 |  |
| `TIENDA_INEXISTENTE` | Tienda que no está en el catálogo | rechazar | 422 |  |
| `FECHA_FORMATO` | Fecha en otro formato inequívoco (hora, `/`, `AAAAMMDD`, D/M) | reparar | se usa AAAA-MM-DD | E04 `17/11/2025`, `…T00:00:00` |
| `FECHA_AMBIGUA` | Fecha D/M/AAAA con día y mes ≤ 12 (`05/06/2026`) | advertir | se interpreta como DD/MM/AAAA |  |
| `FECHA_FALTANTE` | Falta `fecha` o es nula | rechazar | 422 |  |
| `FECHA_ILEGIBLE` | Fecha ilegible o imposible (`2025-02-30`, año de 2 dígitos) | rechazar | 422 |  |
| `FECHA_ANTES_HISTORICO` | Fecha anterior al inicio del histórico | rechazar | 422 |  |
| `FECHA_ANTES_APERTURA` | Fecha anterior a la apertura de la tienda | rechazar | 422 |  |
| `FECHA_FUERA_COBERTURA` | Fecha posterior al último día de `feriados.csv` | advertir | se responde sin información de feriados para ese día |  |
| `LISTA_COMO_OBJETO` | Lista de familias enviada como objeto `{familia: valor}` | reparar | se convierte en lista |  |
| `LISTA_DUPLICADA` | Lista de familias repetida (o con alias) con contenido distinto | rechazar | 422 |  |
| `SIN_FAMILIAS` | Lista de familias ausente, vacía o sin familias reconocibles | rechazar | 422 |  |
| `ITEM_INVALIDO` | Ítem de la lista que no es un objeto | advertir | se descarta el ítem |  |
| `FAMILIA_FORMATO` | Familia escrita de otra forma (mayúsculas, acentos, nombre) | reparar | se usa el id del catálogo | E02 `LÁCTEOS`, `Frutas y Verduras` |
| `FAMILIA_APROXIMADA` | Familia con coincidencia aproximada única (`lacteo`) | advertir | se usa la familia más cercana |  |
| `FAMILIA_DESCONOCIDA` | Familia desconocida, nula o ausente en el ítem | advertir | se descarta el ítem |  |
| `FAMILIA_REPETIDA_IGUAL` | Familia repetida con los mismos valores | reparar | se conserva una |  |
| `FAMILIA_REPETIDA_PRED` | Predicción: familia repetida con valores distintos | advertir | se usa la última aparición | E06 |
| `FAMILIA_REPETIDA_OBS` | Observación: familia repetida con valores distintos | advertir | no se guarda esa familia |  |
| `FAMILIAS_FALTANTES_PRED` | Predicción: faltan familias del catálogo | advertir | se predicen todas las del catálogo; las faltantes con `en_promocion = 0` | E05 |
| `FAMILIAS_FALTANTES_OBS` | Observación: faltan familias del catálogo | advertir | se guardan las recibidas |  |
| `NUMERO_FORMATO` | Entero como texto, decimal entero, separador de miles o coma decimal `,0` | reparar | se usa el entero | E03 `"3"`, `2.0`; E10 `"1,292"`, `"50,0"` |
| `PROMOCION_FALTANTE` | Falta `en_promocion` o es nulo | advertir | se usa 0 |  |
| `PROMOCION_DECIMAL` | `en_promocion` con decimales (`2.5`) | advertir | se redondea al entero más cercano (mitades hacia arriba) |  |
| `PROMOCION_INVALIDA` | `en_promocion` negativo, booleano, no numérico, NaN/Infinity o enorme | advertir | se usa 0 |  |
| `VENTAS_NEGATIVAS` | Ventas negativas (devoluciones) | advertir | no se guarda esa familia | E08 `-4` |
| `VENTAS_DECIMAL` | Ventas con decimales (`"50,5"`) | advertir | no se guarda esa familia |  |
| `VENTAS_INVALIDAS` | Ventas ausentes, nulas, booleanas, no numéricas, NaN/Infinity o enormes | advertir | no se guarda esa familia |  |
| `OBSERVACION_REPETIDA` | Reenvío idéntico de una observación | ignorar | upsert idempotente: `registros_guardados = 0` | E09 |
<!-- politicas:fin -->

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
