"""Limpieza de solicitudes completas: predicción y observaciones.

Flujo: cuerpo crudo → :func:`leer_cuerpo` → claves (alias/duplicados) → identidad (tienda y
fecha: un problema irreparable es 422) → ítems (un problema se repara, se advierte o descarta el
ítem) → duplicados y faltantes → validación estricta con los modelos de ``contrato.entrada``.

Las funciones son puras: no tocan la BD ni MLflow ni el reloj del sistema.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, TypeVar

from novamercado.contrato.entrada import (
    FamiliaPromocion,
    SolicitudObservaciones,
    SolicitudPrediccion,
    VentaFamilia,
)
from novamercado.contrato.salida import Calidad
from novamercado.limpieza.calidad import Hallazgo, SolicitudRechazada, construir_calidad, mostrar
from novamercado.limpieza.catalogo import Catalogo
from novamercado.limpieza.lectura import ObjetoJSON, leer_cuerpo, parejas, sanear_textos
from novamercado.limpieza.normalizadores import (
    ALIAS_ITEM_OBSERVACIONES,
    ALIAS_ITEM_PREDICCION,
    ALIAS_OBSERVACIONES,
    ALIAS_PREDICCION,
    COMA_AMBIGUA,
    Candidato,
    ClavesResueltas,
    interpretar_numero,
    normalizar_familia,
    normalizar_fecha,
    normalizar_tienda,
    resolver_claves,
    sin_invisibles,
)

T = TypeVar("T")
Tipo = Literal["prediccion", "observaciones"]


# ---------------------------------------------------------------------------------------------
# Resultados
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PrediccionLimpia:
    """Solicitud de predicción lista para el modelo, con su calidad."""

    solicitud: SolicitudPrediccion
    calidad: Calidad
    hallazgos: tuple[Hallazgo, ...]
    familias_completadas: tuple[str, ...]
    claves_ignoradas: tuple[str, ...]


@dataclass(frozen=True)
class ObservacionesLimpias:
    """Observaciones que se pueden guardar (``solicitud.ventas``) y su calidad."""

    solicitud: SolicitudObservaciones
    calidad: Calidad
    hallazgos: tuple[Hallazgo, ...]
    familias_descartadas: tuple[str, ...]
    familias_faltantes: tuple[str, ...]
    claves_ignoradas: tuple[str, ...]


# ---------------------------------------------------------------------------------------------
# Políticas por campo de ítem
# ---------------------------------------------------------------------------------------------


def politica_promocion(valor: Any, campo: str) -> tuple[int, list[Hallazgo]]:
    """``en_promocion``: siempre devuelve un entero ≥ 0 (valor por defecto 0 si no es válido)."""
    recibido = mostrar(valor)
    if isinstance(valor, str) and COMA_AMBIGUA.fullmatch(sin_invisibles(valor).strip()):
        # "2,500": en promoción (valores pequeños) se lee con coma decimal, y se advierte.
        decimal = Decimal(sin_invisibles(valor).strip().replace(",", "."))
        entero = abs(int(decimal.to_integral_value(rounding=ROUND_HALF_UP)))
        return entero, [
            Hallazgo("PROMOCION_AMBIGUA", campo, {"recibido": recibido, "valor": entero})
        ]
    n = interpretar_numero(valor)
    if n.estado == "faltante":
        return 0, [Hallazgo("PROMOCION_FALTANTE", campo)]
    if n.estado == "invalido":
        return 0, [
            Hallazgo("PROMOCION_INVALIDA", campo, {"recibido": recibido, "motivo": n.motivo})
        ]
    if n.negativo:
        return 0, [
            Hallazgo("PROMOCION_INVALIDA", campo, {"recibido": recibido, "motivo": "negativo"})
        ]
    if n.estado in ("decimal", "ambiguo"):
        entero = int(n.valor.to_integral_value(rounding=ROUND_HALF_UP))  # type: ignore[union-attr]
        codigo = "PROMOCION_DECIMAL" if n.estado == "decimal" else "PROMOCION_AMBIGUA"
        return entero, [Hallazgo(codigo, campo, {"recibido": recibido, "valor": entero})]
    if n.estado == "reparado":
        return n.valor, [
            Hallazgo("NUMERO_FORMATO", campo, {"recibido": recibido, "valor": n.valor})
        ]  # type: ignore[return-value]
    return n.valor, []  # type: ignore[return-value]


def politica_ventas(valor: Any, campo: str) -> tuple[int | None, list[Hallazgo]]:
    """``ventas``: devuelve el entero a guardar, o ``None`` si esa familia no se guarda."""
    n = interpretar_numero(valor)
    recibido = mostrar(valor)
    if n.estado in ("faltante", "invalido"):
        return None, [
            Hallazgo("VENTAS_INVALIDAS", campo, {"recibido": recibido, "motivo": n.motivo})
        ]
    if n.negativo:
        return None, [Hallazgo("VENTAS_NEGATIVAS", campo, {"recibido": recibido})]
    if n.estado == "decimal":
        return None, [Hallazgo("VENTAS_DECIMAL", campo, {"recibido": recibido})]
    if n.estado == "ambiguo":
        return None, [Hallazgo("VENTAS_AMBIGUAS", campo, {"recibido": recibido})]
    if n.estado == "reparado":
        return n.valor, [
            Hallazgo("NUMERO_FORMATO", campo, {"recibido": recibido, "valor": n.valor})
        ]  # type: ignore[return-value]
    return n.valor, []  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------------------------


def _huella(valor: Any) -> str:
    return json.dumps(valor, sort_keys=True, default=repr, ensure_ascii=False)


#: Ítems máximos de la lista de familias (el catálogo tiene 12; evita trabajo y respuestas enormes).
LIMITE_ITEMS = 200

#: Valores que se listan en un mensaje antes de resumir (una lista enorme no infla la respuesta).
_MAX_VALORES_MENSAJE = 10

#: Plausibilidad (DECISIONES §1.3): por encima de estos límites el valor se considera atípico.
FACTOR_ATIPICO_VENTAS = 5
MARGEN_ATIPICO_VENTAS = 20
FACTOR_ATIPICO_PROMOCION = 2
MARGEN_ATIPICO_PROMOCION = 5


def limite_ventas(maximo: int) -> int:
    """Ventas atípicas: más de ``5 × máximo histórico + 20`` para esa tienda y familia."""
    return FACTOR_ATIPICO_VENTAS * maximo + MARGEN_ATIPICO_VENTAS


def limite_promocion(maximo: int) -> int:
    """Promoción atípica: más de ``2 × máximo histórico + 5`` para esa familia."""
    return FACTOR_ATIPICO_PROMOCION * maximo + MARGEN_ATIPICO_PROMOCION


def _recortar(texto: str, largo: int = 300) -> str:
    return texto if len(texto) <= largo else texto[: largo - 2] + "…)"


def _texto_valores(valores: list[Any]) -> str:
    textos = ["null" if v is None else str(v) for v in valores]
    if len(textos) <= _MAX_VALORES_MENSAJE:
        return ", ".join(textos)
    return ", ".join(textos[:_MAX_VALORES_MENSAJE]) + f", … ({len(textos)} en total)"


def _sin_repetidos(hallazgos: list[Hallazgo]) -> tuple[Hallazgo, ...]:
    vistos: set[tuple[str, str, str]] = set()
    unicos = []
    for h in hallazgos:
        llave = (h.codigo, h.campo, _huella(h.datos))
        if llave not in vistos:
            vistos.add(llave)
            unicos.append(h)
    return tuple(unicos)


def _es_nulo(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _sin_nulos(candidatos: list[Candidato]) -> list[Candidato]:
    """Si una clave llega repetida (o con alias) y alguna copia es nula, cuenta la que trae valor:
    ``{"tienda_id": null, "tienda": 7}`` es la tienda 7, no una tienda faltante."""
    con_valor = [c for c in candidatos if not _es_nulo(c.valor)]
    return con_valor or candidatos


_FALTANTE = {"tienda_id": "TIENDA_FALTANTE", "fecha": "FECHA_FALTANTE"}


def _identidad(
    res: ClavesResueltas,
    canonica: str,
    normalizar: Callable[[Any, str], tuple[T, list[Hallazgo]]],
    hallazgos: list[Hallazgo],
) -> T:
    """Resuelve un campo de identidad: falta o conflicto → 422; repetido igual → reparar."""
    candidatos = _sin_nulos(res.candidatos.get(canonica, []))
    if not candidatos:
        # Faltante: el 422 nombra las claves que llegaron y no se reconocieron.
        claves = f" (claves no reconocidas: {', '.join(res.ignoradas)})" if res.ignoradas else ""
        raise SolicitudRechazada(_FALTANTE[canonica], canonica, claves=_recortar(claves))
    resultados = [normalizar(c.valor, c.clave) for c in candidatos]
    valores = [r[0] for r in resultados]
    if len(candidatos) > 1:
        if len({_huella(v) for v in valores}) > 1:
            raise SolicitudRechazada(
                "CLAVE_DUPLICADA_IDENTIDAD",
                canonica,
                canonica=canonica,
                veces=len(candidatos),
                valores=_texto_valores([mostrar(c.valor) for c in candidatos]),
            )
        hallazgos.append(
            Hallazgo(
                "CLAVE_DUPLICADA_IGUAL", canonica, {"canonica": canonica, "veces": len(candidatos)}
            )
        )
    for _, hs in resultados:
        hallazgos.extend(hs)
    return valores[0]


def _items(
    res: ClavesResueltas, canonica: str, campo_valor: str, hallazgos: list[Hallazgo]
) -> tuple[str, list[tuple[str, Any]]]:
    """Devuelve la clave recibida de la lista y sus ítems como ``(campo_base, ítem)``."""
    candidatos = res.candidatos.get(canonica, [])
    if not candidatos:
        raise SolicitudRechazada("SIN_FAMILIAS", canonica, lista=canonica)
    if len(candidatos) > 1:
        if len({_huella(c.valor) for c in candidatos}) > 1:
            raise SolicitudRechazada(
                "LISTA_DUPLICADA", canonica, canonica=canonica, veces=len(candidatos)
            )
        hallazgos.append(
            Hallazgo(
                "CLAVE_DUPLICADA_IGUAL", canonica, {"canonica": canonica, "veces": len(candidatos)}
            )
        )
    clave, valor = candidatos[-1].clave, candidatos[-1].valor
    es_prediccion = campo_valor == "en_promocion"
    alias_item = ALIAS_ITEM_PREDICCION if es_prediccion else ALIAS_ITEM_OBSERVACIONES
    if isinstance(valor, dict) and resolver_claves(parejas(valor), alias_item).candidatos.get(
        "familia_id"
    ):
        # Un solo ítem enviado sin la lista: {"familia_id": "abarrotes", "en_promocion": 3}.
        hallazgos.append(Hallazgo("LISTA_UN_ITEM", clave, {"lista": clave}))
        return clave, [(f"{clave}[0]", valor)]
    if isinstance(valor, list | dict) and len(valor) > LIMITE_ITEMS:
        raise SolicitudRechazada(
            "LISTA_GRANDE", clave, lista=clave, n=len(valor), limite=LIMITE_ITEMS
        )
    if isinstance(valor, list):
        items = [(f"{clave}[{i}]", item) for i, item in enumerate(valor)]
    elif isinstance(valor, dict) and valor:
        hallazgos.append(Hallazgo("LISTA_COMO_OBJETO", clave, {"lista": clave}))
        items = []
        for k, v in parejas(valor):
            item = (
                {"familia_id": k, **v} if isinstance(v, dict) else {"familia_id": k, campo_valor: v}
            )
            items.append((f"{clave}.{k}", item))
    else:
        raise SolicitudRechazada("SIN_FAMILIAS", clave, lista=clave)
    return clave, items


def _campo_item(
    candidatos: list[Candidato],
    base: str,
    canonica: str,
    evaluar: Callable[[Any, str], tuple[T, list[Hallazgo]]],
    tipo: Tipo,
    hallazgos: list[Hallazgo],
) -> tuple[T | None, bool]:
    """Evalúa un campo de ítem con posibles duplicados. Devuelve ``(valor, en_conflicto)``.

    Repetido con el mismo valor → reparar; con valores distintos → advertir y usar el último
    (predicción) o no guardar (observaciones, ``en_conflicto=True``).
    """
    candidatos = _sin_nulos(candidatos)
    resultados = [evaluar(c.valor, f"{base}.{c.clave}") for c in candidatos]
    for _, hs in resultados:
        hallazgos.extend(hs)
    valores = [r[0] for r in resultados]
    if len(candidatos) > 1:
        datos = {"canonica": canonica, "veces": len(candidatos)}
        if len({_huella(v) for v in valores}) == 1:
            hallazgos.append(Hallazgo("CLAVE_DUPLICADA_IGUAL", f"{base}.{canonica}", datos))
        else:
            efecto = "se usó el último" if tipo == "prediccion" else "no se guardó la familia"
            datos |= {
                "valores": _texto_valores([mostrar(c.valor) for c in candidatos]),
                "efecto": efecto,
            }
            hallazgos.append(Hallazgo("CLAVE_DUPLICADA_ITEM", f"{base}.{canonica}", datos))
            if tipo == "observaciones":
                return None, True
    return valores[-1], False


def _evaluar_familia(
    catalogo: Catalogo, tipo_solicitud: Tipo, aproximadas: set[str]
) -> Callable[[Any, str], tuple[str | None, list[Hallazgo]]]:
    """Evalúa la familia de un ítem. En observaciones una familia aproximada no se guarda; en
    predicción se usa, y su campo queda en ``aproximadas`` (una exacta repetida gana)."""

    def evaluar(valor: Any, campo: str) -> tuple[str | None, list[Hallazgo]]:
        fid, tipo = normalizar_familia(valor, catalogo)
        datos = {"recibido": mostrar(valor), "familia": fid}
        if tipo == "reparado":
            return fid, [Hallazgo("FAMILIA_FORMATO", campo, datos)]
        if tipo == "aproximada" and tipo_solicitud == "observaciones":
            return None, [Hallazgo("FAMILIA_APROXIMADA_OBS", campo, datos)]
        if tipo == "aproximada":
            aproximadas.add(campo)
            return fid, [Hallazgo("FAMILIA_APROXIMADA", campo, datos)]
        if tipo == "desconocida":
            return None, [Hallazgo("FAMILIA_DESCONOCIDA", campo, datos)]
        return fid, []

    return evaluar


@dataclass
class _Entrada:
    familia_id: str
    valor: Any
    base: str
    aproximada: bool = False


def _item_desde_texto(item: Any) -> Any:
    """Un ítem enviado como texto JSON de un objeto se decodifica; si no, se devuelve igual."""
    if not isinstance(item, str) or not item.strip().startswith("{"):
        return item
    try:
        datos = json.loads(item, object_pairs_hook=ObjetoJSON.desde_pares)
    except (ValueError, RecursionError):
        return item
    return sanear_textos(datos)[0] if isinstance(datos, dict) else item


def _limpiar_items(
    items: list[tuple[str, Any]],
    catalogo: Catalogo,
    tipo: Tipo,
    hallazgos: list[Hallazgo],
    ignoradas: list[str] | None = None,
) -> list[_Entrada]:
    """Normaliza cada ítem; descarta (y advierte) los que no tienen familia reconocible.

    Las claves desconocidas de cada ítem se agregan a ``ignoradas`` (con su ruta) para la BD.
    """
    alias = ALIAS_ITEM_PREDICCION if tipo == "prediccion" else ALIAS_ITEM_OBSERVACIONES
    campo_valor = "en_promocion" if tipo == "prediccion" else "ventas"
    evaluar_valor = politica_promocion if tipo == "prediccion" else politica_ventas
    aproximadas: set[str] = set()
    evaluar_familia = _evaluar_familia(catalogo, tipo, aproximadas)
    entradas: list[_Entrada] = []
    for base, item in items:
        decodificado = _item_desde_texto(item)
        if decodificado is not item:
            hallazgos.append(Hallazgo("ITEM_COMO_TEXTO", base))
            item = decodificado
        elif isinstance(item, str) and item.strip():
            # ["abarrotes", "bebidas"]: el ítem es solo la familia.
            hallazgos.append(Hallazgo("ITEM_SOLO_FAMILIA", base, {"recibido": mostrar(item)}))
            item = {"familia_id": item}
        if not isinstance(item, dict):
            hallazgos.append(Hallazgo("ITEM_INVALIDO", base, {"tipo": mostrar(item)}))
            continue
        res = resolver_claves(parejas(item), alias, prefijo=f"{base}.")
        hallazgos.extend(res.hallazgos)
        if ignoradas is not None:
            ignoradas.extend(f"{base}.{k}" for k in res.ignoradas)
        cand_familia = res.candidatos.get("familia_id", [])
        if not cand_familia:
            hallazgos.append(
                Hallazgo("FAMILIA_DESCONOCIDA", f"{base}.familia_id", {"recibido": "(ausente)"})
            )
            continue
        fid, conflicto = _campo_item(
            cand_familia, base, "familia_id", evaluar_familia, tipo, hallazgos
        )
        if fid is None or conflicto:
            continue
        aproximada = any(c.startswith(f"{base}.") for c in aproximadas)
        if campo_valor not in res.candidatos and tipo == "observaciones":
            # La clave de ventas no llegó (o con un nombre no reconocido): se dice así.
            motivo = "no llegó la clave 'ventas'"
            if res.ignoradas:
                motivo += f"; claves no reconocidas: {', '.join(res.ignoradas)}"
            hallazgos.append(
                Hallazgo(
                    "VENTAS_INVALIDAS",
                    f"{base}.ventas",
                    {"recibido": "(ausente)", "motivo": _recortar(motivo, 200)},
                )
            )
            entradas.append(_Entrada(fid, None, base, aproximada))
            continue
        cand_valor = res.candidatos.get(campo_valor, [Candidato(campo_valor, None)])
        valor, conflicto = _campo_item(
            cand_valor, base, campo_valor, evaluar_valor, tipo, hallazgos
        )
        entradas.append(_Entrada(fid, None if conflicto else valor, base, aproximada))
    return entradas


def _agrupar(entradas: list[_Entrada]) -> dict[str, list[_Entrada]]:
    grupos: dict[str, list[_Entrada]] = {}
    for e in entradas:
        grupos.setdefault(e.familia_id, []).append(e)
    return grupos


def _identidad_comun(
    datos: Any,
    catalogo: Catalogo,
    alias: dict[str, frozenset[str]],
    hallazgos: list[Hallazgo],
    tipo: Tipo,
) -> tuple[ClavesResueltas, int, date]:
    if not isinstance(datos, dict):
        raise SolicitudRechazada("RAIZ_NO_OBJETO", "", tipo=type(datos).__name__)
    res = resolver_claves(parejas(datos), alias)
    hallazgos.extend(res.hallazgos)
    tienda_id = _identidad(
        res, "tienda_id", lambda v, c: normalizar_tienda(v, catalogo, c), hallazgos
    )
    fecha = _identidad(
        res, "fecha", lambda v, c: normalizar_fecha(v, catalogo, tienda_id, c, tipo), hallazgos
    )
    return res, tienda_id, fecha


# ---------------------------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------------------------


def limpiar_prediccion(
    datos: Any, catalogo: Catalogo, hallazgos_previos: tuple[Hallazgo, ...] = ()
) -> PrediccionLimpia:
    """Limpia una solicitud de predicción ya decodificada o lanza :class:`SolicitudRechazada`.

    Siempre devuelve **todas** las familias del catálogo: las faltantes con ``en_promocion=0``.
    Una familia repetida con valores distintos usa la última aparición.
    """
    hallazgos = list(hallazgos_previos)
    res, tienda_id, fecha = _identidad_comun(
        datos, catalogo, ALIAS_PREDICCION, hallazgos, "prediccion"
    )
    clave_lista, items = _items(res, "familias", "en_promocion", hallazgos)
    ignoradas = list(res.ignoradas)
    grupos = _agrupar(_limpiar_items(items, catalogo, "prediccion", hallazgos, ignoradas))
    if not grupos:
        raise SolicitudRechazada("SIN_FAMILIAS", clave_lista, lista=clave_lista)

    familias: list[FamiliaPromocion] = []
    for fid, grupo in grupos.items():
        exactas = [e for e in grupo if not e.aproximada]
        grupo = exactas or grupo  # una coincidencia exacta tiene prioridad sobre una aproximada
        valores = [e.valor for e in grupo]
        if len(grupo) > 1:
            ultima = grupo[-1]
            datos_h = {"familia": fid, "veces": len(grupo)}
            if len(set(valores)) == 1:
                hallazgos.append(Hallazgo("FAMILIA_REPETIDA_IGUAL", ultima.base, datos_h))
            else:
                datos_h |= {"valores": _texto_valores(valores), "valor": ultima.valor}
                hallazgos.append(Hallazgo("FAMILIA_REPETIDA_PRED", ultima.base, datos_h))
        promocion = valores[-1]
        maximo = catalogo.max_promocion.get(fid)
        if maximo is not None and promocion > limite_promocion(maximo):
            hallazgos.append(
                Hallazgo(
                    "PROMOCION_ATIPICA",
                    f"{grupo[-1].base}.en_promocion",
                    {"valor": promocion, "limite": limite_promocion(maximo), "maximo": maximo},
                )
            )
            promocion = limite_promocion(maximo)
        familias.append(FamiliaPromocion(familia_id=fid, en_promocion=promocion))

    faltantes = tuple(f for f in catalogo.familias if f not in grupos)
    if faltantes:
        hallazgos.append(
            Hallazgo(
                "FAMILIAS_FALTANTES_PRED",
                clave_lista,
                {"n": len(faltantes), "familias": ", ".join(faltantes)},
            )
        )
        familias.extend(FamiliaPromocion(familia_id=f, en_promocion=0) for f in faltantes)

    unicos = _sin_repetidos(hallazgos)
    return PrediccionLimpia(
        solicitud=SolicitudPrediccion(tienda_id=tienda_id, fecha=fecha, familias=familias),
        calidad=construir_calidad(unicos),
        hallazgos=unicos,
        familias_completadas=faltantes,
        claves_ignoradas=tuple(ignoradas),
    )


def limpiar_observaciones(
    datos: Any, catalogo: Catalogo, hallazgos_previos: tuple[Hallazgo, ...] = ()
) -> ObservacionesLimpias:
    """Limpia una solicitud de observaciones ya decodificada o lanza :class:`SolicitudRechazada`.

    Una familia con ventas inválidas, negativas, decimales o duplicadas en conflicto **no se
    guarda** (se advierte); las faltantes se advierten y se guardan las recibidas.
    """
    hallazgos = list(hallazgos_previos)
    res, tienda_id, fecha = _identidad_comun(
        datos, catalogo, ALIAS_OBSERVACIONES, hallazgos, "observaciones"
    )
    clave_lista, items = _items(res, "ventas", "ventas", hallazgos)
    ignoradas = list(res.ignoradas)
    grupos = _agrupar(_limpiar_items(items, catalogo, "observaciones", hallazgos, ignoradas))
    if not grupos:
        raise SolicitudRechazada("SIN_FAMILIAS", clave_lista, lista=clave_lista)

    ventas: list[VentaFamilia] = []
    descartadas: list[str] = []
    for fid, grupo in grupos.items():
        valores = [e.valor for e in grupo]
        if len(grupo) > 1 and any(v is not None for v in valores):
            datos_h = {"familia": fid, "veces": len(grupo)}
            if len(set(valores)) == 1:
                hallazgos.append(Hallazgo("FAMILIA_REPETIDA_IGUAL", grupo[-1].base, datos_h))
            else:
                datos_h["valores"] = _texto_valores(valores)
                hallazgos.append(Hallazgo("FAMILIA_REPETIDA_OBS", grupo[-1].base, datos_h))
                descartadas.append(fid)
                continue
        if valores[-1] is None:
            descartadas.append(fid)
            continue
        maximo = catalogo.max_ventas.get((tienda_id, fid))
        if maximo is not None and valores[-1] > limite_ventas(maximo):
            hallazgos.append(
                Hallazgo(
                    "VENTAS_ATIPICAS",
                    f"{grupo[-1].base}.ventas",
                    {
                        "valor": valores[-1],
                        "limite": limite_ventas(maximo),
                        "factor": FACTOR_ATIPICO_VENTAS,
                        "maximo": maximo,
                    },
                )
            )
            descartadas.append(fid)
            continue
        ventas.append(VentaFamilia(familia_id=fid, ventas=valores[-1]))

    faltantes = tuple(f for f in catalogo.familias if f not in grupos)
    if faltantes:
        hallazgos.append(
            Hallazgo(
                "FAMILIAS_FALTANTES_OBS",
                clave_lista,
                {"n": len(faltantes), "familias": ", ".join(faltantes)},
            )
        )

    unicos = _sin_repetidos(hallazgos)
    return ObservacionesLimpias(
        solicitud=SolicitudObservaciones(tienda_id=tienda_id, fecha=fecha, ventas=ventas),
        calidad=construir_calidad(unicos),
        hallazgos=unicos,
        familias_descartadas=tuple(descartadas),
        familias_faltantes=faltantes,
        claves_ignoradas=tuple(ignoradas),
    )


def procesar_prediccion(cuerpo: bytes | str, catalogo: Catalogo) -> PrediccionLimpia:
    """Cuerpo crudo de ``POST /v1/prediccion`` → solicitud limpia (o 422)."""
    leido = leer_cuerpo(cuerpo)
    return limpiar_prediccion(leido.datos, catalogo, leido.hallazgos)


def procesar_observaciones(cuerpo: bytes | str, catalogo: Catalogo) -> ObservacionesLimpias:
    """Cuerpo crudo de ``POST /v1/observaciones`` → observaciones limpias (o 422)."""
    leido = leer_cuerpo(cuerpo)
    return limpiar_observaciones(leido.datos, catalogo, leido.hallazgos)
