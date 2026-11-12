"""Caché del modelo en un volumen, con escritura atómica.

Estructura:
    <dir>/versiones/<nombre>-v<version>/   modelo pyfunc descargado y validado
    <dir>/actual.json                       puntero al vigente (se escribe con os.replace)
    <dir>/tmp-*                             descargas en curso (se limpian al arrancar)

`actual.json` solo apunta a carpetas que ya pasaron la predicción de humo, así que tras un corte
a mitad de descarga el servicio arranca con la última versión buena.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

VERSIONES_A_CONSERVAR = 3


@dataclass(frozen=True)
class EntradaCache:
    nombre: str
    alias: str
    version: str
    ruta: Path


class CacheModelos:
    def __init__(self, directorio: Path) -> None:
        self.dir = Path(directorio)
        self.dir_versiones = self.dir / "versiones"
        self.puntero = self.dir / "actual.json"

    def preparar(self) -> None:
        self.dir_versiones.mkdir(parents=True, exist_ok=True)
        for tmp in self.dir.glob("tmp-*"):
            shutil.rmtree(tmp, ignore_errors=True)

    def carpeta_temporal(self) -> Path:
        self.preparar()
        return Path(tempfile.mkdtemp(prefix="tmp-", dir=self.dir))

    def ruta_version(self, nombre: str, version: str) -> Path:
        return self.dir_versiones / f"{nombre}-v{version}"

    def instalar(self, origen: Path, nombre: str, version: str) -> Path:
        """Mueve una descarga (en el mismo volumen) a su carpeta final con `os.replace`."""
        destino = self.ruta_version(nombre, version)
        if destino.exists():
            shutil.rmtree(destino)
        os.replace(origen, destino)
        return destino

    def descartar(self, ruta: Path) -> None:
        shutil.rmtree(ruta, ignore_errors=True)

    def marcar_actual(self, entrada: EntradaCache) -> None:
        """Escribe `actual.json` de forma atómica y poda versiones antiguas."""
        datos = {
            "nombre": entrada.nombre,
            "alias": entrada.alias,
            "version": entrada.version,
            "ruta": entrada.ruta.name,
        }
        fd, tmp = tempfile.mkstemp(prefix="actual-", suffix=".json", dir=self.dir)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.puntero)
        self._podar(entrada.ruta)

    def leer_actual(self) -> EntradaCache | None:
        try:
            datos = json.loads(self.puntero.read_text(encoding="utf-8"))
            ruta = self.dir_versiones / datos["ruta"]
        except (OSError, ValueError, KeyError):
            return None
        if not ruta.is_dir():
            return None
        return EntradaCache(datos["nombre"], datos["alias"], str(datos["version"]), ruta)

    def _podar(self, vigente: Path) -> None:
        carpetas = sorted(
            (p for p in self.dir_versiones.iterdir() if p.is_dir() and p != vigente),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for vieja in carpetas[VERSIONES_A_CONSERVAR - 1 :]:
            shutil.rmtree(vieja, ignore_errors=True)
