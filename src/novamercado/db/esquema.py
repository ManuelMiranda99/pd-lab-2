"""Esquema de la BD del producto como lista ordenada de migraciones idempotentes.

Cada migración se aplica una sola vez y queda registrada en `migraciones`. Para cambiar el esquema
se agrega una migración nueva al final (nunca se edita una ya aplicada).
"""

from __future__ import annotations

import logging

import psycopg

log = logging.getLogger(__name__)

# Clave del bloqueo transaccional que evita que dos procesos migren a la vez.
_CLAVE_BLOQUEO_MIGRACION = 72_610_002

MIGRACIONES: list[tuple[int, str, str]] = [
    (
        1,
        "catálogos, histórico, observaciones, marcador de carga y reentrenamientos",
        """
        CREATE TABLE tiendas (
            tienda_id      INTEGER PRIMARY KEY,
            nombre         TEXT NOT NULL,
            ciudad         TEXT NOT NULL,
            departamento   TEXT NOT NULL,
            tipo           TEXT NOT NULL,
            fecha_apertura DATE NOT NULL
        );
        CREATE TABLE familias (
            familia_id TEXT PRIMARY KEY,
            nombre     TEXT NOT NULL
        );
        CREATE TABLE costos (
            familia_id     TEXT PRIMARY KEY REFERENCES familias (familia_id),
            costo_faltante DOUBLE PRECISION NOT NULL,
            costo_sobrante DOUBLE PRECISION NOT NULL
        );
        CREATE TABLE feriados (
            id     BIGSERIAL PRIMARY KEY,
            fecha  DATE NOT NULL,
            nombre TEXT NOT NULL,
            tipo   TEXT NOT NULL,
            ambito TEXT NOT NULL
        );
        CREATE INDEX feriados_fecha ON feriados (fecha);
        CREATE TABLE historico_ventas (
            tienda_id    INTEGER NOT NULL REFERENCES tiendas (tienda_id),
            fecha        DATE NOT NULL,
            familia_id   TEXT NOT NULL REFERENCES familias (familia_id),
            ventas       INTEGER NOT NULL,
            en_promocion INTEGER NOT NULL,
            PRIMARY KEY (tienda_id, fecha, familia_id)
        );
        CREATE TABLE observaciones (
            tienda_id      INTEGER NOT NULL REFERENCES tiendas (tienda_id),
            fecha          DATE NOT NULL,
            familia_id     TEXT NOT NULL REFERENCES familias (familia_id),
            ventas         INTEGER NOT NULL,
            actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tienda_id, fecha, familia_id)
        );
        CREATE TABLE carga_datos (
            id         SMALLINT PRIMARY KEY CHECK (id = 1),
            sha256     TEXT NOT NULL,
            filas      JSONB NOT NULL,
            cargado_en TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE reentrenamientos (
            id             TEXT PRIMARY KEY,
            estado         TEXT NOT NULL CHECK (estado IN ('en_curso', 'exitoso', 'fallido')),
            promovido      BOOLEAN,
            version        TEXT,
            motivo         TEXT,
            detalle        JSONB,
            creado_en      TIMESTAMPTZ NOT NULL DEFAULT now(),
            actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """,
    ),
    (
        2,
        "solicitudes, predicciones, correcciones e historial de observaciones",
        """
        -- Toda solicitud recibida, aceptada o rechazada (422). El id secuencial ordena las
        -- solicitudes de un mismo día sin usar el reloj (DECISIONES §6.3).
        CREATE TABLE solicitudes (
            id               BIGSERIAL PRIMARY KEY,
            tipo             TEXT NOT NULL CHECK (tipo IN ('prediccion', 'observaciones')),
            estado           TEXT NOT NULL CHECK (estado IN ('aceptada', 'rechazada')),
            cuerpo_crudo     TEXT NOT NULL,
            normalizado      JSONB,
            tienda_id        INTEGER,
            fecha            DATE,
            calidad_estado   TEXT CHECK (calidad_estado IN ('ok', 'corregido', 'advertencia')),
            codigo_rechazo   TEXT,
            detail           TEXT,
            claves_ignoradas JSONB NOT NULL DEFAULT '[]',
            version_modelo   TEXT,
            recibida_en      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((estado = 'rechazada') = (detail IS NOT NULL))
        );
        CREATE INDEX solicitudes_tienda_fecha ON solicitudes (tienda_id, fecha);

        -- Una fila por familia predicha. Sin UNIQUE por tienda/fecha: el contrato admite varias
        -- solicitudes de la misma fecha y todas quedan registradas.
        CREATE TABLE predicciones (
            id                  BIGSERIAL PRIMARY KEY,
            solicitud_id        BIGINT NOT NULL REFERENCES solicitudes (id),
            tienda_id           INTEGER NOT NULL REFERENCES tiendas (tienda_id),
            fecha               DATE NOT NULL,
            familia_id          TEXT NOT NULL REFERENCES familias (familia_id),
            en_promocion        INTEGER NOT NULL CHECK (en_promocion >= 0),
            promocion_explicita BOOLEAN NOT NULL,
            venta_esperada      DOUBLE PRECISION NOT NULL CHECK (venta_esperada >= 0),
            cantidad_sugerida   INTEGER NOT NULL CHECK (cantidad_sugerida >= 0),
            version_modelo      TEXT NOT NULL
        );
        CREATE INDEX predicciones_tienda_fecha ON predicciones (tienda_id, fecha);
        CREATE INDEX predicciones_solicitud ON predicciones (solicitud_id);

        -- Cada hallazgo de la limpieza (reparar, advertir o ignorar), de predicción y de
        -- observaciones.
        CREATE TABLE correcciones (
            id           BIGSERIAL PRIMARY KEY,
            solicitud_id BIGINT NOT NULL REFERENCES solicitudes (id),
            codigo       TEXT NOT NULL,
            campo        TEXT NOT NULL,
            accion       TEXT NOT NULL CHECK (accion IN ('reparar', 'advertir', 'ignorar')),
            detalle      TEXT NOT NULL
        );
        CREATE INDEX correcciones_solicitud ON correcciones (solicitud_id);

        -- Altas y cambios de observaciones (un reenvío idéntico no deja fila).
        CREATE TABLE observaciones_historial (
            id              BIGSERIAL PRIMARY KEY,
            solicitud_id    BIGINT NOT NULL REFERENCES solicitudes (id),
            tienda_id       INTEGER NOT NULL,
            fecha           DATE NOT NULL,
            familia_id      TEXT NOT NULL,
            ventas_anterior INTEGER,
            ventas_nueva    INTEGER NOT NULL
        );
        CREATE INDEX observaciones_historial_tienda_fecha
            ON observaciones_historial (tienda_id, fecha);

        ALTER TABLE observaciones ADD COLUMN solicitud_id BIGINT REFERENCES solicitudes (id);
        ALTER TABLE observaciones
            ADD CONSTRAINT observaciones_ventas_no_negativas CHECK (ventas >= 0);
        """,
    ),
]


def migrar(conn: psycopg.Connection) -> list[int]:
    """Aplica las migraciones pendientes en una transacción. Devuelve las versiones aplicadas."""
    aplicadas: list[int] = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_CLAVE_BLOQUEO_MIGRACION,))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS migraciones (
                version     INTEGER PRIMARY KEY,
                descripcion TEXT NOT NULL,
                aplicada_en TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        hechas = {fila[0] for fila in conn.execute("SELECT version FROM migraciones")}
        for version, descripcion, sql in MIGRACIONES:
            if version in hechas:
                continue
            log.info("Aplicando migración %s: %s", version, descripcion)
            conn.execute(sql)
            conn.execute(
                "INSERT INTO migraciones (version, descripcion) VALUES (%s, %s)",
                (version, descripcion),
            )
            aplicadas.append(version)
    return aplicadas
