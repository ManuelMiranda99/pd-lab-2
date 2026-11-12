# Simulación de la operación (sin fuga)

Configuración: `{"proyecto": "novamercado-sim3", "levantar": true, "corte": "2026-01-14", "inicio": "2026-01-15", "fin": "2026-02-28", "choque": 0.8, "choque_desde": "2026-02-01", "reentrenar_cada": 7, "caida_mlflow": null, "reinicio": null, "prob_error": 0.2, "prob_repetida": 0.1, "semilla": 7}`

- Días simulados: 45 · filas predichas: 5400 · duración: 609.0 s
- **Costo de las sugerencias: modelo 137,098 vs baseline 177,677 (-22.8%)**
- Problemas de contrato: 0 · fallas: 0

## Costo por mes

| Mes | Modelo | Baseline | Diferencia |
|---|---:|---:|---:|
| 2026-01 | 52,304 | 81,801 | -36.1% |
| 2026-02 | 84,795 | 95,876 | -11.6% |

## Reentrenos

| Después de | s | Estado | Promovido | Versión | s hasta en uso |
|---|---:|---|---|---|---:|
| 2026-01-21 | 78.7 | exitoso | False | 1 |  |
| 2026-01-28 | 99.8 | exitoso | False | 1 |  |
| 2026-02-04 | 112.0 | exitoso | False | 1 |  |
| 2026-02-11 | 115.0 | exitoso | True | 5 | 8.1 |
| 2026-02-18 | 33.3 | exitoso | False | 5 |  |
| 2026-02-25 | 45.4 | exitoso | True | 7 | 8.1 |

## Eventos

- {"tipo": "arranque", "segundos": 135.6, "modelo": {"nombre": "novamercado", "alias": "champion", "version": "1"}}
- {"tipo": "corte_verificado", "fecha_max_modelo": "2026-01-14", "corte": "2026-01-14"}

## Chequeos

- E01 equivalente:ok: 10
- E02 equivalente:ok: 16
- E03 equivalente:ok: 12
- E04 equivalente:ok: 14
- E05 sin 500:ok: 19
- E06 sin 500:ok: 20
- E07 equivalente:ok: 19
- E08 guarda 11 y advierte:ok: 47
- E09 reenvío idéntico guarda 0:ok: 45
- arranque en frío → ok ≤ 10 min:ok: 1
- corrección tras E08 guarda 1:ok: 47
- cuerpo binario → 422:ok: 4
- cuerpo json_roto → 422:ok: 1
- cuerpo lista → 422:ok: 4
- cuerpo null → 422:ok: 2
- cuerpo texto_plano → 422:ok: 2
- cuerpo vacio → 422:ok: 4
- inv_camel_case equivalente:ok: 14
- inv_claves_extra equivalente:ok: 14
- inv_familia_desconocida sin 500:ok: 27
- inv_fecha_imposible → 422:ok: 19
- inv_orden equivalente:ok: 12
- inv_promocion_fuera_de_dominio sin 500:ok: 19
- inv_sin_familias sin 500:ok: 13
- inv_sin_tienda → 422:ok: 10
- inv_tienda_inexistente → 422:ok: 18
- modelo inicial sin datos posteriores al corte:ok: 1
- modelo promovido en uso ≤ 2 min:ok: 2
- observación guarda todo:ok: 393
- predicción limpia ok:ok: 450
- reentreno exitoso:ok: 6
- reentreno ≤ 10 min:ok: 6
- sin promoción el vigente no cambia:ok: 4
- solicitud repetida → mismas predicciones:ok: 46

## Latencias

- health: {"n": 40, "p50_ms": 3.7, "p95_ms": 6.8}
- modelo: {"n": 23, "p50_ms": 3.6, "p95_ms": 8.5}
- prediccion: {"n": 769, "p50_ms": 136.3, "p95_ms": 150.7}
- observaciones: {"n": 532, "p50_ms": 10.8, "p95_ms": 13.1}
- reentrenar: {"n": 6, "p50_ms": 31.3, "p95_ms": 48.4}
- estado_reentreno: {"n": 166, "p50_ms": 19.1, "p95_ms": 25.6}
