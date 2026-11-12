"""Utilidades compartidas por `smoke.py` y `simulador.py` (pruebas de punta a punta).

- `Cliente`: llamadas HTTP a la API que **validan cada respuesta contra el contrato** del Anexo
  (los mismos modelos Pydantic de `novamercado.contrato`) y acumulan los problemas encontrados.
- `ERRORES_*`: generadores de solicitudes sucias (catálogo E01–E10 + errores inventados).
- `Compose`: `docker compose stop/start/down/up` del proyecto (nunca `down -v`).

Nada de aquí usa el reloj del sistema para decidir sobre fechas: `time.monotonic()` solo mide
duraciones y plazos de espera.
"""

from __future__ import annotations

import copy
import json
import random
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from novamercado.contrato import (
    ErrorRespuesta,
    Health,
    ModeloInfo,
    ObservacionesResponse,
    PrediccionResponse,
    ReentrenoIniciado,
    ReentrenoStatus,
)

RAIZ = Path(__file__).resolve().parents[1]

#: Códigos permitidos por el Anexo para cada endpoint (cualquier otro es un problema).
CODIGOS: dict[str, set[int]] = {
    "prediccion": {200, 422, 503},
    "observaciones": {200, 422, 503},
    "reentrenar": {202, 503},
    "estado_reentreno": {200, 404, 503},
    "health": {200, 503},
    "modelo": {200, 503},
}

_MODELO_200: dict[str, type[BaseModel]] = {
    "prediccion": PrediccionResponse,
    "observaciones": ObservacionesResponse,
    "reentrenar": ReentrenoIniciado,
    "estado_reentreno": ReentrenoStatus,
    "health": Health,
    "modelo": ModeloInfo,
}


@dataclass
class Respuesta:
    endpoint: str
    codigo: int
    cuerpo: Any
    ms: float
    problemas: list[str] = field(default_factory=list)


def validar_respuesta(endpoint: str, codigo: int, texto: str) -> list[str]:
    """Problemas de contrato de una respuesta (lista vacía = cumple)."""
    problemas: list[str] = []
    if codigo not in CODIGOS[endpoint]:
        problemas.append(f"{endpoint}: código {codigo} no permitido")
    try:
        datos = json.loads(texto)
    except ValueError:
        return [*problemas, f"{endpoint}: respuesta {codigo} no es JSON"]
    exito = codigo in (200, 202) or (endpoint == "health" and codigo == 503)
    modelo = _MODELO_200[endpoint] if exito else ErrorRespuesta
    try:
        modelo.model_validate_json(texto)
    except ValidationError as exc:
        err = exc.errors()[0]
        problemas.append(f"{endpoint} {codigo}: {err['msg']} ({err['loc']})")
    if endpoint == "prediccion" and codigo == 200 and isinstance(datos, dict):
        if not isinstance(datos.get("version_modelo"), str):
            problemas.append("prediccion: version_modelo no es texto")
        familias = [p.get("familia_id") for p in datos.get("predicciones", [])]
        if len(familias) != len(set(familias)):
            problemas.append("prediccion: familias repetidas en la respuesta")
    return problemas


class Cliente:
    """Cliente de la API que valida y registra cada respuesta."""

    def __init__(self, base: str = "http://localhost:8000", timeout: float = 30.0) -> None:
        self.http = httpx.Client(base_url=base, timeout=timeout)
        self.codigos: Counter[tuple[str, int]] = Counter()
        self.problemas: list[str] = []
        self.latencias: dict[str, list[float]] = {}

    def _llamar(self, endpoint: str, metodo: str, ruta: str, **kw: Any) -> Respuesta:
        t0 = time.monotonic()
        try:
            r = self.http.request(metodo, ruta, **kw)
        except httpx.HTTPError as exc:
            self.problemas.append(f"{endpoint}: sin respuesta ({type(exc).__name__})")
            return Respuesta(endpoint, 0, None, (time.monotonic() - t0) * 1000, ["sin respuesta"])
        ms = (time.monotonic() - t0) * 1000
        problemas = validar_respuesta(endpoint, r.status_code, r.text)
        self.codigos[(endpoint, r.status_code)] += 1
        self.latencias.setdefault(endpoint, []).append(ms)
        self.problemas.extend(problemas)
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = r.text
        return Respuesta(endpoint, r.status_code, cuerpo, ms, problemas)

    def prediccion(self, cuerpo: Any, *, crudo: bytes | None = None, **kw: Any) -> Respuesta:
        if crudo is not None:
            return self._llamar("prediccion", "POST", "/v1/prediccion", content=crudo, **kw)
        return self._llamar("prediccion", "POST", "/v1/prediccion", json=cuerpo)

    def observaciones(self, cuerpo: Any, *, crudo: bytes | None = None, **kw: Any) -> Respuesta:
        if crudo is not None:
            return self._llamar("observaciones", "POST", "/v1/observaciones", content=crudo, **kw)
        return self._llamar("observaciones", "POST", "/v1/observaciones", json=cuerpo)

    def health(self) -> Respuesta:
        return self._llamar("health", "GET", "/health")

    def modelo(self) -> Respuesta:
        return self._llamar("modelo", "GET", "/modelo")

    def reentrenar(self) -> Respuesta:
        return self._llamar("reentrenar", "POST", "/admin/reentrenar")

    def estado_reentreno(self, id_: str) -> Respuesta:
        return self._llamar("estado_reentreno", "GET", f"/admin/reentrenar/{id_}")

    # --- esperas ---------------------------------------------------------------------------

    def esperar_health(self, estado: str, limite_s: float, intervalo: float = 2.0) -> float | None:
        """Segundos hasta que `/health` reporta `estado` (None si no ocurre en `limite_s`)."""
        return esperar(
            lambda: isinstance((c := self.health().cuerpo), dict) and c.get("estado") == estado,
            limite_s,
            intervalo,
        )

    def esperar_reentreno(self, id_: str, limite_s: float = 600) -> tuple[float, Respuesta]:
        """Consulta el estado hasta que deja de estar `en_curso`. Devuelve (segundos, respuesta)."""
        t0 = time.monotonic()
        ultima = self.estado_reentreno(id_)
        while (
            isinstance(ultima.cuerpo, dict)
            and ultima.cuerpo.get("estado") == "en_curso"
            and time.monotonic() - t0 < limite_s
        ):
            time.sleep(3)
            ultima = self.estado_reentreno(id_)
        return time.monotonic() - t0, ultima


def esperar(condicion: Callable[[], bool], limite_s: float, intervalo: float = 2.0) -> float | None:
    """Repite `condicion` hasta que sea verdadera; devuelve los segundos o None si vence."""
    t0 = time.monotonic()
    while True:
        try:
            if condicion():
                return time.monotonic() - t0
        except Exception:  # noqa: BLE001 — una dependencia caída es parte de lo que se espera
            pass
        if time.monotonic() - t0 >= limite_s:
            return None
        time.sleep(intervalo)


class Compose:
    """Control del stack con `docker compose` (nunca borra volúmenes)."""

    def __init__(self, proyecto: str | None = None, entorno: dict[str, str] | None = None) -> None:
        self.base = ["docker", "compose"] + (["-p", proyecto] if proyecto else [])
        self.entorno = entorno

    def __call__(self, *args: str, timeout: int = 600) -> str:
        if "-v" in args or "--volumes" in args:
            raise ValueError("Este script nunca borra volúmenes")
        import os

        env = {**os.environ, **(self.entorno or {})}
        r = subprocess.run(
            [*self.base, *args],
            cwd=RAIZ,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            raise RuntimeError(f"docker compose {' '.join(args)} falló: {r.stderr[-500:]}")
        return r.stdout


# ---------------------------------------------------------------------------------------------
# Errores inyectados. Cada función recibe una solicitud limpia y un rng y devuelve una variante.
# `EQUIVALENTES` producen la misma predicción que la limpia (se reparan sin pérdida).
# ---------------------------------------------------------------------------------------------

Mutador = Callable[[dict[str, Any], random.Random], dict[str, Any]]


def _e01(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    t = s["tienda_id"]
    s["tienda_id"] = rng.choice([f"T{t:02d}", str(t), f"tienda-{t:02d}", f" {t} "])
    return s


_VARIANTES_FAMILIA = {
    "lacteos": ["LÁCTEOS", "lacteos ", "Lácteos"],
    "frutas_verduras": ["Frutas y Verduras", "FRUTAS_VERDURAS", "frutas y verduras"],
    "panaderia": ["Panadería", "PANADERIA"],
    "bebe": ["Bebé", "BEBE"],
    "cuidado_personal": ["Cuidado Personal", "cuidado personal"],
}


def _e02(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    for item in s["familias"]:
        if item["familia_id"] in _VARIANTES_FAMILIA and rng.random() < 0.7:
            item["familia_id"] = rng.choice(_VARIANTES_FAMILIA[item["familia_id"]])
    return s


def _e03(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    for item in s["familias"]:
        if rng.random() < 0.5:
            v = item["en_promocion"]
            item["en_promocion"] = rng.choice([str(v), float(v)])
    return s


def _e04(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    a, m, d = s["fecha"].split("-")
    opciones = [f"{s['fecha']}T00:00:00", f"{a}{m}{d}"]
    if int(d) > 12:  # sin ambigüedad DD/MM
        opciones.append(f"{d}/{m}/{a}")
    s["fecha"] = rng.choice(opciones)
    return s


def _e07(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    clave = rng.choice(["tienda", "tiendaId", "Tienda_ID"])
    s[clave] = s.pop("tienda_id")
    return s


def _camel(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    s["Fecha"] = s.pop("fecha")
    s["familias"] = [
        {"familiaId": i["familia_id"], "enPromocion": i["en_promocion"]} for i in s["familias"]
    ]
    return s


def _claves_extra(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    s["origen"] = "pos-legacy"
    s["familias"][0]["comentario"] = "sin cambios"
    return s


def _desordenada(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    rng.shuffle(s["familias"])
    return s


EQUIVALENTES_PREDICCION: dict[str, Mutador] = {
    "E01": _e01,
    "E02": _e02,
    "E03": _e03,
    "E04": _e04,
    "E07": _e07,
    "inv_camel_case": _camel,
    "inv_claves_extra": _claves_extra,
    "inv_orden": _desordenada,
}

#: Equivalentes que no dejan observación de calidad (R9: claves desconocidas se ignoran sin
#: observación; el orden de las familias no importa) → `ok`. El resto → `corregido`/`advertencia`.
SIN_OBSERVACION = {"inv_claves_extra", "inv_orden"}


def estado_esperado(nombre: str) -> set[str]:
    """Estados de calidad aceptables para una variante equivalente."""
    return {"ok"} if nombre in SIN_OBSERVACION else {"corregido", "advertencia"}


def _e05(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    for _ in range(2):
        s["familias"].pop(rng.randrange(len(s["familias"])))
    return s


def _e06(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    item = copy.deepcopy(rng.choice(s["familias"]))
    item["en_promocion"] = item["en_promocion"] + 3
    s["familias"].append(item)
    return s


def _familia_inventada(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    s["familias"].append({"familia_id": rng.choice(["juguetes", "", None, 42]), "en_promocion": 1})
    return s


def _promo_rara(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    item = rng.choice(s["familias"])
    item["en_promocion"] = rng.choice([-3, 2.5, "muchas", None, True, [1], 1e9, "NaN", {"x": 1}])
    return s


def _sin_familias(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    s["familias"] = rng.choice([[], None, "todas", {}])
    return s


SUCIAS_PREDICCION: dict[str, Mutador] = {
    "E05": _e05,
    "E06": _e06,
    "inv_familia_desconocida": _familia_inventada,
    "inv_promocion_fuera_de_dominio": _promo_rara,
    "inv_sin_familias": _sin_familias,
}


#: Identidades irreparables (cada valor debe dar 422). Ojo: `20260101` (entero AAAAMMDD) sí se
#: repara, igual que `"20260415"`.
TIENDAS_INEXISTENTES: list[Any] = [99, 0, -1, "ninguna", None, 3.7, True, [3]]
FECHAS_IMPOSIBLES: list[Any] = ["2026-02-30", "mañana", "", None, "2026-13-01", 3.5, ["2026-03-10"]]


def _tienda_inexistente(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    s["tienda_id"] = rng.choice(TIENDAS_INEXISTENTES)
    return s


def _fecha_imposible(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    s["fecha"] = rng.choice(FECHAS_IMPOSIBLES)
    return s


def _sin_tienda(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    s.pop("tienda_id")
    return s


#: Variantes que deben terminar en 422 (identidad irreparable).
RECHAZOS: dict[str, Mutador] = {
    "inv_tienda_inexistente": _tienda_inexistente,
    "inv_fecha_imposible": _fecha_imposible,
    "inv_sin_tienda": _sin_tienda,
}

#: Cuerpos crudos que no son una solicitud (deben terminar en 422, nunca 500).
CUERPOS_ROTOS: list[tuple[str, bytes, dict[str, str]]] = [
    ("vacio", b"", {}),
    ("json_roto", b'{"tienda_id": 3, "fecha": ', {"content-type": "application/json"}),
    ("lista", b"[1, 2, 3]", {"content-type": "application/json"}),
    ("null", b"null", {"content-type": "application/json"}),
    ("texto_plano", b"hola", {"content-type": "text/plain"}),
    ("binario", bytes(range(256)), {"content-type": "application/octet-stream"}),
]


def _e08(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    rng.choice(s["ventas"])["ventas"] = -rng.randint(1, 20)
    return s


def _e10(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    for item in s["ventas"]:
        v = item["ventas"]
        if v >= 1000 and rng.random() < 0.7:
            item["ventas"] = f"{v // 1000},{v % 1000:03d}"
        elif rng.random() < 0.3:
            item["ventas"] = f"{v},0"
    return s


def _obs_tipos(s: dict, rng: random.Random) -> dict:
    s = copy.deepcopy(s)
    for item in s["ventas"]:
        if rng.random() < 0.4:
            item["ventas"] = rng.choice([str(item["ventas"]), float(item["ventas"])])
    return s


#: Variantes de observación que guardan los mismos valores que la limpia.
EQUIVALENTES_OBSERVACION: dict[str, Mutador] = {
    "E01": _e01,
    "E04": _e04,
    "E07": _e07,
    "E10": _e10,
    "inv_tipos": _obs_tipos,
}


def mutar(mutadores: dict[str, Mutador], s: dict, rng: random.Random) -> tuple[str, dict]:
    """Aplica un mutador elegido al azar. Devuelve (nombre, solicitud)."""
    nombre = rng.choice(sorted(mutadores))
    return nombre, mutadores[nombre](s, rng)
