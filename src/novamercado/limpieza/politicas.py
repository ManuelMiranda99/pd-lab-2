"""Tabla declarativa de políticas de datos: **única fuente** de qué se hace ante cada problema.

Cada problema detectado por la limpieza se identifica con un código. La tabla dice la acción
(``reparar`` / ``advertir`` / ``rechazar`` / ``ignorar``), el efecto concreto y la plantilla del
mensaje que se devuelve en ``calidad.observaciones[].detalle`` o en el ``detail`` del 422.

Los normalizadores nunca deciden la acción: solo informan el código del problema. La tabla de
``DECISIONES.md §1`` se genera con :func:`tabla_markdown` y un test verifica que coincide.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Accion(StrEnum):
    """Acción que aplica el servicio ante un problema."""

    REPARAR = "reparar"
    ADVERTIR = "advertir"
    RECHAZAR = "rechazar"
    IGNORAR = "ignorar"


@dataclass(frozen=True)
class Politica:
    """Una fila de la tabla: problema → acción → efecto → mensaje."""

    codigo: str
    problema: str
    accion: Accion
    efecto: str
    mensaje: str
    ejemplo: str = ""

    def formatear(self, **datos: Any) -> str:
        """Devuelve el mensaje con los datos del caso concreto."""
        return self.mensaje.format(**datos)


_R, _A, _X, _I = Accion.REPARAR, Accion.ADVERTIR, Accion.RECHAZAR, Accion.IGNORAR

_FILAS: tuple[Politica, ...] = (
    # --- Lectura del cuerpo y estructura -------------------------------------------------------
    Politica(
        "CUERPO_VACIO",
        "Cuerpo vacío o solo espacios",
        _X,
        "422",
        "el cuerpo de la solicitud está vacío",
    ),
    Politica(
        "CUERPO_GRANDE",
        "Cuerpo mayor al límite (1 MB)",
        _X,
        "422",
        "el cuerpo supera el límite de {limite} bytes",
    ),
    Politica(
        "JSON_INVALIDO",
        "JSON mal formado, ilegible o demasiado anidado",
        _X,
        "422",
        "el cuerpo no es un JSON válido: {motivo}",
    ),
    Politica(
        "RAIZ_NO_OBJETO",
        "La raíz del JSON no es un objeto (lista, texto, número)",
        _X,
        "422",
        "se esperaba un objeto JSON en la raíz y se recibió {tipo}",
    ),
    Politica(
        "CODIFICACION",
        "Texto en codificación distinta de UTF-8 (Latin-1, UTF-16)",
        _R,
        "se decodifica y se continúa",
        "el cuerpo venía en {codificacion}; se decodificó",
        "bytes Latin-1",
    ),
    Politica(
        "JSON_ANIDADO",
        "Objeto JSON enviado como texto JSON (doble codificación)",
        _R,
        "se decodifica una vez más",
        "el cuerpo era un texto con JSON; se decodificó",
    ),
    Politica(
        "FORMA_TRANSPORTE",
        "BOM UTF-8 o `Content-Type` distinto de JSON",
        _I,
        "se lee el cuerpo como JSON, sin observación",
        "",
    ),
    # --- Claves ---------------------------------------------------------------------------------
    Politica(
        "CLAVE_RENOMBRADA",
        "Clave con alias, mayúsculas, espacios o camelCase",
        _R,
        "se usa la clave canónica",
        "'{recibida}' se interpretó como '{canonica}'",
        "E07 `tienda`",
    ),
    Politica(
        "CLAVE_DUPLICADA_IGUAL",
        "Clave repetida (o canónica y alias) con el mismo valor",
        _R,
        "se conserva un valor",
        "'{canonica}' aparece {veces} veces con el mismo valor",
    ),
    Politica(
        "CLAVE_DUPLICADA_IDENTIDAD",
        "`tienda_id` o `fecha` repetida (o canónica y alias) con valores distintos",
        _X,
        "422",
        "'{canonica}' aparece {veces} veces con valores distintos ({valores})",
    ),
    Politica(
        "CLAVE_DUPLICADA_ITEM",
        "Clave de un ítem repetida con valores distintos",
        _A,
        "predicción: se usa el último valor; observación: no se guarda la familia",
        "'{canonica}' aparece {veces} veces con valores distintos ({valores}); {efecto}",
    ),
    Politica(
        "CLAVE_DESCONOCIDA",
        "Clave que no pertenece al contrato",
        _I,
        "se ignora sin observación (se registra en BD)",
        "",
    ),
    # --- Tienda ---------------------------------------------------------------------------------
    Politica(
        "TIENDA_FORMATO",
        "Id de tienda como texto, con prefijo, decimal entero o nombre",
        _R,
        "se usa el id numérico",
        "'{recibido}' se interpretó como la tienda {tienda_id}",
        'E01 `"T05"`, `"5"`, `5.0`',
    ),
    Politica(
        "TIENDA_FALTANTE",
        "Falta `tienda_id` o es nulo",
        _X,
        "422",
        "falta el identificador de tienda",
    ),
    Politica(
        "TIENDA_ILEGIBLE",
        "Id de tienda no interpretable (booleano, lista, `5.5`, texto)",
        _X,
        "422",
        "no se pudo interpretar '{recibido}' como identificador de tienda",
    ),
    Politica(
        "TIENDA_INEXISTENTE",
        "Tienda que no está en el catálogo",
        _X,
        "422",
        "la tienda {tienda_id} no existe en el catálogo",
    ),
    # --- Fecha ----------------------------------------------------------------------------------
    Politica(
        "FECHA_FORMATO",
        "Fecha en otro formato inequívoco (hora, `/`, `AAAAMMDD`, D/M)",
        _R,
        "se usa AAAA-MM-DD",
        "'{recibido}' se interpretó como {fecha}",
        "E04 `17/11/2025`, `…T00:00:00`",
    ),
    Politica(
        "FECHA_AMBIGUA",
        "Fecha D/M/AAAA con día y mes ≤ 12 (`05/06/2026`)",
        _A,
        "se interpreta como DD/MM/AAAA",
        "'{recibido}' es ambigua; se interpretó como {fecha} (día/mes/año)",
    ),
    Politica("FECHA_FALTANTE", "Falta `fecha` o es nula", _X, "422", "falta la fecha"),
    Politica(
        "FECHA_ILEGIBLE",
        "Fecha ilegible o imposible (`2025-02-30`, año de 2 dígitos)",
        _X,
        "422",
        "no se pudo interpretar '{recibido}' como fecha",
    ),
    Politica(
        "FECHA_ANTES_HISTORICO",
        "Fecha anterior al inicio del histórico",
        _X,
        "422",
        "la fecha {fecha} es anterior al inicio de los datos ({inicio})",
    ),
    Politica(
        "FECHA_ANTES_APERTURA",
        "Fecha anterior a la apertura de la tienda",
        _X,
        "422",
        "la tienda {tienda_id} abrió el {apertura}; no opera el {fecha}",
    ),
    Politica(
        "FECHA_FUERA_COBERTURA",
        "Fecha posterior al último día de `feriados.csv`",
        _A,
        "se responde sin información de feriados para ese día",
        "la fecha {fecha} es posterior a la cobertura de feriados ({fin}); "
        "se asume día sin feriado",
    ),
    # --- Lista de familias e ítems --------------------------------------------------------------
    Politica(
        "LISTA_COMO_OBJETO",
        "Lista de familias enviada como objeto `{familia: valor}`",
        _R,
        "se convierte en lista",
        "'{lista}' venía como objeto; se convirtió en lista",
    ),
    Politica(
        "LISTA_DUPLICADA",
        "Lista de familias repetida (o con alias) con contenido distinto",
        _X,
        "422",
        "'{canonica}' aparece {veces} veces con contenido distinto",
    ),
    Politica(
        "SIN_FAMILIAS",
        "Lista de familias ausente, vacía o sin familias reconocibles",
        _X,
        "422",
        "no hay familias válidas en '{lista}'",
    ),
    Politica(
        "ITEM_INVALIDO",
        "Ítem de la lista que no es un objeto",
        _A,
        "se descarta el ítem",
        "el ítem no es un objeto ({tipo}); se descartó",
    ),
    Politica(
        "FAMILIA_FORMATO",
        "Familia escrita de otra forma (mayúsculas, acentos, nombre)",
        _R,
        "se usa el id del catálogo",
        "'{recibido}' se interpretó como '{familia}'",
        "E02 `LÁCTEOS`, `Frutas y Verduras`",
    ),
    Politica(
        "FAMILIA_APROXIMADA",
        "Familia con coincidencia aproximada única (`lacteo`)",
        _A,
        "se usa la familia más cercana",
        "'{recibido}' no coincide exactamente; se asumió '{familia}'",
    ),
    Politica(
        "FAMILIA_DESCONOCIDA",
        "Familia desconocida, nula o ausente en el ítem",
        _A,
        "se descarta el ítem",
        "'{recibido}' no es una familia del catálogo; se descartó",
    ),
    Politica(
        "FAMILIA_REPETIDA_IGUAL",
        "Familia repetida con los mismos valores",
        _R,
        "se conserva una",
        "'{familia}' aparece {veces} veces con el mismo valor",
    ),
    Politica(
        "FAMILIA_REPETIDA_PRED",
        "Predicción: familia repetida con valores distintos",
        _A,
        "se usa la última aparición",
        "'{familia}' aparece {veces} veces con en_promocion distinto ({valores}); "
        "se usó la última aparición ({valor})",
        "E06",
    ),
    Politica(
        "FAMILIA_REPETIDA_OBS",
        "Observación: familia repetida con valores distintos",
        _A,
        "no se guarda esa familia",
        "'{familia}' aparece {veces} veces con ventas distintas ({valores}); no se guardó",
    ),
    Politica(
        "FAMILIAS_FALTANTES_PRED",
        "Predicción: faltan familias del catálogo",
        _A,
        "se predicen todas las del catálogo; las faltantes con `en_promocion = 0`",
        "faltan {n} familias ({familias}); se predijeron con en_promocion=0",
        "E05",
    ),
    Politica(
        "FAMILIAS_FALTANTES_OBS",
        "Observación: faltan familias del catálogo",
        _A,
        "se guardan las recibidas",
        "faltan {n} familias ({familias}); no se registraron",
    ),
    # --- Números --------------------------------------------------------------------------------
    Politica(
        "NUMERO_FORMATO",
        "Entero como texto, decimal entero, separador de miles o coma decimal `,0`",
        _R,
        "se usa el entero",
        "'{recibido}' se interpretó como {valor}",
        'E03 `"3"`, `2.0`; E10 `"1,292"`, `"50,0"`',
    ),
    Politica(
        "PROMOCION_FALTANTE",
        "Falta `en_promocion` o es nulo",
        _A,
        "se usa 0",
        "no se recibió en_promocion; se asumió 0",
    ),
    Politica(
        "PROMOCION_DECIMAL",
        "`en_promocion` con decimales (`2.5`)",
        _A,
        "se redondea al entero más cercano (mitades hacia arriba)",
        "'{recibido}' no es entero; se redondeó a {valor}",
    ),
    Politica(
        "PROMOCION_INVALIDA",
        "`en_promocion` negativo, booleano, no numérico, NaN/Infinity o enorme",
        _A,
        "se usa 0",
        "'{recibido}' no es válido ({motivo}); se asumió 0",
    ),
    Politica(
        "VENTAS_NEGATIVAS",
        "Ventas negativas (devoluciones)",
        _A,
        "no se guarda esa familia",
        "'{recibido}' es negativo; no se guardó",
        "E08 `-4`",
    ),
    Politica(
        "VENTAS_DECIMAL",
        'Ventas con decimales (`"50,5"`)',
        _A,
        "no se guarda esa familia",
        "'{recibido}' no es un entero; no se guardó",
    ),
    Politica(
        "VENTAS_INVALIDAS",
        "Ventas ausentes, nulas, booleanas, no numéricas, NaN/Infinity o enormes",
        _A,
        "no se guarda esa familia",
        "'{recibido}' no es válido ({motivo}); no se guardó",
    ),
    Politica(
        "OBSERVACION_REPETIDA",
        "Reenvío idéntico de una observación",
        _I,
        "upsert idempotente: `registros_guardados = 0`",
        "",
        "E09",
    ),
)

POLITICAS: dict[str, Politica] = {p.codigo: p for p in _FILAS}


def politica(codigo: str) -> Politica:
    """Devuelve la política de un código; falla si el código no está en la tabla."""
    return POLITICAS[codigo]


def tabla_markdown() -> str:
    """Genera la tabla Markdown que va en ``DECISIONES.md §1`` (entre los marcadores)."""
    lineas = [
        "| Código | Problema | Acción | Efecto | Ejemplo |",
        "|---|---|---|---|---|",
    ]
    for p in _FILAS:
        lineas.append(
            f"| `{p.codigo}` | {p.problema} | {p.accion.value} | {p.efecto} | {p.ejemplo} |"
        )
    return "\n".join(lineas)
