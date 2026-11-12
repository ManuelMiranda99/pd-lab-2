"""Catálogo que necesita la limpieza: tiendas, familias y rango de fechas válido.

El rango de fechas **se deriva de los datos**, nunca del reloj del sistema:

- inicio = primera fecha de ``historico_ventas.csv`` (antes no hay datos: se rechaza);
- fin de cobertura = última fecha de ``feriados.csv`` (después se advierte).

Los normalizadores reciben el catálogo como parámetro; las pruebas usan uno sintético.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType


@dataclass(frozen=True)
class Tienda:
    """Tienda del catálogo."""

    tienda_id: int
    nombre: str
    fecha_apertura: date


@dataclass(frozen=True)
class Catalogo:
    """Catálogo inmutable que se inyecta en la limpieza.

    ``familias`` conserva el orden del catálogo (id → nombre legible).

    Referencias de plausibilidad (derivadas del histórico publicado; vacías = sin control):

    - ``fecha_fin_historico``: último día del histórico (una observación anterior lo reemplaza);
    - ``max_ventas``: máximo histórico de ventas por ``(tienda_id, familia_id)``;
    - ``max_promocion``: máximo histórico de ``en_promocion`` por familia.
    """

    tiendas: Mapping[int, Tienda]
    familias: Mapping[str, str]
    fecha_inicio: date
    fecha_fin_cobertura: date
    fecha_fin_historico: date | None = None
    max_ventas: Mapping[tuple[int, str], int] = field(default_factory=dict)
    max_promocion: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.fecha_inicio > self.fecha_fin_cobertura:
            raise ValueError("fecha_inicio posterior a fecha_fin_cobertura")
        if not self.tiendas or not self.familias:
            raise ValueError("el catálogo necesita al menos una tienda y una familia")
        object.__setattr__(self, "tiendas", MappingProxyType(dict(self.tiendas)))
        object.__setattr__(self, "familias", MappingProxyType(dict(self.familias)))
        object.__setattr__(self, "max_ventas", MappingProxyType(dict(self.max_ventas)))
        object.__setattr__(self, "max_promocion", MappingProxyType(dict(self.max_promocion)))


def _leer_csv(ruta: Path) -> list[dict[str, str]]:
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _fechas_columna(ruta: Path, columna: str = "fecha") -> tuple[date, date]:
    """Mínimo y máximo de una columna de fechas ISO, sin cargar el archivo completo en memoria."""
    minimo: str | None = None
    maximo: str | None = None
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        for fila in csv.DictReader(f):
            valor = (fila.get(columna) or "").strip()
            if not valor:
                continue
            if minimo is None or valor < minimo:
                minimo = valor
            if maximo is None or valor > maximo:
                maximo = valor
    if minimo is None or maximo is None:
        raise ValueError(f"{ruta.name} no tiene fechas en la columna '{columna}'")
    return date.fromisoformat(minimo), date.fromisoformat(maximo)


def cargar_catalogo(datos_dir: str | Path) -> Catalogo:
    """Lee ``tiendas.csv``, ``familias.csv``, ``feriados.csv`` e ``historico_ventas.csv``.

    Si falta el histórico, el inicio se toma de la fecha más antigua de ``feriados.csv``.
    """
    base = Path(datos_dir)
    tiendas = {
        int(f["tienda_id"]): Tienda(
            tienda_id=int(f["tienda_id"]),
            nombre=f["nombre"].strip(),
            fecha_apertura=date.fromisoformat(f["fecha_apertura"].strip()),
        )
        for f in _leer_csv(base / "tiendas.csv")
    }
    familias = {
        f["familia_id"].strip(): f["nombre"].strip() for f in _leer_csv(base / "familias.csv")
    }
    inicio_feriados, fin = _fechas_columna(base / "feriados.csv")
    historico = base / "historico_ventas.csv"
    inicio = _fechas_columna(historico)[0] if historico.exists() else inicio_feriados
    return Catalogo(
        tiendas=tiendas, familias=familias, fecha_inicio=inicio, fecha_fin_cobertura=fin
    )
