# Evidencia de funcionamiento

Generada con `scripts/evidencia.py` sobre un stack levantado desde cero con los datos publicados (proyecto de Compose aparte). Las solicitudes y observaciones son construidas por el script; cada respuesta se validó contra los modelos Pydantic del contrato del Anexo.

## 1. Arranque en frío

- {"segundos_hasta_ok": 65, "modelo": {"nombre": "novamercado", "alias": "champion", "version": "1"}, "health": {"estado": "ok", "componentes": {"base_de_datos": true, "mlflow": true, "airflow": true, "modelo": true}, "version_modelo": "1"}}

## 2. Respuestas de la API (válidas y defectuosas)

`nc` = problema **no catalogado** (se resuelve con las reglas generales de DECISIONES §1.2). Detalle completo de cada solicitud y respuesta en [`api/respuestas.json`](api/respuestas.json).

| Endpoint | Código | Caso | Esperado | Obtenido | Contrato | Qué informó |
|---|---|---|---|---|---|---|
| prediccion | — | solicitud válida | ok | ok ✅ | cumple | — |
| prediccion | E01 | tienda_id 'T05' | corregido | corregido ✅ | cumple | `tienda_id` reparar: 'T05' se interpretó como la tienda 5 |
| prediccion | E01 | tienda_id '5' | corregido | corregido ✅ | cumple | `tienda_id` reparar: '5' se interpretó como la tienda 5 |
| prediccion | E02 | 'LÁCTEOS' y 'Frutas y Verduras' | corregido | corregido ✅ | cumple | `familias[5].familia_id` reparar: 'Frutas y Verduras' se interpretó como 'frutas_verduras'; `familias[6].familia_id` reparar: 'LÁCTEOS' se interpretó como 'lacteos' |
| prediccion | E02 | 'lacteos ' con espacio | corregido | corregido ✅ | cumple | `familias[6].familia_id` reparar: 'lacteos ' se interpretó como 'lacteos' |
| prediccion | E03 | promoción '8' y 9.0 | corregido | corregido ✅ | cumple | `familias[2].en_promocion` reparar: '8' se interpretó como 8; `familias[11].en_promocion` reparar: '9.0' se interpretó como 9 |
| prediccion | E03 | promoción 2.5 (con pérdida) | advertencia | advertencia ✅ | cumple | `familias[2].en_promocion` advertir: '2.5' no es entero; se redondeó a 3 |
| prediccion | E04 | fecha '2026-03-01T00:00:00' | corregido | corregido ✅ | cumple | `fecha` reparar: '2026-03-01T00:00:00' se interpretó como 2026-03-01 |
| prediccion | E04 | fecha '15/03/2026' | corregido | corregido ✅ | cumple | `fecha` reparar: '15/03/2026' se interpretó como 2026-03-15 |
| prediccion | E04 | fecha ambigua '05/03/2026' (DD/MM) | advertencia | advertencia ✅ | cumple | `fecha` advertir: '05/03/2026' es ambigua; se interpretó como 2026-03-05 (día/mes/año) |
| prediccion | E05 | faltan 'bebe' y 'mascotas' | advertencia | advertencia ✅ | cumple | `familias` advertir: faltan 2 familias (bebe, mascotas); se predijeron con en_promocion=0 |
| prediccion | E06 | 'abarrotes' repetida con promoción distinta | advertencia | advertencia ✅ | cumple | `familias[12]` advertir: 'abarrotes' aparece 2 veces con en_promocion distinto (0, 3); se usó la última aparición (3) |
| prediccion | E07 | 'tienda' en vez de 'tienda_id' | corregido | corregido ✅ | cumple | `tienda` reparar: 'tienda' se interpretó como 'tienda_id' |
| prediccion | nc | claves en CamelCase | corregido | corregido ✅ | cumple | `TiendaId` reparar: 'TiendaId' se interpretó como 'tienda_id'; `Fecha` reparar: 'Fecha' se interpretó como 'fecha'; `Familias` reparar: 'Familias' se interpretó como 'familias' |
| prediccion | nc | fecha entera 20260301 | corregido | corregido ✅ | cumple | `fecha` reparar: '20260301' se interpretó como 2026-03-01 |
| prediccion | nc | claves desconocidas (se ignoran) | ok | ok ✅ | cumple | — |
| prediccion | nc | familia desconocida 'juguetes' | advertencia | advertencia ✅ | cumple | `familias[12].familia_id` advertir: 'juguetes' no es una familia del catálogo; se descartó |
| prediccion | nc | promoción negativa -3 | advertencia | advertencia ✅ | cumple | `familias[2].en_promocion` advertir: '-3' no es válido (negativo); se asumió 0 |
| prediccion | nc | promoción 'muchas' | advertencia | advertencia ✅ | cumple | `familias[2].en_promocion` advertir: 'muchas' no es válido (texto no numérico); se asumió 0 |
| prediccion | nc | promoción null | advertencia | advertencia ✅ | cumple | `familias[2].en_promocion` advertir: no se recibió en_promocion; se asumió 0 |
| prediccion | nc | fecha fuera de la cobertura de feriados | advertencia | advertencia ✅ | cumple | `fecha` advertir: la fecha 2027-02-15 es posterior a la cobertura de feriados (2026-12-31); se asume día sin feriado |
| prediccion | nc | tienda inexistente 99 | 422 | 422 ✅ | cumple | tienda_id: la tienda 99 no existe en el catálogo |
| prediccion | nc | fecha imposible '2026-02-30' | 422 | 422 ✅ | cumple | fecha: no se pudo interpretar '2026-02-30' como fecha |
| prediccion | nc | fecha ilegible 'mañana' | 422 | 422 ✅ | cumple | fecha: no se pudo interpretar 'mañana' como fecha |
| prediccion | nc | tienda 10 antes de su apertura | 422 | 422 ✅ | cumple | fecha: la tienda 10 abrió el 2025-03-15; no opera el 2025-01-10 |
| prediccion | nc | lista de familias vacía | 422 | 422 ✅ | cumple | familias: no hay familias válidas en 'familias' |
| prediccion | nc | JSON roto | 422 | 422 ✅ | cumple | el cuerpo no es un JSON válido: Expecting value (línea 1, columna 26) |
| prediccion | nc | cuerpo que no es objeto | 422 | 422 ✅ | cumple | se esperaba un objeto JSON en la raíz y se recibió una lista |
| observaciones | — | observación válida (12 familias nuevas) | ok | ok ✅ | cumple | registros_guardados=12 |
| observaciones | E09 | reenvío idéntico (registros_guardados = 0) | ok | ok ✅ | cumple | registros_guardados=0 |
| observaciones | E01 | tienda 'T05' corrigiendo un valor (1 modificado) | corregido | corregido ✅ | cumple | registros_guardados=1; `tienda_id` reparar: 'T05' se interpretó como la tienda 5 |
| observaciones | E08 | ventas negativas (no se guarda esa familia) | advertencia | advertencia ✅ | cumple | registros_guardados=0; `ventas[1].ventas` advertir: '-4' es negativo; no se guardó |
| observaciones | E10 | '1,234' y '812,0' | corregido | corregido ✅ | cumple | registros_guardados=0; `ventas[2].ventas` reparar: '1,795' se interpretó como 1795; `ventas[3].ventas` reparar: '468,0' se interpretó como 468 |
| observaciones | E10 | '50,5' (decimal con pérdida: no se guarda) | advertencia | advertencia ✅ | cumple | registros_guardados=0; `ventas[2].ventas` reparar: '1,795' se interpretó como 1795; `ventas[3].ventas` advertir: '50,5' no es un entero; no se guardó |
| observaciones | nc | familia desconocida en observaciones | advertencia | advertencia ✅ | cumple | registros_guardados=12; `ventas[12].familia_id` advertir: 'juguetes' no es una familia del catálogo; se descartó |
| observaciones | nc | observación de tienda inexistente | 422 | 422 ✅ | cumple | tienda_id: la tienda 42 no existe en el catálogo |

### Ejemplo completo: solicitud válida

Solicitud:

```json
{
  "tienda_id": 5,
  "fecha": "2026-03-01",
  "familias": [
    {
      "familia_id": "abarrotes",
      "en_promocion": 0
    },
    {
      "familia_id": "bebe",
      "en_promocion": 0
    },
    {
      "familia_id": "bebidas",
      "en_promocion": 8
    },
    {
      "familia_id": "carnes",
      "en_promocion": 0
    },
    {
      "familia_id": "cuidado_personal",
      "en_promocion": 0
    },
    {
      "familia_id": "frutas_verduras",
      "en_promocion": 0
    },
    {
      "familia_id": "lacteos",
      "en_promocion": 3
    },
    {
      "familia_id": "licores",
      "en_promocion": 0
    },
    {
      "familia_id": "limpieza",
      "en_promocion": 0
    },
    {
      "familia_id": "mascotas",
      "en_promocion": 0
    },
    {
      "familia_id": "panaderia",
      "en_promocion": 0
    },
    {
      "familia_id": "snacks",
      "en_promocion": 9
    }
  ]
}
```

Respuesta:

```json
{
  "tienda_id": 5,
  "fecha": "2026-03-01",
  "version_modelo": "1",
  "predicciones": [
    {
      "familia_id": "abarrotes",
      "venta_esperada": 1351.27,
      "cantidad_sugerida": 1636
    },
    {
      "familia_id": "bebe",
      "venta_esperada": 99.79,
      "cantidad_sugerida": 132
    },
    {
      "familia_id": "bebidas",
      "venta_esperada": 1435.61,
      "cantidad_sugerida": 1796
    },
    {
      "familia_id": "carnes",
      "venta_esperada": 374.42,
      "cantidad_sugerida": 349
    },
    {
      "familia_id": "cuidado_personal",
      "venta_esperada": 252.61,
      "cantidad_sugerida": 340
    },
    {
      "familia_id": "frutas_verduras",
      "venta_esperada": 492.43,
      "cantidad_sugerida": 449
    },
    {
      "familia_id": "lacteos",
      "venta_esperada": 726.05,
      "cantidad_sugerida": 712
    },
    {
      "familia_id": "licores",
      "venta_esperada": 112.55,
      "cantidad_sugerida": 163
    },
    {
      "familia_id": "limpieza",
      "venta_esperada": 277.23,
      "cantidad_sugerida": 366
    },
    {
      "familia_id": "mascotas",
      "venta_esperada": 68.61,
      "cantidad_sugerida": 90
    },
    {
      "familia_id": "panaderia",
      "venta_esperada": 407.98,
      "cantidad_sugerida": 351
    },
    {
      "familia_id": "snacks",
      "venta_esperada": 450.27,
      "cantidad_sugerida": 539
    }
  ],
  "calidad": {
    "estado": "ok",
    "observaciones": []
  }
}
```

### Ejemplo completo: E02 reparado

Solicitud:

```json
{
  "tienda_id": 5,
  "fecha": "2026-03-01",
  "familias": [
    {
      "familia_id": "abarrotes",
      "en_promocion": 0
    },
    {
      "familia_id": "bebe",
      "en_promocion": 0
    },
    {
      "familia_id": "bebidas",
      "en_promocion": 8
    },
    {
      "familia_id": "carnes",
      "en_promocion": 0
    },
    {
      "familia_id": "cuidado_personal",
      "en_promocion": 0
    },
    {
      "familia_id": "Frutas y Verduras",
      "en_promocion": 0
    },
    {
      "familia_id": "LÁCTEOS",
      "en_promocion": 3
    },
    {
      "familia_id": "licores",
      "en_promocion": 0
    },
    {
      "familia_id": "limpieza",
      "en_promocion": 0
    },
    {
      "familia_id": "mascotas",
      "en_promocion": 0
    },
    {
      "familia_id": "panaderia",
      "en_promocion": 0
    },
    {
      "familia_id": "snacks",
      "en_promocion": 9
    }
  ]
}
```

Respuesta:

```json
{
  "tienda_id": 5,
  "fecha": "2026-03-01",
  "version_modelo": "1",
  "predicciones": [
    {
      "familia_id": "abarrotes",
      "venta_esperada": 1351.27,
      "cantidad_sugerida": 1636
    },
    {
      "familia_id": "bebe",
      "venta_esperada": 99.79,
      "cantidad_sugerida": 132
    },
    {
      "familia_id": "bebidas",
      "venta_esperada": 1435.61,
      "cantidad_sugerida": 1796
    },
    {
      "familia_id": "carnes",
      "venta_esperada": 374.42,
      "cantidad_sugerida": 349
    },
    {
      "familia_id": "cuidado_personal",
      "venta_esperada": 252.61,
      "cantidad_sugerida": 340
    },
    {
      "familia_id": "frutas_verduras",
      "venta_esperada": 492.43,
      "cantidad_sugerida": 449
    },
    {
      "familia_id": "lacteos",
      "venta_esperada": 726.05,
      "cantidad_sugerida": 712
    },
    {
      "familia_id": "licores",
      "venta_esperada": 112.55,
      "cantidad_sugerida": 163
    },
    {
      "familia_id": "limpieza",
      "venta_esperada": 277.23,
      "cantidad_sugerida": 366
    },
    {
      "familia_id": "mascotas",
      "venta_esperada": 68.61,
      "cantidad_sugerida": 90
    },
    {
      "familia_id": "panaderia",
      "venta_esperada": 407.98,
      "cantidad_sugerida": 351
    },
    {
      "familia_id": "snacks",
      "venta_esperada": 450.27,
      "cantidad_sugerida": 539
    }
  ],
  "calidad": {
    "estado": "corregido",
    "observaciones": [
      {
        "campo": "familias[5].familia_id",
        "accion": "reparar",
        "detalle": "'Frutas y Verduras' se interpretó como 'frutas_verduras'"
      },
      {
        "campo": "familias[6].familia_id",
        "accion": "reparar",
        "detalle": "'LÁCTEOS' se interpretó como 'lacteos'"
      }
    ]
  }
}
```

## 3. Reentrenamiento (endpoint de administración + DAG)

### Sin datos nuevos → no se promueve

- `POST /admin/reentrenar` → 202 {"id": "7387a3fc05404976b06fd05aee37bb2c"}
- Modelo antes: {"nombre": "novamercado", "alias": "champion", "version": "1"}
- `GET /admin/reentrenar/{id}` al terminar (24.3 s): {"estado": "exitoso", "promovido": false, "version": "1", "motivo": "Solo 0 días evaluables después del corte del vigente (mínimo 7): se conserva el vigente"}
- Modelo después: {"nombre": "novamercado", "alias": "champion", "version": "1"}

### 14 días con demanda +25 % → se promueve

- `POST /admin/reentrenar` → 202 {"id": "518816a0ce144b1ea91fa2c278ee6a81"}
- Modelo antes: {"nombre": "novamercado", "alias": "champion", "version": "1"}
- `GET /admin/reentrenar/{id}` al terminar (39.4 s): {"estado": "exitoso", "promovido": true, "version": "3", "motivo": "Costo en validación 46 296 vs 47 167 del vigente (−1.8 %, gana 1/2 ventanas): promovido"}
- Modelo después: {"nombre": "novamercado", "alias": "champion", "version": "3"}
- Versión nueva en uso por la API (sin reinicio) a los 12.2 s de terminar

## 4. Caída temporal de MLflow

| Paso | HTTP | Respuesta | Segundos |
|---|---|---|---|
| GET /health tras stop mlflow | 200 | `{"estado": "degradado", "motivo": "No disponible: mlflow", "componentes": {"base_de_datos": true, "mlflow": false, "airflow": true, "modelo"` | 3.1 |
| POST /v1/prediccion con MLflow caído | 200 | `{"tienda_id": 5, "fecha": "2026-03-01", "version_modelo": "3", "predicciones": [{"familia_id": "abarrotes", "venta_esperada": 1658.54, "cant` |  |
| GET /modelo con MLflow caído | 200 | `{"nombre": "novamercado", "alias": "champion", "version": "3"}` |  |
| POST /admin/reentrenar con MLflow caído | 503 | `{"detail": "MLflow no disponible: no se puede reentrenar"}` |  |
| GET /health tras start mlflow | 200 | `{"estado": "ok", "componentes": {"base_de_datos": true, "mlflow": true, "airflow": true, "modelo": true}, "version_modelo": "3"}` | 9.1 |

## 5. MLflow: experimentos, runs y versiones

Modelo registrado `novamercado`, alias: [{"alias": "champion", "version": "3"}]. Detalle en [`mlflow/mlflow.json`](mlflow/mlflow.json).

| Versión | Alias | Promovido | Run |
|---|---|---|---|
| 1 | — | True | `eae122132fbf472280783e57ce92bfc1` |
| 2 | — | False | `2bc0f7f899834cf2a4ce707a121c3975` |
| 3 | champion | True | `501c4c30da674727a9e8b064d8c7d66d` |

| Run | Rol | Costo validación | Costo baseline | Costo vigente | WAPE | Observaciones | Decisión |
|---|---|---|---|---|---|---|---|
| baseline-bootstrap | referencia | 80,167 | — | — | 0.215 | 0 | — |
| hgb_poisson-bootstrap | retador | 64,841 | 80,167 | — | 0.175 | 0 | Sin modelo vigente; costo en validación 64 841 vs 80 167 del baseline (−19.1 %): promovido |
| baseline-7387a3fc05404976b06fd05aee37bb2c | referencia | — | — | — | — | 0 | — |
| hgb_poisson-7387a3fc05404976b06fd05aee37bb2c | retador | — | — | — | — | 0 | Solo 0 días evaluables después del corte del vigente (mínimo 7): se conserva el vigente |
| baseline-518816a0ce144b1ea91fa2c278ee6a81 | referencia | 66,072 | — | — | 0.205 | 1680 | — |
| hgb_poisson-518816a0ce144b1ea91fa2c278ee6a81 | retador | 46,296 | 66,072 | 47,167 | 0.187 | 1680 | Costo en validación 46 296 vs 47 167 del vigente (−1.8 %, gana 1/2 ventanas): promovido |

## 6. Airflow: ejecuciones del DAG `reentrenamiento`

Detalle en [`airflow/dag_runs.json`](airflow/dag_runs.json).

| Ejecución | Estado | Tareas |
|---|---|---|
| `7387a3fc05404976b06fd05aee37bb2c` | success | verificar (success, 3 s) → entrenar (success, 17 s) → promover (success, 0 s) → confirmar (success, 1 s) → fin (success, 0 s) → cerrar (success, 0 s) |
| `518816a0ce144b1ea91fa2c278ee6a81` | success | verificar (success, 1 s) → entrenar (success, 32 s) → promover (success, 1 s) → confirmar (success, 1 s) → fin (success, 0 s) → cerrar (success, 0 s) |

## Resumen

- 36 casos de API, 0 con diferencias; problemas de contrato en toda la corrida: 0.
- Códigos: {"estado_reentreno 200": 23, "health 200": 18, "health 503": 16, "modelo 200": 19, "observaciones 200": 147, "observaciones 422": 1, "prediccion 200": 162, "prediccion 422": 7, "reentrenar 202": 2, "reentrenar 503": 1}
