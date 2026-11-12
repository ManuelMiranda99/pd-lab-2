"""Generador de datos sintéticos para CI y smoke (sin usar los datos publicados).

Los ids de familia son los del contrato (aparecen en el Anexo); tiendas, nombres, fechas y
valores son inventados. Todo es determinista dada la semilla.
"""

from __future__ import annotations

import random
from datetime import date
from typing import Any

from novamercado.limpieza.catalogo import Catalogo, Tienda

#: Familias del contrato con un nombre legible (con acentos, para probar la normalización).
FAMILIAS_SINTETICAS: dict[str, str] = {
    "abarrotes": "Abarrotes",
    "bebidas": "Bebidas",
    "lacteos": "Lácteos",
    "carnes": "Carnes",
    "panaderia": "Panadería",
    "frutas_verduras": "Frutas y verduras",
    "limpieza": "Limpieza",
    "cuidado_personal": "Cuidado personal",
    "licores": "Licores",
    "snacks": "Snacks y dulces",
    "bebe": "Bebé",
    "mascotas": "Mascotas",
}

#: Tienda sintética que abre tarde (para probar el rechazo antes de la apertura).
TIENDA_NUEVA = 10
APERTURA_TIENDA_NUEVA = date(2025, 6, 1)
INICIO_SINTETICO = date(2024, 3, 1)
FIN_COBERTURA_SINTETICO = date(2026, 12, 31)


def catalogo_sintetico() -> Catalogo:
    """Catálogo propio para pruebas: 10 tiendas (la 10 abre tarde) y las 12 familias."""
    tiendas = {
        i: Tienda(
            tienda_id=i,
            nombre=f"Tienda Sintética {i}",
            fecha_apertura=APERTURA_TIENDA_NUEVA if i == TIENDA_NUEVA else date(2019, 1, 1),
        )
        for i in range(1, 11)
    }
    return Catalogo(
        tiendas=tiendas,
        familias=dict(FAMILIAS_SINTETICAS),
        fecha_inicio=INICIO_SINTETICO,
        fecha_fin_cobertura=FIN_COBERTURA_SINTETICO,
    )


def solicitud_prediccion(
    tienda_id: int = 3, fecha: str = "2026-03-10", semilla: int = 7
) -> dict[str, Any]:
    """Solicitud de predicción válida con promociones aleatorias."""
    rng = random.Random(semilla)
    return {
        "tienda_id": tienda_id,
        "fecha": fecha,
        "familias": [
            {"familia_id": f, "en_promocion": rng.choice([0, 0, 0, 1, 2, 4, 6])}
            for f in FAMILIAS_SINTETICAS
        ],
    }


def solicitud_observaciones(
    tienda_id: int = 3, fecha: str = "2026-03-10", semilla: int = 11
) -> dict[str, Any]:
    """Solicitud de observaciones válida con ventas aleatorias."""
    rng = random.Random(semilla)
    return {
        "tienda_id": tienda_id,
        "fecha": fecha,
        "ventas": [{"familia_id": f, "ventas": rng.randint(20, 1500)} for f in FAMILIAS_SINTETICAS],
    }


# ---------------------------------------------------------------------------------------------
# Archivos CSV sintéticos (mismo formato que los publicados) para el smoke de la CI
# ---------------------------------------------------------------------------------------------

#: Costos inventados (faltante, sobrante): no son los publicados.
COSTOS_SINTETICOS: dict[str, tuple[float, float]] = {
    "abarrotes": (4.0, 1.0),
    "bebidas": (4.0, 1.0),
    "lacteos": (2.0, 2.0),
    "carnes": (3.0, 4.0),
    "panaderia": (1.0, 2.0),
    "frutas_verduras": (1.0, 2.0),
    "limpieza": (6.0, 1.0),
    "cuidado_personal": (6.0, 1.0),
    "licores": (10.0, 1.0),
    "snacks": (3.0, 1.0),
    "bebe": (5.0, 1.0),
    "mascotas": (4.0, 1.0),
}
_NIVEL_FAMILIA = {
    "abarrotes": 900,
    "bebidas": 700,
    "lacteos": 500,
    "carnes": 350,
    "panaderia": 400,
    "frutas_verduras": 450,
    "limpieza": 250,
    "cuidado_personal": 220,
    "licores": 120,
    "snacks": 300,
    "bebe": 60,
    "mascotas": 50,
}
_TIPOS = ("hipermercado", "supermercado", "supermercado", "express")
_FACTOR_TIPO = {"hipermercado": 1.6, "supermercado": 1.0, "express": 0.45}
_FACTOR_DIA = (0.9, 0.85, 0.9, 0.95, 1.1, 1.3, 1.2)  # lunes … domingo


def _feriados_sinteticos(inicio: date, fin_cobertura: date) -> list[tuple[str, str, str, str]]:
    """Feriados inventados (fijos por año) con los tipos y ámbitos del formato publicado."""
    filas: list[tuple[str, str, str, str]] = []
    fijos = [
        (1, 1, "Año Nuevo Sintético", "feriado", "nacional"),
        (5, 1, "Día del Trabajo Sintético", "feriado", "nacional"),
        (9, 15, "Fiesta Nacional Sintética", "feriado", "nacional"),
        (12, 24, "Nochebuena Sintética", "medio_dia", "nacional"),
        (12, 25, "Navidad Sintética", "feriado", "nacional"),
        (12, 31, "Fin de Año Sintético", "medio_dia", "nacional"),
        (8, 15, "Fiesta Local Sintética", "feriado", "Ciudad 1"),
        (11, 27, "Oferta Comercial Sintética", "comercial", "nacional"),
    ]
    for anio in range(inicio.year, fin_cobertura.year + 1):
        for mes, dia, nombre, tipo, ambito in fijos:
            d = date(anio, mes, dia)
            if inicio <= d <= fin_cobertura:
                filas.append((d.isoformat(), nombre, tipo, ambito))
    return sorted(filas)


def escribir_datos(
    carpeta: Any,
    inicio: date = date(2025, 1, 1),
    fin: date = date(2026, 2, 28),
    semilla: int = 7,
) -> dict[str, int]:
    """Escribe los cinco CSV con datos inventados en `carpeta`. Devuelve filas por archivo.

    Ventas Poisson con nivel por familia y tipo de tienda, patrón semanal, tendencia leve, efecto
    de la promoción y de los feriados. La tienda 10 abre en `APERTURA_TIENDA_NUEVA`.
    """
    import csv
    from pathlib import Path

    import numpy as np

    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(semilla)
    tiendas = []
    for i in range(1, 11):
        tipo = _TIPOS[i % len(_TIPOS)]
        apertura = APERTURA_TIENDA_NUEVA if i == TIENDA_NUEVA else date(2019, 1, 1)
        tiendas.append(
            (i, f"Tienda Sintética {i}", f"Ciudad {(i - 1) % 4 + 1}", "Depto", tipo, apertura)
        )
    feriados = _feriados_sinteticos(inicio, FIN_COBERTURA_SINTETICO)
    efecto_fecha = {
        f: (0.5 if t == "feriado" else 1.8 if t == "medio_dia" else 1.15)
        for f, _, t, a in feriados
        if a == "nacional"
    }

    def _escribir(nombre: str, cabecera: list[str], filas: list[tuple]) -> int:
        with open(carpeta / nombre, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(cabecera)
            w.writerows(filas)
        return len(filas)

    historico = []
    dias = (fin - inicio).days + 1
    for k in range(dias):
        d = date.fromordinal(inicio.toordinal() + k)
        base_dia = (
            _FACTOR_DIA[d.weekday()] * (1 + 0.08 * k / 365) * efecto_fecha.get(d.isoformat(), 1.0)
        )
        for tid, _, _, _, tipo, apertura in tiendas:
            if d < apertura:
                continue
            rampa = min(1.0, 0.5 + (d - apertura).days / 240) if tid == TIENDA_NUEVA else 1.0
            for fam, nivel in _NIVEL_FAMILIA.items():
                promo = int(rng.choice([0, 0, 0, 0, 1, 2, 4, 8]))
                lam = nivel * _FACTOR_TIPO[tipo] * base_dia * rampa * (1 + 0.04 * np.log1p(promo))
                historico.append((d.isoformat(), tid, fam, int(rng.poisson(lam)), promo))
    return {
        "tiendas.csv": _escribir(
            "tiendas.csv",
            ["tienda_id", "nombre", "ciudad", "departamento", "tipo", "fecha_apertura"],
            [(*t[:5], t[5].isoformat()) for t in tiendas],
        ),
        "familias.csv": _escribir(
            "familias.csv", ["familia_id", "nombre"], list(FAMILIAS_SINTETICAS.items())
        ),
        "costos.csv": _escribir(
            "costos.csv",
            ["familia_id", "costo_faltante", "costo_sobrante"],
            [(f, cf, cs) for f, (cf, cs) in COSTOS_SINTETICOS.items()],
        ),
        "feriados.csv": _escribir("feriados.csv", ["fecha", "nombre", "tipo", "ambito"], feriados),
        "historico_ventas.csv": _escribir(
            "historico_ventas.csv",
            ["fecha", "tienda_id", "familia_id", "ventas", "en_promocion"],
            historico,
        ),
    }


if __name__ == "__main__":  # python -m novamercado.sintetico CARPETA
    import argparse

    p = argparse.ArgumentParser(description="Escribe datos sintéticos con el formato publicado.")
    p.add_argument("carpeta")
    p.add_argument("--inicio", type=date.fromisoformat, default=date(2025, 1, 1))
    p.add_argument("--fin", type=date.fromisoformat, default=date(2026, 2, 28))
    p.add_argument("--semilla", type=int, default=7)
    a = p.parse_args()
    print(escribir_datos(a.carpeta, a.inicio, a.fin, a.semilla))
