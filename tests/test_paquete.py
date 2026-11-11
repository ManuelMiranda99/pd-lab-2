"""Pruebas mínimas del paquete y de las reglas del repo."""

import subprocess
from pathlib import Path

import novamercado

RAIZ = Path(__file__).resolve().parents[1]


def test_paquete_importa_con_version():
    assert isinstance(novamercado.__version__, str)


def test_sin_datos_ni_credenciales_versionados():
    """Ningún archivo de datos ni credencial está en el índice de git."""
    res = subprocess.run(["git", "ls-files"], cwd=RAIZ, capture_output=True, text=True, check=False)
    if res.returncode != 0:  # fuera de un repo git (p. ej. un tarball): nada que revisar
        return
    prohibidos = [
        f
        for f in res.stdout.splitlines()
        if f.startswith("data/")
        or f.endswith((".csv", ".parquet", ".pkl", ".joblib", ".pdf", ".zip"))
        or Path(f).name == ".env"
        or (Path(f).name.startswith(".env.") and Path(f).name != ".env.example")
    ]
    assert prohibidos == []
