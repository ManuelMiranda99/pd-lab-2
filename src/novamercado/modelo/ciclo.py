"""Ciclo de entrenamiento: entrenar retador, compararlo con el vigente, registrar y promover.

Lo usan el bootstrap (primer modelo) y el DAG `reentrenamiento`. Nunca lo usa la API.
Orden de escritura: registrar versión → mover alias (solo si gana) → resultado en la BD.
Un fallo o un retador que no gana **no** tocan el alias.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from novamercado.config import Config
from novamercado.db import repos
from novamercado.db.conexion import conectar
from novamercado.features.panel import leer_historia
from novamercado.modelo.baseline import ajustar_baseline, predicciones_rodantes, ultimas_fechas
from novamercado.modelo.criterio import decidir_reemplazo
from novamercado.modelo.metricas import costo_total, metricas_tecnicas

log = logging.getLogger(__name__)

DIAS_VALIDACION = 28


@dataclass
class Resultado:
    promovido: bool
    version: str | None  # versión vigente (con alias) al terminar
    version_registrada: str | None
    motivo: str
    detalle: dict[str, Any] = field(default_factory=dict)


def _commit_git() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — en las imágenes no hay git: no es un error
        return "desconocido"


def cargar_vigente(cfg: Config) -> tuple[Any | None, str | None]:
    """(modelo interno, versión) del alias vigente, o (None, None) si no hay alias."""
    import mlflow.pyfunc
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException

    cliente = MlflowClient(tracking_uri=cfg.mlflow_uri, registry_uri=cfg.mlflow_uri)
    try:
        mv = cliente.get_model_version_by_alias(cfg.modelo_nombre, cfg.modelo_alias)
    except MlflowException as exc:
        if exc.error_code == "RESOURCE_DOES_NOT_EXIST":
            return None, None
        raise
    mlflow.set_tracking_uri(cfg.mlflow_uri)
    pyfunc = mlflow.pyfunc.load_model(f"models:/{cfg.modelo_nombre}/{mv.version}")
    return pyfunc.unwrap_python_model().modelo, str(mv.version)


def entrenar_y_registrar(cfg: Config, *, id_reentreno: str | None, comparar: bool) -> Resultado:
    """Entrena el retador, evalúa contra el vigente en la misma ventana y lo registra."""
    import mlflow
    from mlflow import MlflowClient

    from novamercado.modelo.pyfunc import REQUISITOS_PIP, RUTA_PAQUETE, ModeloNovaMercado

    with conectar(cfg.db_url) as conn:
        historia = leer_historia(conn)
        costos = repos.costos_por_familia(conn)
        sha = repos.huella_datos(conn)
        n_obs = conn.execute("SELECT count(*) FROM observaciones").fetchone()[0]
        conn.commit()
    if historia.empty:
        raise RuntimeError("No hay datos para entrenar (¿falta la carga inicial?)")

    fechas_val = ultimas_fechas(historia, DIAS_VALIDACION)
    inicio_val = fechas_val[0]
    # Esqueleto: retador y vigente se evalúan en la misma ventana (las últimas 28 fechas) con
    # predicción rodante (cada día solo ve `< D`). Los factores de ambos pueden haber visto parte
    # de la ventana; la validación fuera de muestra con varias ventanas llega en la Fase 5.
    final = ajustar_baseline(historia, costos)
    pred_ret = predicciones_rodantes(final, historia, fechas_val)
    costo_ret = costo_total(pred_ret, costos)
    tecnicas = metricas_tecnicas(pred_ret)

    costo_vig: float | None = None
    version_vigente: str | None = None
    if comparar:
        vigente, version_vigente = cargar_vigente(cfg)
        if vigente is not None:
            costo_vig = costo_total(predicciones_rodantes(vigente, historia, fechas_val), costos)
    decision = decidir_reemplazo(costo_ret, costo_vig, mejora_minima=cfg.mejora_minima)

    mlflow.set_tracking_uri(cfg.mlflow_uri)
    mlflow.set_experiment(cfg.experimento)
    with mlflow.start_run(run_name=f"{final.nombre}-{id_reentreno or 'bootstrap'}") as run:
        mlflow.log_params(
            {
                "modelo": final.nombre,
                "k_semanas": final.k,
                "id_reentreno": id_reentreno or "bootstrap",
                "datos_sha256": sha or "",
                "fecha_min": str(historia["fecha"].min().date()),
                "fecha_max": str(historia["fecha"].max().date()),
                "filas": len(historia),
                "observaciones": n_obs,
                "validacion_inicio": str(pd.Timestamp(inicio_val).date()),
                "validacion_dias": len(fechas_val),
                "commit": _commit_git(),
            }
        )
        metricas = {"costo_validacion": costo_ret, **tecnicas}
        if costo_vig is not None:
            metricas["costo_vigente"] = costo_vig
        mlflow.log_metrics(metricas)
        mlflow.set_tag("decision", decision.motivo)
        info = mlflow.pyfunc.log_model(
            name="modelo",
            python_model=ModeloNovaMercado(final),
            code_paths=[str(RUTA_PAQUETE)],
            pip_requirements=REQUISITOS_PIP,
            registered_model_name=cfg.modelo_nombre,
        )
        version = str(info.registered_model_version)
        log.info("Run %s: versión registrada %s", run.info.run_id, version)

    cliente = MlflowClient(tracking_uri=cfg.mlflow_uri, registry_uri=cfg.mlflow_uri)
    cliente.set_model_version_tag(cfg.modelo_nombre, version, "promovido", str(decision.promover))
    if decision.promover:
        cliente.set_registered_model_alias(cfg.modelo_nombre, cfg.modelo_alias, version)
        version_vigente = version
    return Resultado(
        promovido=decision.promover,
        version=version_vigente,
        version_registrada=version,
        motivo=decision.motivo,
        detalle={"costo_retador": costo_ret, "costo_vigente": costo_vig, **tecnicas},
    )


def ejecutar_reentrenamiento(id_reentreno: str, cfg: Config | None = None) -> dict[str, Any]:
    """Punto de entrada del DAG: entrena, decide y escribe el resultado en `reentrenamientos`."""
    cfg = cfg or Config.desde_entorno()
    res = entrenar_y_registrar(cfg, id_reentreno=id_reentreno, comparar=True)
    with conectar(cfg.db_url) as conn:
        repos.terminar_reentrenamiento(
            conn,
            id_reentreno,
            estado="exitoso",
            promovido=res.promovido,
            version=res.version,
            motivo=res.motivo,
            detalle={**res.detalle, "version_registrada": res.version_registrada},
        )
    return {"promovido": res.promovido, "version": res.version, "motivo": res.motivo}


def marcar_fallido(id_reentreno: str, motivo: str, cfg: Config | None = None) -> None:
    """Registra el fallo con la versión vigente (si se puede consultar); no toca el alias."""
    cfg = cfg or Config.desde_entorno()
    version: str | None = None
    try:
        from mlflow import MlflowClient

        cliente = MlflowClient(tracking_uri=cfg.mlflow_uri, registry_uri=cfg.mlflow_uri)
        version = str(
            cliente.get_model_version_by_alias(cfg.modelo_nombre, cfg.modelo_alias).version
        )
    except Exception:  # noqa: BLE001 — MLflow caído: el fallo se registra igual
        log.warning("No se pudo consultar la versión vigente al registrar el fallo")
    with conectar(cfg.db_url) as conn:
        repos.terminar_reentrenamiento(
            conn, id_reentreno, estado="fallido", promovido=False, version=version, motivo=motivo
        )
