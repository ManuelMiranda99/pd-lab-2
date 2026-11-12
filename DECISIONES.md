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

Cifras de `scripts/eda.py` (resumen en `evidencia/eda/resumen_eda.json`, gráficas en
`evidencia/eda/`). Un "factor" es la venta del día dividida entre la mediana del mismo día de la
semana en las ±4 semanas vecinas, sumando las tiendas 1–9.

### 2.1 Regla temporal: solo observaciones con `fecha < D`, filtradas en SQL
- **Decisión:** al predecir la fecha `D` el panel se arma con `histórico ∪ observaciones` filtrado
  con `WHERE fecha < :D` en la consulta. Los lags se calculan **a la fecha del último dato
  disponible** (el último valor anterior a `D`, el del mismo día de la semana más reciente, etc.)
  y se agrega `dias_desde_ultimo_dato`. Al entrenar se enmascaran lags al azar para que el
  modelo aprenda a predecir con huecos.
- **Alternativas:** lags fijos (`D−1`, `D−7`) que quedan vacíos si falta un día; un modelo sin
  lags, solo con calendario; filtrar en pandas después de leer todo.
- **Justificación:** el enunciado dice que al predecir `D` solo se conocen días anteriores, y el
  periodo evaluado puede tener huecos y cambios de demanda. Los lags fijos dejan sin señal al
  modelo justo cuando falta información, y un modelo solo de calendario no ve los cambios de
  nivel. Si el filtro está en SQL, la fuga no depende de que el código de Python esté bien. Una
  prueba lo verifica: cambiar la venta de `D` no cambia la predicción de `D`.

### 2.2 Variables
- **Decisión:** un solo conjunto de variables (`src/novamercado/features/`, la misma función para
  entrenar y para servir):

| Grupo | Variables | Evidencia del EDA |
|---|---|---|
| Nivel reciente | último dato, media del mismo día de la semana (4 semanas), medias móviles de 7/28/91 días, razón 7/28, `dias_desde_ultimo_dato` | Tendencia de +6.7 % interanual (+5.6 % express, +7.1 % hipermercados). El nivel tiene que salir de datos recientes |
| Promoción | `en_promocion` del día (viene en la solicitud), promoción media de 28 días | Efecto por producto en log-ventas de +2.3 % (abarrotes) a +6.5 % (licores). Es monótono: residuo de −0.06 sin promoción, +0.04 con 1–2, +0.08 con 3–5, +0.11 con 6–9 y con 10+. Rango 0–19; 43.5 % de las filas tienen promoción |
| Calendario | día de la semana, día del mes, días a fin de mes, indicadores de día 1, 15, 16 y último día, mes | Días de pago que no están en `feriados.csv`: día 15 ×1.18, 16 ×1.14, último día ×1.19, día 1 ×1.13 (mediana del resto 0.99; el penúltimo día no sube, 0.98). Mes: diciembre ×1.19, enero ×0.91 |
| Feriados nacionales | indicadores por `tipo` (`feriado`, `comercial`, `pago`, `medio_dia`) | Bono 14 ×1.23, aguinaldo ×1.27, Viernes Negro ×1.13, Todos los Santos ×1.13; Viernes Santo ×0.90 |
| Feriado local | `feriado_local` con cruce **exacto** `ambito == ciudad` (Mixco ≠ Guatemala) | Asunción (15-ago, ciudad de Guatemala, tiendas 1, 4 y 10): ×0.91 frente a ×1.19 del resto de tiendas ese día, que es día de pago. Santiago (Antigua) ×0.92 frente a 0.95; Santo Domingo (Cobán) sin efecto |
| Eventos especiales | 24-dic, 25-dic, 31-dic, 1-ene, 26–30 dic, Semana Santa | 24-dic ×1.96, 31-dic ×2.32 (licores ×4.3 y ×5.4, carnes ×3.1 y ×3.9), 25-dic ×0.45 y 1-ene ×0.38 (abren), 26–30 dic ×1.27, Semana Santa ×0.89–1.07 |
| Tienda y familia | `tienda_id`, `familia_id`, `tipo` de tienda (categóricas), `dias_desde_apertura` (con tope) | Tienda 10: abre el 2025-03-15 con 379/día la primera semana, ~868/día en julio y ~885 en las últimas 4 semanas, al nivel de las otras express (~856) |

- **Datos que se excluyen:** el cierre de la tienda 7 (2024-09-09 → 2024-09-15, 7 días con todo en 0)
  queda fuera del objetivo y de los lags (como dato faltante, no como venta 0). Los ceros reales
  se conservan: son 55 filas en todo el histórico, la mayor proporción es 3.4 % (mascotas en la
  tienda 4) y se concentran en bebé y mascotas de las express 4, 9 y 10.
- **Lo que no se usa:** el reloj del sistema; la venta del propio día `D`; un año o tendencia
  absoluta (los árboles no extrapolan: el nivel llega por los lags); un indicador del 31 de
  octubre (su ×1.25 se explica porque es fin de mes, ya cubierto por "último día").
- **Alternativas:** solo lags (sin calendario); modelos separados por tienda y familia (120
  series); variables de Fourier en lugar de indicadores de eventos.
- **Justificación:** cada grupo tiene un efecto medible de más del 5 % en el histórico. Los eventos
  de diciembre son el mayor riesgo de costo (31-dic ×2.32 y licores ×5.4), y con solo dos ocurrencias
  de cada uno un indicador explícito los aprende mejor que una forma suave. Un modelo **global**
  comparte lo aprendido entre series cortas (la tienda 10 tiene 351 días) y es el único práctico
  de entrenar en < 3 min.

### 2.3 Fechas fuera de la cobertura de `feriados.csv`
- **Decisión:** el calendario se arma solo con `feriados.csv` (cubre hasta 2026-12-31; 19 filas son
  posteriores al histórico). Si `D` cae fuera de esa cobertura, las variables de feriado quedan en 0
  y la respuesta lleva una advertencia (la política está en §1). Los eventos fijos (24/25/31-dic,
  1-ene) y los días de pago se calculan con la fecha, así que no dependen del CSV.
- **Alternativas:** calcular los feriados con una librería externa; rechazar la fecha.
- **Justificación:** el enunciado garantiza que el periodo evaluado está cubierto por el CSV, y una
  librería externa podría contradecirlo (por ejemplo, en los feriados locales).

## 3. Cálculo de la cantidad sugerida

Cifras de `scripts/backtest_cantidad.py` (`evidencia/eda/backtest_cantidad.json`). La validación
cubre ene–feb 2026 (59 días, 7 080 filas) con pronóstico a un paso: al predecir `D` solo se usan
ventas `< D`. El costo es el del enunciado con `costos.csv`, sumado sobre tiendas, familias y días.

| Modelo | WAPE | Sesgo | Costo sugiriendo la predicción | Costo con predicción × q_f |
|---|---|---|---|---|
| Baseline (media del mismo día de la semana, 4 semanas) | 0.294 | +8.7 % | 351 652 | 310 576 (−11.7 %) |
| HGB, pérdida Poisson | 0.179 | −1.5 % | 237 004 | **179 224 (−24.4 %)** |
| HGB, log1p + smearing | 0.178 | −1.0 % | 235 119 | 179 426 |
| HGB, log1p sin corrección | 0.178 | −3.4 % | 240 264 | 179 389 |

HGB Poisson con cuantil cuesta **−42.3 %** frente al mejor baseline (310 576).

### 3.1 Cantidad sugerida = predicción × cuantil τ del ratio real/predicho por familia
- **Decisión:** `cantidad_sugerida = ceil(venta_esperada × q_f)`, acotada a ≥ 0. `q_f` es el
  cuantil `τ_f = c_f/(c_f + c_s)` de la razón `real / predicho` de la familia `f`, medida **fuera
  de muestra**: un modelo auxiliar entrenado con datos anteriores a `corte − 56 días` predice
  esos 56 días; el modelo final se entrena con todo lo anterior al corte. `q_f` se recalibra en
  cada reentrenamiento y se guarda dentro del pyfunc, con su linaje en MLflow.
- **Valores (corte 2026-01-01):** τ va de 0.286 (panadería) a 0.952 (licores). `q_f` resultó:
  licores 1.52, bebé 1.37, cuidado personal 1.37, limpieza 1.33, mascotas 1.31, bebidas 1.27,
  abarrotes 1.26, snacks 1.17, lácteos 0.94, carnes 0.93, frutas y verduras 0.90, panadería 0.88.
  Las familias donde sobrar cuesta más que faltar quedan por debajo de la predicción.
- **Alternativas medidas:** sugerir la predicción (+32 % de costo: 237 004); un desplazamiento
  aditivo `pred + cuantil τ del residuo` (194 920, +8.8 %); ventanas de calibración de 28 y 91 días
  (179 725 y 178 655, menos de 0.6 % de diferencia). **No medidas:** regresión cuantílica directa
  (un modelo por familia o por τ) y un modelo de distribución completa.
- **Justificación:** es la solución newsvendor del costo del enunciado. La razón multiplicativa
  escala con el tamaño de la tienda (un error de ±50 unidades no pesa igual en una express que en un
  hipermercado), por eso le gana al aditivo. Con un solo modelo de media se obtienen las dos salidas
  del contrato. La cobertura empírica `P(real ≤ sugerido)` queda 2.4 puntos sobre τ, así que el
  cuantil está bien calibrado. Calibrar fuera de muestra evita que `q_f` salga demasiado estrecho
  por el sobreajuste del modelo final. Recalibrar en cada reentreno hace que, si la demanda cambia,
  el colchón de seguridad también se ajuste. Se usa `ceil` porque la demanda es entera y el cuantil
  óptimo de una variable entera es el entero superior.
- **Dónde se gana más:** licores (8 661 → 2 899), cuidado personal (8 199 → 3 646), limpieza
  (9 176 → 4 229) y abarrotes (36 982 → 23 571). Panadería y frutas y verduras ganan poco porque su
  τ es cercano a 0.3.

### 3.2 Corrección de sesgo de `venta_esperada`: pérdida Poisson
- **Decisión:** HistGradientBoosting global con `loss="poisson"` sobre las ventas en unidades. No
  lleva corrección posterior.
- **Alternativas:** entrenar en `log1p` y corregir con smearing de Duan; `log1p` sin corrección;
  error cuadrático en unidades.
- **Justificación:** sin corrección, `log1p` predice la mediana y subestima la media (sesgo de
  −3.4 %). Poisson y smearing quedan prácticamente empatados (WAPE 0.179 contra 0.178, sesgo −1.5 %
  contra −1.0 %, costo con cuantil 179 224 contra 179 426). Se elige Poisson porque estima la media
  directamente, tolera los ceros y no depende de un factor global que supone residuos homogéneos
  (en diciembre no lo son). Con cualquiera de las tres variantes el costo de la cantidad sugerida
  es casi igual (±0.1 %), porque `q_f` absorbe el sesgo. La corrección importa para que
  `venta_esperada` sea una media honesta, que es lo que el contrato promete.

## 4. Criterio de reemplazo del modelo vigente

### 4.1 Retador contra vigente en las mismas ventanas recientes (rolling origin)
- **Decisión:** el DAG evalúa los dos modelos sobre los **mismos días**: los días con observación
  posteriores al corte de entrenamiento del vigente (los últimos 21 como máximo), en ventanas de 7
  días.
  - **Vigente:** se usa tal cual, sin reentrenar. Predice cada día `D` con variables construidas
    solo con datos `< D` y con el `en_promocion` de las solicitudes (§6.3).
  - **Retador:** su receta se reentrena con datos anteriores al inicio de cada ventana (origen
    móvil) y su `q_f` se calibra igual que en §3.1. Así ninguno de los dos ve los días con los que
    se lo evalúa.
  - **Métrica:** el costo de la cantidad sugerida (métrica de negocio). WAPE y sesgo se registran
    como métricas técnicas, pero no deciden.
- **Alternativas:** una sola ventana de validación (holdout); comparar contra las predicciones que
  el vigente realmente sirvió, guardadas en la BD (se descarta porque las cubren varias versiones y
  faltan días sin solicitud); promover siempre el último modelo; comparar métricas técnicas.
- **Justificación:** el enunciado evalúa el costo. Si el retador se evaluara con días que vio al
  entrenar, ganaría siempre. Con varias ventanas, un solo día extremo (31-dic, 1-ene) no decide
  solo. En el backtest, la ventana que contiene el 1-ene cuesta 96 315 al baseline, frente a
  52 000–68 000 de las demás.

### 4.2 Regla de promoción
- **Decisión:** el retador se promueve (se mueve el alias `champion`) solo si se cumplen **todas**:
  1. hay al menos 7 días evaluables después del corte del vigente;
  2. `costo_retador ≤ costo_vigente × (1 − 0.005)`, es decir, al menos 0.5 % de mejora en el total
     de las ventanas;
  3. el retador gana en al menos la mitad de las ventanas;
  4. el retador cuesta menos que el baseline en esas mismas ventanas (protege contra un modelo
     roto).

  Si no hay vigente (arranque en frío), el primero se promueve si cumple la condición 4. Un fallo
  en cualquier tarea, o un retador que no cumple, **no toca el alias**. El `motivo` siempre lleva
  ambos costos y la regla aplicada, por ejemplo: `"Costo en validación 167 896 vs 169 132 del
  vigente (−0.7 %, gana 4/4 ventanas): promovido"` o `"Costo 169 256 vs 167 896 del vigente
  (+0.8 %): se conserva el vigente"`.
- **Evidencia (rolling origin, 4 ventanas de 14 días en ene–feb 2026):**

| Ventana | Vigente congelado (corte 2025-11-01) | Retador reentrenado | Placebo (otra configuración) | Baseline |
|---|---|---|---|---|
| 01-01 | 39 343 | 39 004 | 39 527 | 96 315 |
| 01-15 | 42 255 | 42 124 | 42 793 | 67 570 |
| 01-29 | 44 129 | 43 706 | 43 658 | 53 891 |
| 02-12 | 43 405 | 43 062 | 43 279 | 52 184 |
| **Total** | 169 132 | **167 896 (−0.73 %, gana 4/4)** | 169 256 | 269 959 |

  El "placebo" usa los mismos datos que el retador pero con otra semilla y parada temprana. Sus
  diferencias por ventana van de −0.1 % a +1.6 %, con signo mixto, y suman +0.8 %.
- **Alternativas:** cualquier mejora (> 0 %); umbral de 1–2 %; una prueba estadística por bloques
  de días.
- **Justificación:** con demanda estable, reentrenar con dos meses más de datos mejora 0.7 % y gana
  en todas las ventanas: el umbral de 0.5 % lo deja pasar. Con un cambio de demanda la mejora
  esperada es mucho mayor (los lags del vigente se ajustan al nivel, pero sus efectos aprendidos y
  su `q_f` no). Un umbral de 1–2 % dejaría congelado un modelo que va quedando desactualizado. Un
  umbral de 0 % promovería diferencias de configuración del tamaño del ruido (el placebo), que
  oscilan de signo entre ventanas, y la regla de mayoría de ventanas filtra esos casos. El costo
  de promover por error un modelo equivalente es casi nulo. Lo que el criterio debe impedir es
  promover uno peor que el vigente o que el baseline, y eso lo cubren las condiciones 2–4. El umbral
  es una constante configurable y se registra en MLflow con cada decisión.
- **Tiempo:** cada ajuste de HGB tarda ~10 s con ~75 000 filas. La evaluación pide como máximo 3
  ventanas × 1 ajuste, más la calibración y el modelo final: ~1 min, dentro de la meta de < 3 min.

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

### 5.4 Backend de MLflow en una base `mlflow` del Postgres del producto
- **Decisión:** el tracking de MLflow usa la base `mlflow` dentro del Postgres del producto (la crea el
  entrypoint si no existe); los artefactos van a un volumen servido por MLflow (`--serve-artifacts`,
  `mlflow-artifacts:/`). Imagen propia `ghcr.io/mlflow/mlflow:v3.18.0` + `psycopg2-binary==2.9.11`.
- **Alternativas:** SQLite en volumen; usar la instancia de Postgres de Airflow.
- **Justificación:** robusto con varios workers, una instancia menos, y se respeta la separación
  producto/Airflow que exige el enunciado.

### 5.5 MLflow sin ejecución de jobs de GenAI
- **Decisión:** `MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false`.
- **Alternativas:** dejar el valor por defecto.
- **Justificación:** MLflow 3.18 lanza ~8 procesos de jobs (huey) que ocupaban ~2 GB; sin ellos el
  pico del stack bajó de 3.65 GB a 1.9 GB. El servicio no usa funciones de GenAI.

### 5.6 Autenticación de Airflow: FAB auth manager con JWT
- **Decisión:** FAB auth manager; la API obtiene un token con `POST /auth/token` y llama a `/api/v2`.
- **Alternativas:** SimpleAuthManager (genera contraseñas aleatorias en cada arranque).
- **Justificación:** credenciales estables definidas en `.env`, igual que en el Lab 1.

### 5.7 Imágenes y entornos
- **Decisión:** una imagen única `novamercado/app` para la API y el `bootstrap`; Airflow con un venv
  aislado `/opt/venv-ml` (`requirements-ml.txt` + `novamercado`) usado por `ExternalPythonOperator`.
  `psycopg` pasa a `requirements-ml.txt` porque lo necesitan el bootstrap y el DAG.
- **Alternativas:** una imagen por servicio; instalar las dependencias ML en el entorno de Airflow.
- **Justificación:** menos imágenes que construir y mantener, y el entrenamiento no compite con las
  constraints de Airflow.

### 5.8 Entrada del modelo pyfunc
- **Decisión:** el pyfunc recibe un DataFrame con columna `rol` (`solicitud` o `panel`); el panel se lee
  en SQL con `fecha < D` y, si una fecha tiene observación y fila del histórico, gana la observación.
- **Alternativas:** pasar el panel por otro canal (archivo, variable global); que el modelo consulte la BD.
- **Justificación:** un pyfunc solo recibe un DataFrame; así el modelo sigue siendo puro y el filtro
  temporal queda en SQL. Cierra la duda de la Fase 2 sobre observación vs histórico.

### 5.9 Arranque y salud
- **Decisión:** la API arranca sin esperar al bootstrap y responde 503 (`/health` con
  `estado: "degradado"`) hasta que hay modelo; carga alias → caché en volumen → 503. Los chequeos de
  salud corren en un hilo cada 10 s con timeouts de 3 s. El bootstrap espera a MLflow hasta 300 s y
  usa `restart: on-failure:3`; si MLflow no responde, sale con error y nunca entrena.
- **Alternativas:** `depends_on` con la API esperando al bootstrap; consultar las dependencias en cada
  petición a `/health`.
- **Justificación:** `/health` responde rápido y el camino de predicción nunca depende de MLflow.

### 5.10 Criterio de promoción provisional (Fase 1)
- **Decisión provisional:** promover si `costo_retador ≤ costo_vigente × (1 − CRITERIO_MEJORA_MINIMA)`
  con mínimo 0, evaluando ambos en las mismas 28 fechas con predicción rodante `< D`.
- **Justificación:** sirve para demostrar el ciclo completo con el baseline. **Se reemplaza** por el
  criterio de §4 (mejora ≥ 0.5 %, mayoría de ventanas, mejor que el baseline) en la Fase 5, porque un
  empate no debe tocar `champion`.

## 6. Reglas operativas

### 6.1 `POST /admin/reentrenar` con una ejecución en curso → 202 con el id de esa ejecución
- **Decisión:** si ya hay una ejecución `en_curso`, la API responde **202 con el `id` de esa misma
  ejecución** y no dispara otra. Para que dos solicitudes simultáneas no creen dos ejecuciones, la
  verificación y el alta se hacen en una transacción con `pg_advisory_xact_lock`. Una fila
  `en_curso` que supera el límite de la ejecución (`dagrun_timeout` de 9 min más un margen) se
  verifica contra Airflow. Si ya no corre, se marca `fallido` y se dispara una nueva. El tiempo se
  mide con las marcas de la BD (`now()` de Postgres), no para validar fechas de negocio.
- **Alternativas:** 503 ("no se puede iniciar"); 202 con un id nuevo que queda en cola detrás del
  actual (`max_active_runs=1`).
- **Justificación:** el contrato reserva el 503 para "no se puede iniciar". Aquí sí hay un
  reentrenamiento en marcha que cumple lo pedido, así que el cliente que consulte el id recibe un
  resultado terminal en ≤ 10 min. Con el 503, un evaluador que dispara reentrenos periódicos podría
  contar un fallo. Con la cola, el segundo run esperaría al primero y podría pasar de los 10 min
  contados desde la solicitud. Lo que se pierde es poco: las observaciones que lleguen durante la
  ejecución entran en el siguiente reentreno.

### 6.2 MLflow, Airflow o la BD caídos → 503
- **Decisión:** antes de disparar, la API comprueba MLflow (`GET /health`, timeout de 3 s) y
  dispara el DAG en Airflow (timeout de 5 s). Si alguno no responde, si el DAG no existe o está
  pausado, o si la BD no está disponible, responde **503** con un `detail` que nombra la
  dependencia. La fila de `reentrenamientos` se crea antes de disparar. Si el disparo falla, queda
  como `fallido` con el motivo (sirve de auditoría), pero el id no se devuelve.
- **Alternativas:** 202 y dejar que el DAG falle; encolar la solicitud hasta que la dependencia
  vuelva.
- **Justificación:** sin MLflow el DAG no puede registrar ni comparar y fallaría seguro; avisarlo
  de entrada es más honesto que un 202 que termina en `fallido`. Encolar agrega un estado que el
  contrato no tiene. El camino de predicción no se toca: sigue sirviendo el modelo en memoria y
  `/health` pasa a `degradado`.

### 6.3 `en_promocion` para entrenar: la última solicitud válida del día, o NULL con indicador
- **Decisión:** las observaciones no traen promociones. Para cada (tienda, fecha, familia), el DAG
  toma el `en_promocion` de la **última solicitud de predicción válida** (no rechazada) de esa
  tienda y fecha que incluyó esa familia de forma explícita, con el valor ya limpio que se usó para
  predecir. "Última" se ordena por el id secuencial de la solicitud (`BIGSERIAL`), no por el reloj.
  Si la familia no vino en ninguna solicitud (incluidas las que se completaron con promoción 0 por
  E05) o no hubo solicitud ese día, el valor queda en **NULL** con el indicador
  `promo_desconocida = 1`. El modelo lo trata como faltante (HistGradientBoosting admite NaN de
  forma nativa).
- **Alternativas:** la primera solicitud del día; el promedio o el máximo de las solicitudes;
  imputar 0; imputar la promoción media de la serie; descartar el día del entrenamiento.
- **Justificación:** el contrato permite varias solicitudes de la misma fecha, y la última es la
  versión más reciente del plan de la tienda (corrige a las anteriores). Además es determinista.
  Imputar 0 sesgaría el efecto de promoción: el 43.5 % de las filas del histórico tiene promoción
  y cada producto suma de +2 % a +6 %. Descartar el día perdería justo el nivel reciente que hace
  falta ante cambios de demanda. Un valor supuesto por E05 no es un dato de la tienda, y usarlo
  para entrenar sería inventar datos. Al predecir, la promoción siempre viene en la solicitud, así
  que el NULL solo aparece al entrenar.
