"""Normalizadores puros y generales (no atados a los ejemplos del catálogo de errores).

Cada función recibe un valor crudo (y el catálogo si lo necesita) y devuelve el valor canónico
junto con los hallazgos, o lanza :class:`SolicitudRechazada`. Ninguna decide acciones: los
códigos de hallazgo se resuelven en la tabla de políticas. Ninguna usa el reloj del sistema.
"""

from __future__ import annotations

import difflib
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from novamercado.contrato.entrada import LIMITE_ENTERO
from novamercado.limpieza.calidad import Hallazgo, SolicitudRechazada, mostrar
from novamercado.limpieza.catalogo import Catalogo
from novamercado.limpieza.lectura import NoRepresentable

# ---------------------------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------------------------


def sin_acentos(texto: str) -> str:
    """NFKC + elimina marcas diacríticas (``"Lácteos"`` → ``"Lacteos"``)."""
    descompuesto = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", texto))
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def forma_canonica(texto: str) -> str:
    """Forma comparable: sin acentos, minúsculas, separadores (espacio ``-`` ``.`` ``/``) → ``_``.

    ``"Frutas y Verduras"`` → ``"frutas_y_verduras"``; ``"tiendaId"`` → ``"tienda_id"``.
    """
    texto = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", sin_acentos(texto).strip())
    texto = texto.casefold().replace("&", " y ")
    return re.sub(r"[^0-9a-z]+", "_", texto).strip("_")


def forma_compacta(texto: str) -> str:
    """Forma sin conectores ni separadores: ``"Frutas & verduras"`` → ``"frutasverduras"``."""
    partes = [p for p in forma_canonica(texto).split("_") if p not in ("y", "e", "and", "")]
    return "".join(partes)


# ---------------------------------------------------------------------------------------------
# Claves (alias, capitalización y duplicados)
# ---------------------------------------------------------------------------------------------

#: Alias aceptados por clave canónica (comparados en :func:`forma_canonica`).
ALIAS_PREDICCION: dict[str, frozenset[str]] = {
    "tienda_id": frozenset(
        {
            "tienda_id",
            "tienda",
            "id_tienda",
            "tiendaid",
            "store_id",
            "store",
            "codigo_tienda",
            "cod_tienda",
            "sucursal",
            "sucursal_id",
            "id_sucursal",
            "numero_tienda",
            "num_tienda",
        }
    ),
    "fecha": frozenset(
        {
            "fecha",
            "date",
            "dia",
            "fecha_prediccion",
            "fecha_pronostico",
            "fecha_venta",
            "fecha_observacion",
        }
    ),
    "familias": frozenset(
        {"familias", "familia", "items", "detalle", "productos", "promociones", "lineas"}
    ),
}
ALIAS_OBSERVACIONES: dict[str, frozenset[str]] = {
    "tienda_id": ALIAS_PREDICCION["tienda_id"],
    "fecha": ALIAS_PREDICCION["fecha"],
    "ventas": frozenset(
        {
            "ventas",
            "venta",
            "observaciones",
            "familias",
            "items",
            "detalle",
            "lineas",
            "ventas_reales",
        }
    ),
}
_ALIAS_FAMILIA = frozenset(
    {
        "familia_id",
        "familia",
        "id_familia",
        "familiaid",
        "family",
        "family_id",
        "categoria",
        "categoria_id",
        "nombre_familia",
    }
)
ALIAS_ITEM_PREDICCION: dict[str, frozenset[str]] = {
    "familia_id": _ALIAS_FAMILIA,
    "en_promocion": frozenset(
        {
            "en_promocion",
            "promocion",
            "promociones",
            "promo",
            "en_promo",
            "promos",
            "productos_en_promocion",
            "num_promociones",
            "cantidad_promocion",
        }
    ),
}
ALIAS_ITEM_OBSERVACIONES: dict[str, frozenset[str]] = {
    "familia_id": _ALIAS_FAMILIA,
    "ventas": frozenset(
        {"ventas", "venta", "unidades", "cantidad", "unidades_vendidas", "ventas_reales", "valor"}
    ),
}


@dataclass(frozen=True)
class Candidato:
    """Un valor recibido para una clave canónica, con la clave tal como llegó."""

    clave: str
    valor: Any


@dataclass(frozen=True)
class ClavesResueltas:
    """Claves de un objeto agrupadas por clave canónica."""

    candidatos: dict[str, list[Candidato]]
    ignoradas: tuple[str, ...]
    hallazgos: tuple[Hallazgo, ...]


def resolver_claves(
    pares: list[tuple[str, Any]], alias: dict[str, frozenset[str]], prefijo: str = ""
) -> ClavesResueltas:
    """Agrupa las parejas de un objeto por clave canónica (alias, mayúsculas, camelCase).

    Las claves que no son exactamente la canónica generan ``CLAVE_RENOMBRADA``. Las claves
    desconocidas se devuelven en ``ignoradas`` (sin observación). Los duplicados se dejan como
    varios candidatos para que el llamador decida con la tabla de políticas.
    """
    indice = {forma: canonica for canonica, formas in alias.items() for forma in formas}
    candidatos: dict[str, list[Candidato]] = {}
    ignoradas: list[str] = []
    hallazgos: list[Hallazgo] = []
    renombradas: set[str] = set()
    for clave, valor in pares:
        texto = clave if isinstance(clave, str) else str(clave)
        canonica = indice.get(forma_canonica(texto))
        if canonica is None:
            ignoradas.append(texto)
            continue
        candidatos.setdefault(canonica, []).append(Candidato(texto, valor))
        if texto != canonica and texto not in renombradas:
            renombradas.add(texto)
            hallazgos.append(
                Hallazgo(
                    "CLAVE_RENOMBRADA",
                    f"{prefijo}{texto}",
                    {"recibida": texto, "canonica": canonica},
                )
            )
    return ClavesResueltas(candidatos, tuple(ignoradas), tuple(hallazgos))


# ---------------------------------------------------------------------------------------------
# Números
# ---------------------------------------------------------------------------------------------

EstadoNumero = Literal["ok", "reparado", "decimal", "invalido", "faltante"]

_SIGNO = r"[+-]?"
_PATRONES_NUMERO: tuple[tuple[re.Pattern[str], str, str], ...] = (
    # (patrón, separador de miles a quitar, separador decimal a convertir en ".")
    (re.compile(rf"{_SIGNO}\d+"), "", ""),
    (re.compile(rf"{_SIGNO}\d{{1,3}}(,\d{{3}})+"), ",", ""),
    (re.compile(rf"{_SIGNO}\d{{1,3}}(\.\d{{3}}){{2,}}"), ".", ""),
    (re.compile(rf"{_SIGNO}\d{{1,3}}( \d{{3}})+"), " ", ""),
    (re.compile(rf"{_SIGNO}\d{{1,3}}(,\d{{3}})+\.\d+"), ",", ""),
    (re.compile(rf"{_SIGNO}\d{{1,3}}(\.\d{{3}})+,\d+"), ".", ","),
    (re.compile(rf"{_SIGNO}\d+,(\d{{1,2}}|\d{{4,}})"), "", ","),
    (re.compile(rf"{_SIGNO}(\d+\.\d*|\.\d+)([eE]{_SIGNO}\d+)?"), "", ""),
    (re.compile(rf"{_SIGNO}\d+[eE]{_SIGNO}\d+"), "", ""),
)


@dataclass(frozen=True)
class Numero:
    """Interpretación de un valor numérico recibido.

    - ``ok``: entero JSON sin cambios; ``reparado``: entero obtenido sin pérdida;
    - ``decimal``: número con parte fraccionaria (``valor`` es ``Decimal``);
    - ``invalido``: no representable (``motivo`` lo explica); ``faltante``: ``null``/ausente.
    """

    estado: EstadoNumero
    valor: int | Decimal | None = None
    motivo: str = ""

    @property
    def negativo(self) -> bool:
        """``True`` si el valor interpretado es menor que cero."""
        return self.valor is not None and self.valor < 0


def _desde_decimal(d: Decimal, reparado: bool) -> Numero:
    if not d.is_finite():
        return Numero("invalido", motivo="no es finito")
    if abs(d) > LIMITE_ENTERO:
        return Numero("invalido", motivo="fuera de rango")
    if d == d.to_integral_value():
        return Numero("reparado" if reparado else "ok", int(d))
    return Numero("decimal", d)


def interpretar_numero(valor: Any) -> Numero:
    """Interpreta un número recibido como JSON o como texto, sin conversiones con pérdida.

    Acepta ``"3"``, ``2.0``, ``"1,234"`` (miles), ``"812,0"`` (coma decimal), ``"1.234.567"``,
    ``"1 234"``, ``"1e3"`` y dígitos de ancho completo. ``"1.234"`` se lee como decimal (punto
    decimal JSON). Los booleanos, listas, objetos, ``NaN``/``Infinity`` y enteros que no caben en
    un ``integer`` de PostgreSQL son inválidos.
    """
    if valor is None:
        return Numero("faltante", motivo="nulo")
    if isinstance(valor, bool):
        return Numero("invalido", motivo="booleano")
    if isinstance(valor, NoRepresentable):
        return Numero("invalido", motivo="no es finito o es demasiado grande")
    if isinstance(valor, int):
        if abs(valor) > LIMITE_ENTERO:
            return Numero("invalido", motivo="fuera de rango")
        return Numero("ok", valor)
    if isinstance(valor, float):
        if not math.isfinite(valor):
            return Numero("invalido", motivo="no es finito")
        return _desde_decimal(Decimal(repr(valor)), reparado=True)
    if isinstance(valor, str):
        texto = unicodedata.normalize("NFKC", valor).replace(" ", " ").strip()
        texto = texto.replace("'", "").replace("_", "")
        if not texto:
            return Numero("faltante", motivo="texto vacío")
        for patron, miles, coma in _PATRONES_NUMERO:
            if patron.fullmatch(texto):
                limpio = texto.replace(miles, "") if miles else texto
                limpio = limpio.replace(coma, ".") if coma else limpio
                try:
                    return _desde_decimal(Decimal(limpio), reparado=True)
                except InvalidOperation:  # pragma: no cover - los patrones lo impiden
                    break
        return Numero("invalido", motivo="texto no numérico")
    return Numero("invalido", motivo=f"tipo {type(valor).__name__}")


# ---------------------------------------------------------------------------------------------
# Tienda
# ---------------------------------------------------------------------------------------------

_PATRON_TIENDA = re.compile(r"(?:t|tda|tienda|store|sucursal|no|num|n)?_?0*(\d{1,9})(?:_0+)?")


def normalizar_tienda(
    valor: Any, catalogo: Catalogo, campo: str = "tienda_id"
) -> tuple[int, list[Hallazgo]]:
    """Normaliza el id de tienda: ``5``, ``"5"``, ``"T05"``, ``5.0``, ``"Tienda 5"`` o el nombre.

    Rechaza si falta, si no se puede interpretar o si no existe en el catálogo.
    """
    if valor is None:
        raise SolicitudRechazada("TIENDA_FALTANTE", campo)
    tienda_id: int | None = None
    if isinstance(valor, bool | list | dict | NoRepresentable):
        tienda_id = None
    elif isinstance(valor, int | float):
        num = interpretar_numero(valor)
        tienda_id = num.valor if num.estado in ("ok", "reparado") else None  # type: ignore[assignment]
    elif isinstance(valor, str):
        forma = forma_canonica(valor)
        num = interpretar_numero(valor)
        coincidencia = _PATRON_TIENDA.fullmatch(forma)
        if num.estado in ("ok", "reparado", "decimal"):
            tienda_id = num.valor if num.estado != "decimal" else None  # type: ignore[assignment]
        elif coincidencia:
            tienda_id = int(coincidencia.group(1))
        else:
            por_nombre = [
                t.tienda_id for t in catalogo.tiendas.values() if forma_canonica(t.nombre) == forma
            ]
            tienda_id = por_nombre[0] if len(por_nombre) == 1 else None
    if tienda_id is None:
        if isinstance(valor, str) and not valor.strip():
            raise SolicitudRechazada("TIENDA_FALTANTE", campo)
        raise SolicitudRechazada("TIENDA_ILEGIBLE", campo, recibido=mostrar(valor))
    if tienda_id not in catalogo.tiendas:
        raise SolicitudRechazada("TIENDA_INEXISTENTE", campo, tienda_id=tienda_id)
    hallazgos = []
    if type(valor) is not int:
        hallazgos.append(
            Hallazgo("TIENDA_FORMATO", campo, {"recibido": mostrar(valor), "tienda_id": tienda_id})
        )
    return tienda_id, hallazgos


# ---------------------------------------------------------------------------------------------
# Fecha
# ---------------------------------------------------------------------------------------------

_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_ISO_FLEXIBLE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
_ISO_CON_HORA = re.compile(
    r"(\d{4})-(\d{1,2})-(\d{1,2})[T ]\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?",
    re.IGNORECASE,
)
_COMPACTA = re.compile(r"(\d{4})(\d{2})(\d{2})")
_DIA_MES = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")


def _construir(anio: str, mes: str, dia: str) -> date | None:
    try:
        return date(int(anio), int(mes), int(dia))
    except ValueError:
        return None


def interpretar_fecha(valor: Any) -> tuple[date, Literal["ok", "reparado", "ambigua"]]:
    """Interpreta una fecha sin reloj del sistema; lanza ``ValueError`` si es ilegible.

    Formatos: ``AAAA-MM-DD`` (ok); con hora o zona, ``AAAA/MM/DD``, ``AAAA.MM.DD``,
    ``AAAAMMDD`` (texto o entero), ``DD/MM/AAAA`` (reparado); ``D/M/AAAA`` con ambos ≤ 12 y
    distintos → ``ambigua`` (se interpreta día/mes). Si el primer número es ≤ 12 y el segundo
    > 12, la única lectura posible es mes/día (reparado).
    """
    if isinstance(valor, bool):
        raise ValueError("booleano")
    if isinstance(valor, int):
        valor = str(valor)
    if not isinstance(valor, str):
        raise ValueError("tipo no admitido")
    texto = unicodedata.normalize("NFKC", valor).strip()
    if m := _ISO.fullmatch(texto):
        d = _construir(*m.groups())
        if d is not None:
            return d, "ok" if texto == valor else "reparado"
    for patron in (_ISO_CON_HORA, _ISO_FLEXIBLE, _COMPACTA):
        if m := patron.fullmatch(texto):
            d = _construir(*m.groups()[:3])
            if d is None:
                break
            return d, "reparado"
    if m := _DIA_MES.fullmatch(texto):
        a, b, anio = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12 and b <= 12:
            d = _construir(anio, str(b), str(a))
            tipo = "reparado"
        elif a <= 12 < b:
            d = _construir(anio, str(a), str(b))
            tipo = "reparado"
        else:
            d = _construir(anio, str(b), str(a))
            tipo = "reparado" if a == b else "ambigua"
        if d is not None:
            return d, tipo  # type: ignore[return-value]
    raise ValueError("formato no reconocido")


def normalizar_fecha(
    valor: Any, catalogo: Catalogo, tienda_id: int, campo: str = "fecha"
) -> tuple[date, list[Hallazgo]]:
    """Normaliza la fecha y la valida contra el rango derivado de los datos.

    - antes del inicio del histórico o de la apertura de la tienda → rechazo;
    - después de la cobertura de ``feriados.csv`` → advertencia;
    - fechas pasadas dentro del histórico se aceptan (el servicio opera en fechas simuladas).
    """
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        raise SolicitudRechazada("FECHA_FALTANTE", campo)
    try:
        fecha, tipo = interpretar_fecha(valor)
    except ValueError:
        raise SolicitudRechazada("FECHA_ILEGIBLE", campo, recibido=mostrar(valor)) from None
    hallazgos: list[Hallazgo] = []
    datos = {"recibido": mostrar(valor), "fecha": fecha.isoformat()}
    if tipo == "reparado":
        hallazgos.append(Hallazgo("FECHA_FORMATO", campo, datos))
    elif tipo == "ambigua":
        hallazgos.append(Hallazgo("FECHA_AMBIGUA", campo, datos))
    if fecha < catalogo.fecha_inicio:
        raise SolicitudRechazada(
            "FECHA_ANTES_HISTORICO",
            campo,
            fecha=fecha.isoformat(),
            inicio=catalogo.fecha_inicio.isoformat(),
        )
    apertura = catalogo.tiendas[tienda_id].fecha_apertura
    if fecha < apertura:
        raise SolicitudRechazada(
            "FECHA_ANTES_APERTURA",
            campo,
            tienda_id=tienda_id,
            apertura=apertura.isoformat(),
            fecha=fecha.isoformat(),
        )
    if fecha > catalogo.fecha_fin_cobertura:
        hallazgos.append(
            Hallazgo(
                "FECHA_FUERA_COBERTURA",
                campo,
                {"fecha": fecha.isoformat(), "fin": catalogo.fecha_fin_cobertura.isoformat()},
            )
        )
    return fecha, hallazgos


# ---------------------------------------------------------------------------------------------
# Familia
# ---------------------------------------------------------------------------------------------

_UMBRAL_DIFUSO = 0.8


def _indice_familias(catalogo: Catalogo) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    canonicas: dict[str, set[str]] = {}
    compactas: dict[str, set[str]] = {}
    for fid, nombre in catalogo.familias.items():
        for texto in (fid, nombre):
            canonicas.setdefault(forma_canonica(texto), set()).add(fid)
            compactas.setdefault(forma_compacta(texto), set()).add(fid)
    return canonicas, compactas


def normalizar_familia(
    valor: Any, catalogo: Catalogo
) -> tuple[str | None, Literal["ok", "reparado", "aproximada", "desconocida"]]:
    """Busca la familia por id **y** por nombre del catálogo.

    Coincidencia exacta → ``ok``; única tras normalizar (mayúsculas, acentos, espacios, ``_ - &
    y``) → ``reparado``; única aproximada (similitud ≥ 0.8) → ``aproximada``; si no →
    ``desconocida`` (``None``).
    """
    if not isinstance(valor, str):
        return None, "desconocida"
    if valor in catalogo.familias:
        return valor, "ok"
    canonicas, compactas = _indice_familias(catalogo)
    for indice, forma in ((canonicas, forma_canonica(valor)), (compactas, forma_compacta(valor))):
        ids = indice.get(forma)
        if ids is not None:
            return (next(iter(ids)), "reparado") if len(ids) == 1 else (None, "desconocida")
    compacta = forma_compacta(valor)
    if not compacta:
        return None, "desconocida"
    cercanas = difflib.get_close_matches(compacta, list(compactas), n=3, cutoff=_UMBRAL_DIFUSO)
    ids = {fid for c in cercanas for fid in compactas[c]}
    if len(ids) == 1:
        return next(iter(ids)), "aproximada"
    return None, "desconocida"
