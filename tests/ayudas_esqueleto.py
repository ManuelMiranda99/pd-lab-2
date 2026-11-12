"""Ayudas compartidas por las pruebas del esqueleto (datos sintéticos propios y BD de prueba).

No usa los datos publicados. Las pruebas que necesitan Postgres usan el fixture `bd` y se
saltan si no hay `TEST_DATABASE_URL`.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

FAMILIAS = ["abarrotes", "lacteos", "panaderia"]
COSTOS = {"abarrotes": (1.0, 0.2), "lacteos": (1.0, 1.2), "panaderia": (1.0, 2.5)}


def panel_sintetico(
    dias: int = 70, tiendas: tuple[int, ...] = (1, 2), inicio: str = "2025-01-06", semilla: int = 7
) -> pd.DataFrame:
    """Ventas diarias con estacionalidad semanal y ruido, sin huecos."""
    rng = np.random.default_rng(semilla)
    fechas = pd.date_range(inicio, periods=dias, freq="D")
    filas = []
    for t in tiendas:
        for i, fam in enumerate(FAMILIAS):
            base = 100 * (i + 1) * t
            for f in fechas:
                nivel = base * (1.3 if f.dayofweek >= 5 else 1.0)
                filas.append((t, f, fam, float(max(0, round(nivel + rng.normal(0, base * 0.1))))))
    return pd.DataFrame(filas, columns=["tienda_id", "fecha", "familia_id", "ventas"])


def escribir_csvs(carpeta: Path, *, bom: bool = False, dias: int = 21) -> None:
    """Escribe un juego mínimo de CSV publicados (sintéticos) en `carpeta`."""
    carpeta.mkdir(parents=True, exist_ok=True)
    enc = "utf-8-sig" if bom else "utf-8"
    pd.DataFrame(
        [
            (1, "Tienda Uno", "Guatemala", "Guatemala", "hipermercado", "2019-01-01"),
            (2, "Tienda Dos", "Mixco", "Guatemala", "express", "2019-01-01"),
        ],
        columns=["tienda_id", "nombre", "ciudad", "departamento", "tipo", "fecha_apertura"],
    ).to_csv(carpeta / "tiendas.csv", index=False, encoding=enc)
    pd.DataFrame([(f, f.capitalize()) for f in FAMILIAS], columns=["familia_id", "nombre"]).to_csv(
        carpeta / "familias.csv", index=False, encoding=enc
    )
    pd.DataFrame(
        [(f, *COSTOS[f]) for f in FAMILIAS],
        columns=["familia_id", "costo_faltante", "costo_sobrante"],
    ).to_csv(carpeta / "costos.csv", index=False, encoding=enc)
    pd.DataFrame(
        [("2025-01-01", "Año Nuevo", "feriado", "nacional")],
        columns=["fecha", "nombre", "tipo", "ambito"],
    ).to_csv(carpeta / "feriados.csv", index=False, encoding=enc)
    panel = panel_sintetico(dias=dias)
    panel["fecha"] = panel["fecha"].dt.strftime("%Y-%m-%d")
    panel["ventas"] = panel["ventas"].astype(int)
    panel["en_promocion"] = 0
    panel[["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"]].to_csv(
        carpeta / "historico_ventas.csv", index=False, encoding=enc
    )


@pytest.fixture(name="bd_url")
def bd_url() -> str:
    """URL de una BD de prueba **vacía** (se borra el esquema public)."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Sin TEST_DATABASE_URL: prueba de integración con Postgres omitida")
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    return url


@pytest.fixture(name="bd")
def bd(bd_url: str):
    """Conexión a la BD de prueba con el esquema migrado."""
    import psycopg

    from novamercado.db.esquema import migrar

    with psycopg.connect(bd_url) as conn:
        migrar(conn)
        yield conn
