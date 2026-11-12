"""Modelo en memoria y hot-reload sin reinicio.

- `ModeloCargado` es inmutable: cada predicción toma una referencia (`gestor.actual`) y la usa de
  principio a fin, aunque el poller la reemplace a mitad de camino.
- El poller (un hilo) consulta el alias cada `poll_segundos`. Si cambió: descarga a la caché,
  valida con una predicción de humo y recién entonces hace el cambio.
- Arranque: alias en MLflow → caché en volumen → sin modelo (la API responde 503).
- El camino de predicción **nunca** llama a MLflow.
"""

from __future__ import annotations

import logging
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novamercado.servicio.cache import CacheModelos, EntradaCache
from novamercado.servicio.registro import Registro, RegistroNoDisponible

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModeloCargado:
    modelo: Any  # PyFuncModel (o cualquier objeto con .predict(DataFrame))
    nombre: str
    alias: str
    version: str
    origen: str  # "mlflow" | "cache"


def cargar_y_validar(ruta: Path) -> Any:
    """Carga un pyfunc desde disco y lo valida con una predicción de humo."""
    import mlflow.pyfunc

    from novamercado.modelo.pyfunc import entrada_de_humo, validar_salida

    modelo = mlflow.pyfunc.load_model(str(ruta))
    entrada = entrada_de_humo()
    validar_salida(modelo.predict(entrada), int((entrada["rol"] == "solicitud").sum()))
    return modelo


class GestorModelo:
    def __init__(
        self,
        registro: Registro,
        cache: CacheModelos,
        *,
        nombre: str,
        alias: str,
        poll_segundos: int = 20,
        cargador=cargar_y_validar,
    ) -> None:
        self.registro = registro
        self.cache = cache
        self.nombre = nombre
        self.alias = alias
        self.poll_segundos = poll_segundos
        self._cargar = cargador
        self._actual: ModeloCargado | None = None
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None
        self._bloqueo = threading.Lock()

    @property
    def actual(self) -> ModeloCargado | None:
        return self._actual

    # --- arranque y poller -------------------------------------------------------------------

    def arrancar(self) -> None:
        """Primer intento de carga (alias → caché) y luego el hilo del poller."""
        try:
            self.cache.preparar()
        except OSError:
            log.exception("No se pudo preparar la caché %s", self.cache.dir)
        if not self.sincronizar():
            self.cargar_desde_cache()
        self._hilo = threading.Thread(target=self._bucle, name="poller-modelo", daemon=True)
        self._hilo.start()

    def detener(self) -> None:
        self._detener.set()
        if self._hilo is not None:
            self._hilo.join(timeout=5)

    def _espera(self) -> float:
        """Sin modelo se consulta más seguido: el bootstrap puede registrarlo pronto."""
        return self.poll_segundos if self._actual is not None else min(5, self.poll_segundos)

    def _bucle(self) -> None:
        while not self._detener.wait(self._espera()):
            try:
                if not self.sincronizar() and self._actual is None:
                    self.cargar_desde_cache()
            except Exception:  # noqa: BLE001 — el poller nunca debe morir
                log.exception("Error inesperado en el poller del modelo")

    # --- operaciones -------------------------------------------------------------------------

    def cargar_desde_cache(self) -> bool:
        entrada = self.cache.leer_actual()
        if entrada is None or entrada.nombre != self.nombre:
            return False
        try:
            modelo = self._cargar(entrada.ruta)
        except Exception:  # noqa: BLE001
            log.exception("La caché %s no es válida", entrada.ruta)
            return False
        with self._bloqueo:
            if self._actual is None:
                self._actual = ModeloCargado(
                    modelo, entrada.nombre, entrada.alias, entrada.version, "cache"
                )
                log.info("Modelo %s v%s cargado desde la caché", entrada.nombre, entrada.version)
        return True

    def sincronizar(self) -> bool:
        """Consulta el alias y cambia de modelo si hace falta.

        Devuelve True si MLflow respondió (haya o no cambio) y False si no estuvo disponible o
        si la versión nueva no pudo instalarse.
        """
        try:
            version = self.registro.version_con_alias(self.nombre, self.alias)
        except RegistroNoDisponible as exc:
            log.warning("MLflow no disponible al consultar el alias: %s", exc)
            return False
        if version is None:
            return True  # aún no hay modelo con alias (el bootstrap lo creará)
        actual = self._actual
        if actual is not None and actual.version == version:
            return True
        return self._instalar(version)

    def _instalar(self, version: str) -> bool:
        tmp = self.cache.carpeta_temporal()
        final: Path | None = None
        try:
            descargado = self.registro.descargar(self.nombre, version, tmp)
            final = self.cache.instalar(descargado, self.nombre, version)
            modelo = self._cargar(final)
        except Exception:  # noqa: BLE001 — descarga o humo fallidos: se conserva el vigente
            log.exception("No se pudo instalar la versión %s", version)
            if final is not None and (self._actual is None or self._actual.version != version):
                self.cache.descartar(final)
            return False
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.cache.marcar_actual(EntradaCache(self.nombre, self.alias, version, final))
        with self._bloqueo:
            anterior = self._actual
            self._actual = ModeloCargado(modelo, self.nombre, self.alias, version, "mlflow")
        log.info(
            "Modelo en uso: %s v%s (antes: %s)",
            self.nombre,
            version,
            anterior.version if anterior else "ninguno",
        )
        return True
