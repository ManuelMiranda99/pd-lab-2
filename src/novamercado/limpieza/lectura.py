"""Lectura tolerante del cuerpo crudo de una solicitud.

Se lee el cuerpo como bytes **sin mirar el ``Content-Type``** y se decodifica como JSON con estas
defensas:

- BOM UTF-8 o UTF-16 y textos en Latin-1/cp1252;
- claves duplicadas: se conservan **todas** las parejas (``object_pairs_hook``) para que la
  limpieza decida con la tabla de políticas;
- ``NaN``/``Infinity`` y enteros gigantes se convierten en :class:`NoRepresentable` (nunca llegan
  a Postgres ni al modelo);
- un objeto enviado como texto JSON se decodifica una vez más;
- los surrogates UTF-16 sueltos se reemplazan por ``?`` (no se pueden codificar en UTF-8);
- cuerpo vacío, JSON inválido, anidamiento excesivo o raíz que no es objeto → 422.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from novamercado.limpieza.calidad import Hallazgo, SolicitudRechazada

#: Tamaño máximo del cuerpo aceptado (una solicitud válida ocupa ~1 KB).
LIMITE_BYTES = 1_000_000
#: Dígitos máximos de un entero JSON antes de tratarlo como no representable.
_MAX_DIGITOS = 30


@dataclass(frozen=True)
class NoRepresentable:
    """Número JSON que no puede usarse: ``NaN``, ``Infinity`` o un entero gigante."""

    texto: str

    def __str__(self) -> str:
        return self.texto


class ObjetoJSON(dict):
    """``dict`` que recuerda todas las parejas clave-valor, incluidas las claves repetidas.

    Como un ``dict`` normal, conserva el último valor de cada clave; ``pares`` guarda todo.
    """

    pares: list[tuple[str, Any]]

    @classmethod
    def desde_pares(cls, pares: list[tuple[str, Any]]) -> ObjetoJSON:
        """Construye el objeto conservando el orden y las repeticiones."""
        obj = cls(pares)
        obj.pares = list(pares)
        return obj


def parejas(obj: dict[str, Any]) -> list[tuple[str, Any]]:
    """Todas las parejas de un objeto (con repeticiones si viene de :class:`ObjetoJSON`)."""
    return list(getattr(obj, "pares", obj.items()))


def _entero(texto: str) -> int | NoRepresentable:
    digitos = texto.lstrip("-")
    return NoRepresentable(texto) if len(digitos) > _MAX_DIGITOS else int(texto)


def _real(texto: str) -> float | NoRepresentable:
    valor = float(texto)
    return valor if valor not in (float("inf"), float("-inf")) else NoRepresentable(texto)


def _decodificar(cuerpo: bytes) -> tuple[str, str | None]:
    """Devuelve el texto y, si no era UTF-8, el nombre de la codificación usada."""
    if cuerpo.startswith(b"\xef\xbb\xbf"):
        cuerpo = cuerpo[3:]
    if cuerpo.startswith((b"\xff\xfe", b"\xfe\xff")):
        return cuerpo.decode("utf-16", errors="replace"), "UTF-16"
    try:
        return cuerpo.decode("utf-8"), None
    except UnicodeDecodeError:
        return cuerpo.decode("cp1252", errors="replace"), "Latin-1/cp1252"


def _texto_valido(texto: str) -> tuple[str, bool]:
    """Reemplaza los surrogates UTF-16 sueltos (``"\\ud800"`` en JSON) por ``?``.

    Python los acepta al decodificar el JSON, pero no se pueden codificar en UTF-8: llegarían a
    la respuesta o a Postgres y romperían la serialización.
    """
    try:
        texto.encode("utf-8")
    except UnicodeEncodeError:
        return texto.encode("utf-8", errors="replace").decode("utf-8"), True
    return texto, False


def sanear_textos(valor: Any) -> tuple[Any, bool]:
    """Recorre el JSON decodificado y sanea claves y textos. Devuelve ``(valor, hubo_cambios)``."""
    if isinstance(valor, str):
        return _texto_valido(valor)
    if isinstance(valor, list):
        cambios = False
        salida = []
        for item in valor:
            nuevo, cambio = sanear_textos(item)
            salida.append(nuevo)
            cambios |= cambio
        return salida, cambios
    if isinstance(valor, dict):
        cambios = False
        pares = []
        for clave, item in parejas(valor):
            clave_nueva, c1 = sanear_textos(clave)
            nuevo, c2 = sanear_textos(item)
            pares.append((clave_nueva, nuevo))
            cambios |= c1 or c2
        return (ObjetoJSON.desde_pares(pares), True) if cambios else (valor, False)
    return valor, False


def _cargar(texto: str) -> Any:
    return json.loads(
        texto,
        object_pairs_hook=ObjetoJSON.desde_pares,
        parse_constant=NoRepresentable,
        parse_int=_entero,
        parse_float=_real,
    )


@dataclass(frozen=True)
class CuerpoLeido:
    """Resultado de la lectura: el objeto JSON y los hallazgos de transporte."""

    datos: ObjetoJSON
    hallazgos: tuple[Hallazgo, ...] = ()


def leer_cuerpo(cuerpo: bytes | str) -> CuerpoLeido:
    """Lee el cuerpo crudo y devuelve un objeto JSON o lanza :class:`SolicitudRechazada`."""
    hallazgos: list[Hallazgo] = []
    if isinstance(cuerpo, str):
        cuerpo = cuerpo.encode("utf-8")
    if len(cuerpo) > LIMITE_BYTES:
        raise SolicitudRechazada("CUERPO_GRANDE", "", limite=LIMITE_BYTES)
    texto, codificacion = _decodificar(cuerpo)
    if codificacion:
        hallazgos.append(Hallazgo("CODIFICACION", "cuerpo", {"codificacion": codificacion}))
    texto = texto.lstrip("﻿").strip()
    if not texto:
        raise SolicitudRechazada("CUERPO_VACIO", "")
    try:
        datos = _cargar(texto)
        if isinstance(datos, str) and datos.strip().startswith("{"):
            datos = _cargar(datos.strip())
            hallazgos.append(Hallazgo("JSON_ANIDADO", "cuerpo"))
    except json.JSONDecodeError as exc:
        raise SolicitudRechazada(
            "JSON_INVALIDO", "", motivo=f"{exc.msg} (línea {exc.lineno}, columna {exc.colno})"
        ) from None
    except (RecursionError, ValueError) as exc:
        raise SolicitudRechazada("JSON_INVALIDO", "", motivo=type(exc).__name__) from None
    if not isinstance(datos, dict):
        raise SolicitudRechazada("RAIZ_NO_OBJETO", "", tipo=_tipo_json(datos))
    try:
        datos, saneado = sanear_textos(datos)
    except RecursionError:
        raise SolicitudRechazada("JSON_INVALIDO", "", motivo="RecursionError") from None
    if saneado:
        hallazgos.append(Hallazgo("TEXTO_INVALIDO", "cuerpo"))
    return CuerpoLeido(datos=datos, hallazgos=tuple(hallazgos))


def _tipo_json(valor: Any) -> str:
    if isinstance(valor, list):
        return "una lista"
    if isinstance(valor, str):
        return "un texto"
    if valor is None:
        return "null"
    if isinstance(valor, bool):
        return "un booleano"
    return "un número"
