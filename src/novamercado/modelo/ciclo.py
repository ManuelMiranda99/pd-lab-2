"""Ciclo de entrenamiento: entrenar retador, compararlo con el vigente, registrar y promover.

Lo usan el bootstrap (primer modelo) y el DAG `reentrenamiento`. Nunca lo usa la API.
Orden de escritura: registrar versión → mover alias (solo si gana) → resultado en la BD.
Retador: HGB Poisson con cuantil por familia (`modelo.hgb`), evaluado con origen móvil contra el
vigente y el baseline (`modelo.validacion`) y decidido con el criterio de §4.2 (`modelo.criterio`).
Un fallo o un retador que no gana **no** tocan el alias.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

import novamercado
from novamercado.config import Config
from novamercado.db import repos
from novamercado.db.conexion import conectar
from novamercado.features.panel import leer_contexto, leer_historia
from novamercado.modelo.baseline import ajustar_baseline
from novamercado.modelo.criterio import Decision, decidir_reemplazo
from novamercado.modelo.hgb import DIAS_CALIBRACION, HIPERPARAMETROS, ModeloHGB
from novamercado.modelo.validacion import entrenar_y_evaluar

log = logging.getLogger(__name__)


@dataclass
class Resultado:
    promovido: bool
    version: str | None  # versión vigente (con alias) al terminar
    version_registrada: str | None
    motivo: str
    detalle: dict[str, Any] = field(default_factory=dict)


def _commit_git() -> str:
    """Commit del código: `NOVAMERCADO_COMMIT` (imágenes) o `git rev-parse` (desarrollo)."""
    desde_entorno = os.environ.get("NOVAMERCADO_COMMIT", "").strip()
    if desde_entorno:
        return desde_entorno
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — en las imágenes no hay git: no es un error
        return "desconocido"


@dataclass
class Vigente:
    modelo: Any
    version: str
    corte: pd.Timestamp | None  # primer día que no vio al entrenar (None si se desconoce)


def cargar_vigente(cfg: Config) -> Vigente | None:
    """Modelo interno, versión y corte del alias vigente, o None si no hay alias."""
    import mlflow.pyfunc
    from mlflow import MlflowClient
    from mlflow.exceptions import MlflowException

    cliente = MlflowClient(tracking_uri=cfg.mlflow_uri, registry_uri=cfg.mlflow_uri)
    try:
        mv = cliente.get_model_version_by_alias(cfg.modelo_nombre, cfg.modelo_alias)
    except MlflowException as exc:
        if exc.error_code == "RESOURCE_DOES_NOT_EXIST":
            return None
        raise
    mlflow.set_tracking_uri(cfg.mlflow_uri)
    pyfunc = mlflow.pyfunc.load_model(f"models:/{cfg.modelo_nombre}/{mv.version}")
    modelo = pyfunc.unwrap_python_model().modelo
    corte = getattr(modelo, "fecha_corte", None)
    return Vigente(modelo, str(mv.version), None if corte is None else pd.Timestamp(corte))


def _metricas_por_familia(prefijo: str, valores: dict[str, float]) -> dict[str, float]:
    return {f"{prefijo}_{f}": float(v) for f, v in sorted(valores.items())}


def entrenar_y_registrar(cfg: Config, *, id_reentreno: str | None, comparar: bool) -> Resultado:
    """Entrena el retador, lo evalúa contra vigente y baseline (§4) y lo registra con su linaje.

    Orden: registrar la versión → mover el alias solo si la decisión lo permite. Sin vigente y
    con un retador que no mejora al baseline, se registra y promueve el baseline (el servicio
    necesita un modelo). Cualquier excepción sale antes de tocar el alias.
    """
    import mlflow
    from mlflow import MlflowClient

    from novamercado.modelo.pyfunc import REQUISITOS_PIP, RUTA_PAQUETE, ModeloNovaMercado

    t0 = time.perf_counter()
    with conectar(cfg.db_url) as conn:
        historia = leer_historia(conn)
        ctx = leer_contexto(conn)
        costos = repos.costos_por_familia(conn)
        sha = repos.huella_datos(conn)
        n_obs = conn.execute("SELECT count(*) FROM observaciones").fetchone()[0]
        conn.commit()
    if historia.empty:
        raise RuntimeError("No hay datos para entrenar (¿falta la carga inicial?)")

    vigente = cargar_vigente(cfg) if comparar else None
    res = entrenar_y_evaluar(
        historia,
        ctx,
        costos,
        vigente=vigente.modelo if vigente else None,
        corte_vigente=vigente.corte if vigente else None,
    )
    decision = decidir_reemplazo(res.comparacion, mejora_minima=cfg.mejora_minima)
    final: Any = res.modelo
    if vigente is None and not decision.promover:
        final = ajustar_baseline(historia, costos)
        decision = Decision(
            True, f"{decision.motivo}; se promueve el baseline como respaldo del arranque"
        )
    comp = res.comparacion
    id_texto = id_reentreno or "bootstrap"
    mlflow.set_tracking_uri(cfg.mlflow_uri)
    mlflow.set_experiment(cfg.experimento)
    linaje = {
        "id_reentreno": id_texto,
        "datos_sha256": sha or "",
        "fecha_min": str(historia["fecha"].min().date()),
        "fecha_max": str(historia["fecha"].max().date()),
        "filas": len(historia),
        "observaciones": n_obs,
        "commit": _commit_git(),
        "version_paquete": novamercado.__version__,
        "ventanas": len(comp.retador),
        "dias_evaluados": comp.dias,
        "evaluacion_inicio": res.ventanas[0]["inicio"] if res.ventanas else "",
        "evaluacion_fin": res.ventanas[-1]["fin"] if res.ventanas else "",
        "mejora_minima": cfg.mejora_minima,
        "version_vigente": vigente.version if vigente else "",
    }
    # Run de referencia del baseline en las mismas ventanas (evidencia de la mejora, F5.4).
    with mlflow.start_run(run_name=f"baseline-{id_texto}"):
        mlflow.set_tag("rol", "referencia")
        mlflow.log_params({**linaje, "modelo": "baseline_dia_semana"})
        if comp.baseline:
            mlflow.log_metrics({"costo_validacion": comp.total_baseline, **res.tecnicas_baseline})
    with mlflow.start_run(run_name=f"{final.nombre}-{id_texto}") as run:
        mlflow.set_tag("rol", "retador")
        mlflow.set_tag("decision", decision.motivo)
        params = {**linaje, "modelo": final.nombre}
        if isinstance(final, ModeloHGB):
            params.update({f"hgb_{k}": v for k, v in HIPERPARAMETROS.items()})
            params["dias_calibracion"] = DIAS_CALIBRACION
            params["fecha_corte"] = str(final.fecha_corte.date()) if final.fecha_corte else ""
        mlflow.log_params(params)
        metricas: dict[str, float] = {
            **res.tecnicas,
            **_metricas_por_familia("q", final.factores),
            "duracion_entrenamiento_s": time.perf_counter() - t0,
            "promovido": float(decision.promover),
        }
        if comp.retador:
            metricas["costo_validacion"] = comp.total_retador
            metricas["costo_baseline"] = comp.total_baseline
        if comp.vigente is not None and comp.vigente:
            metricas["costo_vigente"] = float(comp.total_vigente or 0.0)
            metricas["ventanas_ganadas"] = float(comp.ventanas_ganadas)
        mlflow.log_metrics(metricas)
        for i, v in enumerate(res.ventanas):
            for rol in ("retador", "vigente", "baseline"):
                if f"costo_{rol}" in v:
                    mlflow.log_metric(f"costo_ventana_{rol}", v[f"costo_{rol}"], step=i)
        mlflow.log_dict({"ventanas": res.ventanas}, "evaluacion.json")
        info = mlflow.pyfunc.log_model(
            name="modelo",
            python_model=ModeloNovaMercado(final),
            code_paths=[str(RUTA_PAQUETE)],
            pip_requirements=REQUISITOS_PIP,
            registered_model_name=cfg.modelo_nombre,
        )
        version = str(info.registered_model_version)
        log.info("Run %s: versión registrada %s (%s)", run.info.run_id, version, decision.motivo)

    cliente = MlflowClient(tracking_uri=cfg.mlflow_uri, registry_uri=cfg.mlflow_uri)
    cliente.set_model_version_tag(cfg.modelo_nombre, version, "promovido", str(decision.promover))
    version_vigente = vigente.version if vigente else None
    if decision.promover:
        cliente.set_registered_model_alias(cfg.modelo_nombre, cfg.modelo_alias, version)
        version_vigente = version
    return Resultado(
        promovido=decision.promover,
        version=version_vigente,
        version_registrada=version,
        motivo=decision.motivo,
        detalle={
            "costo_retador": comp.total_retador if comp.retador else None,
            "costo_vigente": comp.total_vigente if comp.vigente else None,
            "costo_baseline": comp.total_baseline if comp.baseline else None,
            "ventanas": res.ventanas,
            "segundos": round(time.perf_counter() - t0, 1),
            **res.tecnicas,
        },
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
