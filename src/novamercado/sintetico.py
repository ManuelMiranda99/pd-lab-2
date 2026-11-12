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
