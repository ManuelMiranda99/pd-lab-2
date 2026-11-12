"""Carga inicial de los datos publicados (CSV) en la BD del producto.

La carga es idempotente: ocurre en **una** transacción que termina escribiendo el marcador
`carga_datos` (sha256 de los archivos + filas). Si el marcador existe, no se vuelve a cargar.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import psycopg

log = logging.getLogger(__name__)

ARCHIVOS_REQUERIDOS = (
    "tiendas.csv",
    "familias.csv",
    "costos.csv",
    "feriados.csv",
    "historico_ventas.csv",
)


class ArchivosFaltantes(FileNotFoundError):
    """Faltan archivos publicados en la carpeta de datos."""


@dataclass(frozen=True)
class ResultadoCarga:
    cargado: bool  # True si esta llamada cargó; False si ya estaba cargado
    sha256: str
    filas: dict[str, int]


def localizar_archivos(raiz: Path) -> dict[str, Path]:
    """Encuentra los CSV publicados en `raiz` o en una subcarpeta (p. ej. si el zip trae una).

    El nombre se compara sin distinguir mayúsculas. Falla con un mensaje claro si falta alguno.
    """
    if not raiz.is_dir():
        raise ArchivosFaltantes(
            f"No existe la carpeta de datos {raiz}. Coloca los archivos publicados en la carpeta "
            "indicada en el README (por defecto ./data) o define DATOS_DIR en .env."
        )
    candidatas = [raiz] + sorted(p for p in raiz.iterdir() if p.is_dir())
    encontrados: dict[str, Path] = {}
    for carpeta in candidatas:
        por_nombre = {p.name.lower(): p for p in carpeta.iterdir() if p.is_file()}
        for requerido in ARCHIVOS_REQUERIDOS:
            if requerido not in encontrados and requerido in por_nombre:
                encontrados[requerido] = por_nombre[requerido]
    faltan = [a for a in ARCHIVOS_REQUERIDOS if a not in encontrados]
    if faltan:
        raise ArchivosFaltantes(
            f"Faltan archivos en {raiz} (se buscó también un nivel de subcarpetas): "
            f"{', '.join(faltan)}."
        )
    return encontrados


def huella(archivos: dict[str, Path]) -> str:
    """sha256 del contenido de los archivos requeridos, en orden fijo."""
    h = hashlib.sha256()
    for nombre in ARCHIVOS_REQUERIDOS:
        h.update(nombre.encode())
        h.update(archivos[nombre].read_bytes())
    return h.hexdigest()


def _leer(ruta: Path) -> pd.DataFrame:
    """Lee un CSV tolerando BOM y espacios en los encabezados."""
    df = pd.read_csv(ruta, encoding="utf-8-sig", dtype=str, keep_default_na=False)
    df.columns = [c.strip().lower() for c in df.columns]
    return df.apply(lambda s: s.str.strip())


def leer_publicados(archivos: dict[str, Path]) -> dict[str, pd.DataFrame]:
    """Lee y tipa los CSV publicados."""
    tiendas = _leer(archivos["tiendas.csv"])
    tiendas["tienda_id"] = tiendas["tienda_id"].astype(int)
    familias = _leer(archivos["familias.csv"])
    familias["familia_id"] = familias["familia_id"].str.lower()
    costos = _leer(archivos["costos.csv"])
    costos["familia_id"] = costos["familia_id"].str.lower()
    costos[["costo_faltante", "costo_sobrante"]] = costos[
        ["costo_faltante", "costo_sobrante"]
    ].astype(float)
    feriados = _leer(archivos["feriados.csv"])
    historico = _leer(archivos["historico_ventas.csv"])
    historico["familia_id"] = historico["familia_id"].str.lower()
    historico["tienda_id"] = historico["tienda_id"].astype(int)
    historico["ventas"] = historico["ventas"].astype(float).round().astype(int)
    historico["en_promocion"] = historico["en_promocion"].astype(float).round().astype(int)
    return {
        "tiendas": tiendas[
            ["tienda_id", "nombre", "ciudad", "departamento", "tipo", "fecha_apertura"]
        ],
        "familias": familias[["familia_id", "nombre"]],
        "costos": costos[["familia_id", "costo_faltante", "costo_sobrante"]],
        "feriados": feriados[["fecha", "nombre", "tipo", "ambito"]],
        "historico_ventas": historico[
            ["tienda_id", "fecha", "familia_id", "ventas", "en_promocion"]
        ],
    }


def _copiar(conn: psycopg.Connection, tabla: str, df: pd.DataFrame) -> None:
    columnas = ", ".join(df.columns)
    with conn.cursor() as cur, cur.copy(f"COPY {tabla} ({columnas}) FROM STDIN") as copia:
        for fila in df.itertuples(index=False, name=None):
            copia.write_row(fila)


def marcador(conn: psycopg.Connection) -> tuple[str, dict[str, int]] | None:
    fila = conn.execute("SELECT sha256, filas FROM carga_datos WHERE id = 1").fetchone()
    return (fila[0], fila[1]) if fila else None


def cargar_datos(conn: psycopg.Connection, raiz: Path) -> ResultadoCarga:
    """Carga los CSV si todavía no hay marcador. Todo o nada (una transacción)."""
    archivos = localizar_archivos(raiz)
    sha = huella(archivos)
    existente = marcador(conn)
    conn.commit()
    if existente is not None:
        sha_previo, filas_previas = existente
        if sha_previo != sha:
            log.warning(
                "Los archivos de %s difieren de los ya cargados (sha %s… vs %s…); se conserva lo "
                "cargado.",
                raiz,
                sha[:12],
                sha_previo[:12],
            )
        return ResultadoCarga(False, sha_previo, dict(filas_previas))

    tablas = leer_publicados(archivos)
    filas = {nombre: len(df) for nombre, df in tablas.items()}
    with conn.transaction():
        # Bloqueo de la fila-marcador: si otro proceso cargó mientras leíamos, no se duplica.
        conn.execute("LOCK TABLE carga_datos IN EXCLUSIVE MODE")
        if marcador(conn) is not None:
            return ResultadoCarga(False, sha, filas)
        for nombre in ("tiendas", "familias", "costos", "feriados", "historico_ventas"):
            _copiar(conn, nombre, tablas[nombre])
        conn.execute(
            "INSERT INTO carga_datos (id, sha256, filas) VALUES (1, %s, %s)",
            (sha, json.dumps(filas)),
        )
    log.info("Datos cargados: %s", filas)
    return ResultadoCarga(True, sha, filas)
