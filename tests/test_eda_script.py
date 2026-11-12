"""Humo de `scripts/eda.py` con datos sintéticos (nunca con los datos publicados)."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

FAMILIAS = [
    "abarrotes",
    "bebidas",
    "lacteos",
    "carnes",
    "panaderia",
    "frutas_verduras",
    "limpieza",
    "cuidado_personal",
    "licores",
    "snacks",
    "bebe",
    "mascotas",
]


def _escribir_datos(carpeta: Path) -> None:
    rng = np.random.default_rng(0)
    fechas = pd.date_range("2025-01-01", "2025-06-30", freq="D")
    tiendas = pd.DataFrame(
        {
            "tienda_id": range(1, 11),
            "nombre": [f"T{i}" for i in range(1, 11)],
            "ciudad": ["Guatemala", "Mixco"] * 5,
            "departamento": "Guatemala",
            "tipo": ["hipermercado", "supermercado", "supermercado", "express"] * 2
            + ["express", "express"],
            "fecha_apertura": ["2019-01-01"] * 9 + ["2025-03-15"],
        }
    )
    filas = []
    for t in tiendas.itertuples():
        for f in fechas[fechas >= pd.Timestamp(t.fecha_apertura)]:
            for fam in FAMILIAS:
                promo = int(rng.integers(0, 5))
                filas.append((f.date(), t.tienda_id, fam, int(rng.poisson(50 + 5 * promo)), promo))
    hist = pd.DataFrame(
        filas, columns=["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"]
    )
    texto = hist["fecha"].astype(str)
    cierre = (hist["tienda_id"] == 7) & (texto >= "2025-02-01") & (texto < "2025-02-08")
    hist.loc[cierre, "ventas"] = 0
    hist.to_csv(carpeta / "historico_ventas.csv", index=False)
    tiendas.to_csv(carpeta / "tiendas.csv", index=False)
    pd.DataFrame({"familia_id": FAMILIAS, "nombre": FAMILIAS}).to_csv(
        carpeta / "familias.csv", index=False
    )
    pd.DataFrame(
        {
            "fecha": ["2025-04-17", "2025-04-18", "2025-05-01", "2025-06-10", "2026-12-31"],
            "nombre": ["Jueves Santo", "Viernes Santo", "Día del Trabajo", "Local", "Fin"],
            "tipo": ["feriado", "feriado", "feriado", "feriado", "medio_dia"],
            "ambito": ["nacional", "nacional", "nacional", "Mixco", "nacional"],
        }
    ).to_csv(carpeta / "feriados.csv", index=False)
    pd.DataFrame({"familia_id": FAMILIAS, "costo_faltante": 1.0, "costo_sobrante": 0.5}).to_csv(
        carpeta / "costos.csv", index=False
    )


def test_eda_corre_con_datos_sinteticos(tmp_path):
    pytest.importorskip("matplotlib")
    import eda

    datos = tmp_path / "datos"
    datos.mkdir()
    _escribir_datos(datos)
    salida = tmp_path / "salida"
    res = eda.main(["--datos", str(datos), "--salida", str(salida)])

    e = res["estructura"]
    assert e["duplicados"] == 0 and e["huecos"] == 0
    assert e["familias_en_todas_las_tiendas"]
    assert res["cierres"][7]["dias"] == 7
    assert res["tienda_10"]["primer_dato"] == "2025-03-15"
    assert len(list(salida.glob("*.png"))) == 9
    assert json.loads((salida / "resumen_eda.json").read_text())["estructura"]["dias"] == 181


def test_eda_falla_claro_si_faltan_archivos(tmp_path):
    import comun_datos

    with pytest.raises(FileNotFoundError, match="historico_ventas"):
        comun_datos.cargar(tmp_path)
