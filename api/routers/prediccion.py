"""`POST /v1/prediccion` mínimo (esqueleto). La limpieza completa llega en las Fases 3–4."""

from __future__ import annotations

import psycopg
from fastapi import APIRouter, HTTPException

from api.dependencias import ServicioDep, abrir_conexion
from api.esquemas_provisionales import (
    Calidad,
    PrediccionFamilia,
    RespuestaPrediccion,
    SolicitudPrediccion,
)
from novamercado.db import repos
from novamercado.servicio.prediccion import predecir

router = APIRouter(tags=["prediccion"])


@router.post("/v1/prediccion", response_model=RespuestaPrediccion)
def prediccion(solicitud: SolicitudPrediccion, servicio: ServicioDep) -> RespuestaPrediccion:
    modelo = servicio.gestor.actual  # una sola referencia para toda la solicitud
    if modelo is None:
        raise HTTPException(status_code=503, detail="Sin modelo cargado todavía")
    with abrir_conexion(servicio) as conn:
        try:
            if not repos.tienda_existe(conn, solicitud.tienda_id):
                raise HTTPException(
                    status_code=422,
                    detail=f"tienda_id {solicitud.tienda_id} no existe en el catálogo",
                )
            catalogo = set(repos.familias_catalogo(conn))
            desconocidas = [
                f.familia_id for f in solicitud.familias if f.familia_id not in catalogo
            ]
            if desconocidas:
                raise HTTPException(
                    status_code=422, detail=f"familia_id desconocida: {', '.join(desconocidas)}"
                )
            resultado = predecir(
                conn,
                modelo,
                solicitud.tienda_id,
                solicitud.fecha,
                [(f.familia_id, f.en_promocion) for f in solicitud.familias],
            )
        except psycopg.Error as exc:
            raise HTTPException(status_code=503, detail="Base de datos no disponible") from exc
    return RespuestaPrediccion(
        tienda_id=solicitud.tienda_id,
        fecha=solicitud.fecha.isoformat(),
        version_modelo=modelo.version,
        predicciones=[
            PrediccionFamilia(
                familia_id=p.familia_id,
                venta_esperada=p.venta_esperada,
                cantidad_sugerida=p.cantidad_sugerida,
            )
            for p in resultado
        ],
        calidad=Calidad(estado="ok", observaciones=[]),
    )
