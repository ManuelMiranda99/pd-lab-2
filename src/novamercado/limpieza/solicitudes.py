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
from decimal import ROUND_HALF_UP
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
from novamercado.limpieza.lectura import leer_cuerpo, parejas
from novamercado.limpieza.normalizadores import (
    ALIAS_ITEM_OBSERVACIONES,
    ALIAS_ITEM_PREDICCION,
    ALIAS_OBSERVACIONES,
    ALIAS_PREDICCION,
    Candidato,
    ClavesResueltas,
    interpretar_numero,
    normalizar_familia,
    normalizar_fecha,
    normalizar_tienda,
    resolver_claves,
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
    n = interpretar_numero(valor)
    recibido = mostrar(valor)
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
    if n.estado == "decimal":
        entero = int(n.valor.to_integral_value(rounding=ROUND_HALF_UP))  # type: ignore[union-attr]
        return entero, [
            Hallazgo("PROMOCION_DECIMAL", campo, {"recibido": recibido, "valor": entero})
        ]
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


def _texto_valores(valores: list[Any]) -> str:
    return ", ".join("null" if v is None else str(v) for v in valores)


def _sin_repetidos(hallazgos: list[Hallazgo]) -> tuple[Hallazgo, ...]:
    vistos: set[tuple[str, str, str]] = set()
    unicos = []
    for h in hallazgos:
        llave = (h.codigo, h.campo, _huella(h.datos))
        if llave not in vistos:
            vistos.add(llave)
            unicos.append(h)
    return tuple(unicos)


def _identidad(
    res: ClavesResueltas,
    canonica: str,
    normalizar: Callable[[Any, str], tuple[T, list[Hallazgo]]],
    hallazgos: list[Hallazgo],
) -> T:
    """Resuelve un campo de identidad: falta o conflicto → 422; repetido igual → reparar."""
    candidatos = res.candidatos.get(canonica, [])
    if not candidatos:
        return normalizar(None, canonica)[0]  # lanza el rechazo de "faltante"
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


def _evaluar_familia(catalogo: Catalogo) -> Callable[[Any, str], tuple[str | None, list[Hallazgo]]]:
    def evaluar(valor: Any, campo: str) -> tuple[str | None, list[Hallazgo]]:
        fid, tipo = normalizar_familia(valor, catalogo)
        datos = {"recibido": mostrar(valor), "familia": fid}
        if tipo == "reparado":
            return fid, [Hallazgo("FAMILIA_FORMATO", campo, datos)]
        if tipo == "aproximada":
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


def _limpiar_items(
    items: list[tuple[str, Any]],
    catalogo: Catalogo,
    tipo: Tipo,
    hallazgos: list[Hallazgo],
) -> list[_Entrada]:
    """Normaliza cada ítem; descarta (y advierte) los que no tienen familia reconocible."""
    alias = ALIAS_ITEM_PREDICCION if tipo == "prediccion" else ALIAS_ITEM_OBSERVACIONES
    campo_valor = "en_promocion" if tipo == "prediccion" else "ventas"
    evaluar_valor = politica_promocion if tipo == "prediccion" else politica_ventas
    evaluar_familia = _evaluar_familia(catalogo)
    entradas: list[_Entrada] = []
    for base, item in items:
        if not isinstance(item, dict):
            hallazgos.append(Hallazgo("ITEM_INVALIDO", base, {"tipo": mostrar(item)}))
            continue
        res = resolver_claves(parejas(item), alias, prefijo=f"{base}.")
        hallazgos.extend(res.hallazgos)
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
        cand_valor = res.candidatos.get(campo_valor, [Candidato(campo_valor, None)])
        valor, conflicto = _campo_item(
            cand_valor, base, campo_valor, evaluar_valor, tipo, hallazgos
        )
        entradas.append(_Entrada(fid, None if conflicto else valor, base))
    return entradas


def _agrupar(entradas: list[_Entrada]) -> dict[str, list[_Entrada]]:
    grupos: dict[str, list[_Entrada]] = {}
    for e in entradas:
        grupos.setdefault(e.familia_id, []).append(e)
    return grupos


def _identidad_comun(
    datos: Any, catalogo: Catalogo, alias: dict[str, frozenset[str]], hallazgos: list[Hallazgo]
) -> tuple[ClavesResueltas, int, date]:
    if not isinstance(datos, dict):
        raise SolicitudRechazada("RAIZ_NO_OBJETO", "", tipo=type(datos).__name__)
    res = resolver_claves(parejas(datos), alias)
    hallazgos.extend(res.hallazgos)
    tienda_id = _identidad(
        res, "tienda_id", lambda v, c: normalizar_tienda(v, catalogo, c), hallazgos
    )
    fecha = _identidad(
        res, "fecha", lambda v, c: normalizar_fecha(v, catalogo, tienda_id, c), hallazgos
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
    res, tienda_id, fecha = _identidad_comun(datos, catalogo, ALIAS_PREDICCION, hallazgos)
    clave_lista, items = _items(res, "familias", "en_promocion", hallazgos)
    grupos = _agrupar(_limpiar_items(items, catalogo, "prediccion", hallazgos))
    if not grupos:
        raise SolicitudRechazada("SIN_FAMILIAS", clave_lista, lista=clave_lista)

    familias: list[FamiliaPromocion] = []
    for fid, grupo in grupos.items():
        valores = [e.valor for e in grupo]
        if len(grupo) > 1:
            ultima = grupo[-1]
            datos_h = {"familia": fid, "veces": len(grupo)}
            if len(set(valores)) == 1:
                hallazgos.append(Hallazgo("FAMILIA_REPETIDA_IGUAL", ultima.base, datos_h))
            else:
                datos_h |= {"valores": _texto_valores(valores), "valor": ultima.valor}
                hallazgos.append(Hallazgo("FAMILIA_REPETIDA_PRED", ultima.base, datos_h))
        familias.append(FamiliaPromocion(familia_id=fid, en_promocion=valores[-1]))

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
        claves_ignoradas=res.ignoradas,
    )


def limpiar_observaciones(
    datos: Any, catalogo: Catalogo, hallazgos_previos: tuple[Hallazgo, ...] = ()
) -> ObservacionesLimpias:
    """Limpia una solicitud de observaciones ya decodificada o lanza :class:`SolicitudRechazada`.

    Una familia con ventas inválidas, negativas, decimales o duplicadas en conflicto **no se
    guarda** (se advierte); las faltantes se advierten y se guardan las recibidas.
    """
    hallazgos = list(hallazgos_previos)
    res, tienda_id, fecha = _identidad_comun(datos, catalogo, ALIAS_OBSERVACIONES, hallazgos)
    clave_lista, items = _items(res, "ventas", "ventas", hallazgos)
    grupos = _agrupar(_limpiar_items(items, catalogo, "observaciones", hallazgos))
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
        claves_ignoradas=res.ignoradas,
    )


def procesar_prediccion(cuerpo: bytes | str, catalogo: Catalogo) -> PrediccionLimpia:
    """Cuerpo crudo de ``POST /v1/prediccion`` → solicitud limpia (o 422)."""
    leido = leer_cuerpo(cuerpo)
    return limpiar_prediccion(leido.datos, catalogo, leido.hallazgos)


def procesar_observaciones(cuerpo: bytes | str, catalogo: Catalogo) -> ObservacionesLimpias:
    """Cuerpo crudo de ``POST /v1/observaciones`` → observaciones limpias (o 422)."""
    leido = leer_cuerpo(cuerpo)
    return limpiar_observaciones(leido.datos, catalogo, leido.hallazgos)
