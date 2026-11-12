"""Bootstrap idempotente del producto (servicio one-shot de Compose).

Bajo un `pg_advisory_lock`:
1. migra el esquema;
2. carga los CSV publicados en una transacción con marcador (si no estaban); con
   `HISTORICO_HASTA` (solo simulador/smoke) recorta el histórico a esa fecha;
3. consulta el alias en MLflow:
   - existe → no hace nada más;
   - hay versiones pero ninguna con alias → asigna el alias a la última (no reentrena);
   - no hay versiones → entrena y registra la v1 con el alias.
Si MLflow no responde tras la espera, sale con error **sin entrenar**.

Uso: `python -m novamercado.bootstrap`
"""

from __future__ import annotations

import logging
import sys
import time

from novamercado.config import Config
from novamercado.db.carga import ArchivosFaltantes, cargar_datos
from novamercado.db.conexion import bloqueo_consultivo, conectar, esperar_bd
from novamercado.db.esquema import migrar
from novamercado.servicio.registro import mlflow_saludable

log = logging.getLogger("novamercado.bootstrap")


def esperar_mlflow(uri: str, segundos: int) -> bool:
    limite = time.monotonic() + segundos
    while True:
        if mlflow_saludable(uri):
            return True
        if time.monotonic() >= limite:
            return False
        time.sleep(3)


def asegurar_modelo(cfg: Config) -> str:
    """Garantiza que exista una versión con alias. Devuelve qué hizo."""
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException

    cliente = MlflowClient(tracking_uri=cfg.mlflow_uri, registry_uri=cfg.mlflow_uri)
    try:
        mv = cliente.get_model_version_by_alias(cfg.modelo_nombre, cfg.modelo_alias)
        return f"ya existe {cfg.modelo_nombre}@{cfg.modelo_alias} = v{mv.version}"
    except MlflowException as exc:
        if exc.error_code != "RESOURCE_DOES_NOT_EXIST":
            raise
    versiones = cliente.search_model_versions(f"name = '{cfg.modelo_nombre}'")
    # Solo una versión que alguna vez fue promovida: la última registrada puede ser un retador
    # que perdió (registros anteriores a que solo se registraran los promovidos).
    promovidas = [v for v in versiones if (v.tags or {}).get("promovido") == "True"]
    if promovidas:
        ultima = max(promovidas, key=lambda v: int(v.version))
        cliente.set_registered_model_alias(cfg.modelo_nombre, cfg.modelo_alias, ultima.version)
        return (
            f"alias {cfg.modelo_alias} asignado a la v{ultima.version} promovida antes "
            "(sin reentrenar)"
        )

    from novamercado.modelo.ciclo import entrenar_y_registrar

    res = entrenar_y_registrar(cfg, id_reentreno=None, comparar=False)
    return f"primer modelo entrenado y registrado: v{res.version} ({res.motivo})"


def main(cfg: Config | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = cfg or Config.desde_entorno()
    t0 = time.monotonic()
    if cfg.historico_hasta is not None:
        log.warning(
            "MODO SIMULACIÓN: se carga el histórico solo hasta %s (HISTORICO_HASTA). "
            "No usar en la evaluación.",
            cfg.historico_hasta,
        )
    esperar_bd(cfg.db_url)
    with bloqueo_consultivo(cfg.db_url):
        with conectar(cfg.db_url) as conn:
            aplicadas = migrar(conn)
            log.info("Migraciones aplicadas: %s", aplicadas or "ninguna (al día)")
            try:
                carga = cargar_datos(conn, cfg.datos_dir, hasta=cfg.historico_hasta)
            except (ArchivosFaltantes, ValueError) as exc:
                log.error("%s", exc)
                return 2
            log.info("Datos: %s (%s)", "cargados" if carga.cargado else "ya estaban", carga.filas)
        if not esperar_mlflow(cfg.mlflow_uri, cfg.espera_mlflow_segundos):
            log.error("MLflow (%s) no responde: no se entrena. Saliendo con error.", cfg.mlflow_uri)
            return 3
        log.info("Modelo: %s", asegurar_modelo(cfg))
    log.info("Bootstrap terminado en %.1f s", time.monotonic() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
