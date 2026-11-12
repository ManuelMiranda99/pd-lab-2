# Simulación de la operación (sin fuga)

Configuración: `{"proyecto": "novamercado-sim", "levantar": true, "corte": "2025-10-31", "inicio": "2025-11-28", "fin": "2026-02-28", "choque": 1.2, "choque_desde": "2026-02-01", "reentrenar_cada": 14, "caida_mlflow": "2026-01-10", "reinicio": "2026-01-24", "prob_error": 0.2, "prob_repetida": 0.1, "semilla": 2026}`

- Días simulados: 93 · filas predichas: 11160 · duración: 459.2 s
- **Costo de las sugerencias: modelo 352,937 vs baseline 523,125 (-32.5%)**
- Problemas de contrato: 0 · fallas: 0

## Costo por mes

| Mes | Modelo | Baseline | Diferencia |
|---|---:|---:|---:|
| 2025-11 | 11,711 | 15,886 | -26.3% |
| 2025-12 | 127,742 | 184,164 | -30.6% |
| 2026-01 | 91,610 | 177,338 | -48.3% |
| 2026-02 | 121,874 | 145,736 | -16.4% |

## Reentrenos

| Después de | s | Estado | Promovido | Versión | s hasta en uso |
|---|---:|---|---|---|---:|
| 2025-12-11 | 42.4 | exitoso | False | 1 |  |
| 2025-12-25 | 51.4 | exitoso | False | 1 |  |
| 2026-01-08 | 51.4 | exitoso | True | 2 | 8.0 |
| 2026-01-22 | 42.4 | exitoso | False | 2 |  |
| 2026-02-05 | 54.6 | exitoso | False | 2 |  |
| 2026-02-19 | 48.5 | exitoso | True | 3 | 10.1 |

## Eventos

- {"tipo": "arranque", "segundos": 82.1, "modelo": {"nombre": "novamercado", "alias": "champion", "version": "1"}}
- {"tipo": "corte_verificado", "fecha_max_modelo": "2025-10-31", "corte": "2025-10-31"}
- {"tipo": "mlflow_detenido", "fecha": "2026-01-10", "segundos_hasta_degradado": 7.068904665997252}
- {"tipo": "mlflow_iniciado", "fecha": "2026-01-10", "segundos_hasta_ok": 8.085217667001416}
- {"tipo": "down_up", "fecha": "2026-01-24", "segundos": 28.9, "modelo": {"nombre": "novamercado", "alias": "champion", "version": "2"}}

## Chequeos

- E01 equivalente:ok: 28
- E02 equivalente:ok: 21
- E03 equivalente:ok: 22
- E04 equivalente:ok: 27
- E05 sin 500:ok: 31
- E06 sin 500:ok: 46
- E07 equivalente:ok: 27
- E08 guarda 11 y advierte:ok: 85
- E09 reenvío idéntico guarda 0:ok: 87
- MLflow caído → degradado < 1 min:ok: 1
- MLflow de vuelta → ok < 2 min:ok: 1
- arranque en frío → ok ≤ 10 min:ok: 1
- corrección tras E08 guarda 1:ok: 85
- cuerpo binario → 422:ok: 5
- cuerpo json_roto → 422:ok: 11
- cuerpo lista → 422:ok: 9
- cuerpo null → 422:ok: 7
- cuerpo texto_plano → 422:ok: 11
- cuerpo vacio → 422:ok: 8
- down/up conserva el modelo:ok: 1
- down/up → ok ≤ 10 min:ok: 1
- inv_camel_case equivalente:ok: 16
- inv_claves_extra equivalente:ok: 28
- inv_familia_desconocida sin 500:ok: 47
- inv_fecha_imposible → 422:ok: 38
- inv_orden equivalente:ok: 31
- inv_promocion_fuera_de_dominio sin 500:ok: 31
- inv_sin_familias sin 500:ok: 37
- inv_sin_tienda → 422:ok: 34
- inv_tienda_inexistente → 422:ok: 26
- modelo inicial sin datos posteriores al corte:ok: 1
- modelo promovido en uso ≤ 2 min:ok: 2
- observación guarda todo:ok: 835
- predicción limpia ok:ok: 930
- reentreno con MLflow caído → 503:ok: 1
- reentreno exitoso:ok: 6
- reentreno ≤ 10 min:ok: 6
- sin promoción el vigente no cambia:ok: 4
- solicitud repetida → mismas predicciones:ok: 94
- tras down/up las observaciones persisten:ok: 1

## Latencias

- health: {"n": 44, "p50_ms": 4.7, "p95_ms": 10.2}
- modelo: {"n": 27, "p50_ms": 3.2, "p95_ms": 9.9}
- prediccion: {"n": 1565, "p50_ms": 49.9, "p95_ms": 76.9}
- observaciones: {"n": 1093, "p50_ms": 9.9, "p95_ms": 11.9}
- reentrenar: {"n": 7, "p50_ms": 42.6, "p95_ms": 72.9}
- estado_reentreno: {"n": 102, "p50_ms": 20.7, "p95_ms": 30.0}
