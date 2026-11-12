"""Etapas del DAG `reentrenamiento` (DECISIONES §6.4). El DAG solo las llama, en este orden:

1. `verificar`: la ejecución existe (si se disparó a mano desde Airflow, se crea), hay datos y
   MLflow responde. Guarda la versión con alias al empezar (`version_previa`).
2. `entrenar`: lee histórico + observaciones de la BD, entrena el retador, lo evalúa contra el
   vigente y el baseline y **registra la versión sin mover el alias**. Guarda el resultado
   provisional (`registrado`, `promover`, `version_registrada`, `motivo_provisional`).
3. `promover`: mueve `champion` solo si el criterio lo decidió y el alias sigue donde estaba al
   empezar. Marca `alias_movido`.
4. `confirmar`: escribe `exitoso` con la versión que tiene el alias al terminar.
5. `cerrar` (trigger `all_done`): si la ejecución sigue `en_curso` es porque algo falló. Si el
   alias ya se había movido, la escribe `exitoso` (el modelo nuevo está en uso); si no, `fallido`
   con la etapa y el error. Nunca toca el alias.

Cada etapa anota en `reentrenamientos.detalle` su nombre al empezar y el error si falla, así
`cerrar` y la API pueden explicar qué pasó. Corren en el venv ML del contenedor de Airflow.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from novamercado.config import Config
from novamercado.db import repos
from novamercado.db.conexion import conectar

log = logging.getLogger(__name__)

LARGO_ERROR = 300


class ErrorEtapa(RuntimeError):
    """Una precondición de la etapa no se cumple (el mensaje va al `motivo`)."""


class RegistroAlias(Protocol):
    """Lo único que las etapas necesitan del Model Registry."""

    def version_con_alias(self) -> str | None: ...

    def mover_alias(self, version: str) -> None: ...


class AliasMlflow:
    """`RegistroAlias` sobre `MlflowClient` (alias `cfg.modelo_alias` de `cfg.modelo_nombre`)."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _cliente(self):
        from mlflow import MlflowClient

        return MlflowClient(tracking_uri=self.cfg.mlflow_uri, registry_uri=self.cfg.mlflow_uri)

    def version_con_alias(self) -> str | None:
        from mlflow.exceptions import MlflowException

        try:
            mv = self._cliente().get_model_version_by_alias(
                self.cfg.modelo_nombre, self.cfg.modelo_alias
            )
        except MlflowException as exc:
            if exc.error_code == "RESOURCE_DOES_NOT_EXIST":
                return None
            raise
        return str(mv.version)

    def mover_alias(self, version: str) -> None:
        self._cliente().set_registered_model_alias(
            self.cfg.modelo_nombre, self.cfg.modelo_alias, version
        )


_SIN_CONEXION = ("Max retries exceeded", "Connection refused", "NameResolutionError", "timed out")


def texto_error(exc: BaseException) -> str:
    """Error legible para el `motivo`: una línea, acotada; la caída de MLflow se resume."""
    crudo = str(exc).strip()
    if "mlflow" in crudo.lower() and any(m in crudo for m in _SIN_CONEXION):
        return "MLflow no respondió (sin conexión)"
    texto = crudo.splitlines()[0] if crudo else type(exc).__name__
    return texto if len(texto) <= LARGO_ERROR else texto[: LARGO_ERROR - 1] + "…"


@contextmanager
def _etapa(cfg: Config, id_: str, nombre: str) -> Iterator[None]:
    """Anota el inicio de la etapa y, si falla, el error (sin ocultar la excepción)."""
    with conectar(cfg.db_url) as conn:
        repos.actualizar_detalle(conn, id_, {"etapa": nombre})
    try:
        yield
    except Exception as exc:
        log.exception("Falló la etapa %s del reentrenamiento %s", nombre, id_)
        try:
            with conectar(cfg.db_url) as conn:
                repos.actualizar_detalle(conn, id_, {"error": texto_error(exc)})
        except Exception:  # noqa: BLE001 — la BD caída no debe tapar el error original
            log.exception("No se pudo anotar el error de la etapa %s", nombre)
        raise


def _leer(cfg: Config, id_: str) -> repos.Reentrenamiento:
    with conectar(cfg.db_url) as conn:
        fila = repos.obtener_reentrenamiento(conn, id_)
    if fila is None:
        raise ErrorEtapa(f"No existe el reentrenamiento {id_}")
    return fila


def _activa(cfg: Config, id_: str) -> repos.Reentrenamiento:
    fila = _leer(cfg, id_)
    if fila.estado != "en_curso":
        raise ErrorEtapa(f"El reentrenamiento {id_} ya terminó ({fila.estado})")
    return fila


# --- etapas --------------------------------------------------------------------------------


def verificar(
    id_: str, cfg: Config | None = None, *, registro: RegistroAlias | None = None
) -> dict[str, Any]:
    cfg = cfg or Config.desde_entorno()
    registro = registro or AliasMlflow(cfg)
    with conectar(cfg.db_url) as conn:
        # Disparo manual desde Airflow (sin pasar por la API): se crea la fila con el run_id.
        conn.execute(
            "INSERT INTO reentrenamientos (id, estado) VALUES (%s, 'en_curso')"
            " ON CONFLICT (id) DO NOTHING",
            (id_,),
        )
        conn.commit()
    with _etapa(cfg, id_, "verificar"):
        _activa(cfg, id_)
        with conectar(cfg.db_url) as conn:
            filas, observaciones = conn.execute(
                "SELECT (SELECT count(*) FROM historico_ventas),"
                " (SELECT count(*) FROM observaciones)"
            ).fetchone()
        if filas + observaciones == 0:
            raise ErrorEtapa("No hay datos para entrenar (¿falta la carga inicial?)")
        try:
            previa = registro.version_con_alias()
        except Exception as exc:  # noqa: BLE001
            raise ErrorEtapa(f"MLflow no disponible: {texto_error(exc)}") from exc
        datos = {"version_previa": previa, "filas_historico": filas, "observaciones": observaciones}
        with conectar(cfg.db_url) as conn:
            repos.actualizar_detalle(conn, id_, datos)
    return datos


def entrenar(
    id_: str,
    cfg: Config | None = None,
    *,
    entrenador: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Entrena, evalúa y registra la versión **sin mover el alias**; guarda el provisional."""
    cfg = cfg or Config.desde_entorno()
    if entrenador is None:
        from novamercado.modelo.ciclo import entrenar_y_registrar as entrenador
    with _etapa(cfg, id_, "entrenar"):
        _activa(cfg, id_)
        res = entrenador(cfg, id_reentreno=id_, comparar=True, mover_alias=False)
        provisional = {
            "registrado": True,
            "promover": bool(res.promovido),
            "version_registrada": res.version_registrada,
            "version_previa": res.version_previa,
            "motivo_provisional": res.motivo,
            "resultado": res.detalle,
        }
        with conectar(cfg.db_url) as conn:
            repos.actualizar_detalle(conn, id_, provisional)
    return {k: provisional[k] for k in ("promover", "version_registrada", "version_previa")}


def promover(
    id_: str, cfg: Config | None = None, *, registro: RegistroAlias | None = None
) -> dict[str, Any]:
    """Mueve el alias solo si el criterio lo decidió y nadie lo movió durante el reentreno."""
    cfg = cfg or Config.desde_entorno()
    registro = registro or AliasMlflow(cfg)
    with _etapa(cfg, id_, "promover"):
        d = _activa(cfg, id_).detalle
        if not d.get("registrado"):
            raise ErrorEtapa("No hay un resultado provisional que promover")
        if not d.get("promover"):
            return {"alias_movido": False}
        actual = registro.version_con_alias()
        if actual != d.get("version_previa"):
            motivo = (
                f"{d.get('motivo_provisional', '')}; no se promueve: el alias cambió de "
                f"v{d.get('version_previa')} a v{actual} durante el reentreno"
            )
            with conectar(cfg.db_url) as conn:
                repos.actualizar_detalle(
                    conn, id_, {"promover": False, "motivo_provisional": motivo}
                )
            return {"alias_movido": False}
        registro.mover_alias(str(d["version_registrada"]))
        with conectar(cfg.db_url) as conn:
            repos.actualizar_detalle(conn, id_, {"alias_movido": True})
    return {"alias_movido": True}


def _version_al_terminar(registro: RegistroAlias, d: dict[str, Any]) -> str | None:
    """Versión con alias ahora; si MLflow no responde, la que debería tener según el detalle."""
    try:
        return registro.version_con_alias()
    except Exception:  # noqa: BLE001
        log.warning("No se pudo leer el alias al terminar; se usa el detalle de la ejecución")
        return d.get("version_registrada") if d.get("alias_movido") else d.get("version_previa")


def confirmar(
    id_: str, cfg: Config | None = None, *, registro: RegistroAlias | None = None
) -> dict[str, Any]:
    """Escribe `exitoso` con `version` = alias vigente al terminar (aunque no se promueva)."""
    cfg = cfg or Config.desde_entorno()
    registro = registro or AliasMlflow(cfg)
    with _etapa(cfg, id_, "confirmar"):
        d = _activa(cfg, id_).detalle
        if not d.get("registrado"):
            raise ErrorEtapa("No hay un resultado provisional que confirmar")
        version = _version_al_terminar(registro, d)
        promovido = bool(d.get("alias_movido"))
        motivo = str(d.get("motivo_provisional") or "")
        with conectar(cfg.db_url) as conn:
            repos.terminar_reentrenamiento(
                conn,
                id_,
                estado="exitoso",
                promovido=promovido,
                version=version,
                motivo=motivo,
                detalle={"etapa": "terminado"},
            )
    return {"promovido": promovido, "version": version, "motivo": motivo}


def cerrar(
    id_: str, cfg: Config | None = None, *, registro: RegistroAlias | None = None
) -> dict[str, Any]:
    """Última tarea (`all_done`): deja la ejecución en un estado terminal coherente con el alias."""
    cfg = cfg or Config.desde_entorno()
    registro = registro or AliasMlflow(cfg)
    with conectar(cfg.db_url) as conn:
        fila = repos.obtener_reentrenamiento(conn, id_)
    if fila is None or fila.estado in repos.ESTADOS_TERMINALES:
        return {"estado": fila.estado if fila else None, "cambios": False}
    d = fila.detalle
    try:
        version = registro.version_con_alias()
        leida = True
    except Exception:  # noqa: BLE001 — MLflow caído: se decide con el detalle
        version, leida = None, False
    movido = bool(d.get("alias_movido")) or (
        leida
        and bool(d.get("promover"))
        and d.get("version_registrada") is not None
        and version == str(d.get("version_registrada"))
    )
    if movido:
        estado, promovido = "exitoso", True
        version = version if leida else d.get("version_registrada")
        motivo = f"{d.get('motivo_provisional', '')} (el alias ya se había movido)".strip()
    else:
        estado, promovido = "fallido", False
        version = version if leida else d.get("version_previa")
        error = d.get("error") or "la tarea no terminó"
        motivo = f"Falló la etapa '{d.get('etapa', 'desconocida')}': {error}. " + (
            "El modelo vigente no cambió"
        )
    with conectar(cfg.db_url) as conn:
        escribio = repos.terminar_reentrenamiento(
            conn,
            id_,
            estado=estado,
            promovido=promovido,
            version=None if version is None else str(version),
            motivo=motivo,
        )
    log.info("Reentrenamiento %s cerrado como %s: %s", id_, estado, motivo)
    return {"estado": estado, "cambios": escribio}
