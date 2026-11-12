"""Regresiones de la evaluación como docente: entradas fuera del catálogo y bordes operativos.

Cada prueba fija un caso que antes terminaba en 500, en un 503 engañoso o en una política
incoherente con DECISIONES.md.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from novamercado.limpieza import SolicitudRechazada, procesar_observaciones, procesar_prediccion
from novamercado.limpieza.normalizadores import interpretar_fecha, normalizar_tienda
from novamercado.servicio.airflow import AirflowClient, AirflowNoDisponible
from novamercado.servicio.salud import EstadoSalud, resumir
from test_api_esqueleto import TODO_OK, _cliente, _modelo, catalogo_sintetico


def _catalogo():
    """Catálogo sintético con referencias de plausibilidad y fin del histórico."""
    return replace(
        catalogo_sintetico(),
        fecha_fin_historico=date(2025, 6, 30),
        max_ventas={(1, "abarrotes"): 100},
        max_promocion={"abarrotes": 10},
    )


def _obs(fecha: str = "2025-07-02", ventas=50, familia: str = "abarrotes") -> str:
    return json.dumps(
        {"tienda_id": 1, "fecha": fecha, "ventas": [{"familia_id": familia, "ventas": ventas}]}
    )


def _pred(**cambios) -> str:
    cuerpo = {
        "tienda_id": 1,
        "fecha": "2025-07-02",
        "familias": [{"familia_id": "abarrotes", "en_promocion": 0}],
    }
    cuerpo.update(cambios)
    return json.dumps(cuerpo)


def _codigos(limpia) -> set[str]:
    return {h.codigo for h in limpia.hallazgos}


# --- Surrogates UTF-16 sueltos: nunca un 500 ---------------------------------------------------


@pytest.mark.parametrize(
    "cuerpo",
    [
        '{"tienda_id":"\\ud800","fecha":"2025-07-02","familias":[{"familia_id":"abarrotes"}]}',
        '{"tienda_id":1,"fecha":"\\ud800","familias":[{"familia_id":"abarrotes"}]}',
    ],
)
def test_surrogate_en_identidad_es_422_legible(cuerpo):
    with pytest.raises(SolicitudRechazada) as exc:
        procesar_prediccion(cuerpo, _catalogo())
    exc.value.detail.encode("utf-8")  # el detail se puede serializar


def test_surrogate_en_familia_o_clave_se_sanea_y_la_respuesta_serializa():
    cuerpo = (
        '{"tienda_id":1,"fecha":"2025-07-02","\\ud800x":1,'
        '"familias":[{"familia_id":"abarrotes","en_promocion":"\\ud800"},'
        '{"familia_id":"\\udfff","en_promocion":0}]}'
    )
    limpia = procesar_prediccion(cuerpo, _catalogo())
    assert "TEXTO_INVALIDO" in _codigos(limpia)
    limpia.calidad.model_dump_json().encode("utf-8")
    assert all("\ud800" not in c for c in limpia.claves_ignoradas)


def test_api_responde_422_y_no_500_con_surrogate():
    cliente, _ = _cliente(modelo=_modelo(), catalogo=_catalogo)
    cuerpo = '{"tienda_id":"\\ud800","fecha":"2025-07-02","familias":[{"familia_id":"x"}]}'
    with cliente:
        r = cliente.post("/v1/prediccion", content=cuerpo)
    assert r.status_code == 422 and "detail" in r.json()


def test_error_inesperado_es_503_con_detail_nunca_500():
    def catalogo_roto():
        raise RuntimeError("fallo imprevisto")

    cliente, _ = _cliente(modelo=_modelo(), catalogo=catalogo_roto)
    # El `TestClient` relanza las excepciones del servidor; aquí se mira la respuesta enviada.
    sin_relanzar = TestClient(cliente.app, raise_server_exceptions=False)
    with sin_relanzar:
        r = sin_relanzar.post("/v1/prediccion", content=_pred())
    assert r.status_code == 503 and "RuntimeError" in r.json()["detail"]


# --- Fechas en observaciones ---------------------------------------------------------------------


def test_observacion_posterior_a_la_cobertura_se_rechaza():
    with pytest.raises(SolicitudRechazada) as exc:
        procesar_observaciones(_obs(fecha="2099-01-01"), _catalogo())
    assert exc.value.codigo == "FECHA_FUERA_COBERTURA_OBS"
    # La predicción de esa fecha sigue respondiendo con advertencia.
    limpia = procesar_prediccion(_pred(fecha="2099-01-01"), _catalogo())
    assert limpia.calidad.estado == "advertencia"


def test_observacion_con_fecha_ambigua_se_rechaza_y_la_prediccion_advierte():
    with pytest.raises(SolicitudRechazada) as exc:
        procesar_observaciones(_obs(fecha="03/07/2025"), _catalogo())
    assert exc.value.codigo == "FECHA_AMBIGUA_OBS"
    assert "FECHA_AMBIGUA" in _codigos(procesar_prediccion(_pred(fecha="03/07/2025"), _catalogo()))


def test_observacion_dentro_del_historico_queda_registrada():
    limpia = procesar_observaciones(_obs(fecha="2025-06-15"), _catalogo())
    assert "OBSERVACION_EN_HISTORICO" in _codigos(limpia)
    assert limpia.calidad.estado == "advertencia" and len(limpia.solicitud.ventas) == 1
    assert "OBSERVACION_EN_HISTORICO" not in _codigos(procesar_observaciones(_obs(), _catalogo()))


@pytest.mark.parametrize("texto", ["2025-07-02T25:00:00", "2025-07-02T10:61:00", "2025-07-02T99"])
def test_hora_imposible_es_ilegible(texto):
    with pytest.raises(SolicitudRechazada) as exc:
        procesar_prediccion(_pred(fecha=texto), _catalogo())
    assert exc.value.codigo == "FECHA_ILEGIBLE"


@pytest.mark.parametrize(
    ("texto", "tipo"),
    [
        ("2025-07-02T01:00:00+00:00", "zona"),
        ("2025-07-02T18:00:00+02:00", "reparado"),  # 10:00 en Guatemala: mismo día
        ("2025-07-02T03:00:00Z", "zona"),  # 21:00 del día anterior en Guatemala
        ("2025-07-02T00:00:00Z", "reparado"),
        ("2025-07-02T23:00:00-06:00", "reparado"),
        ("2025-07-02T08:30:00", "reparado"),
    ],
)
def test_zona_horaria_advierte_solo_si_en_guatemala_es_otro_dia(texto, tipo):
    assert interpretar_fecha(texto) == (date(2025, 7, 2), tipo)


# --- Números ambiguos y atípicos -----------------------------------------------------------------


def test_ventas_con_punto_ambiguo_no_se_guardan():
    limpia = procesar_observaciones(_obs(ventas="1.000"), _catalogo())
    assert "VENTAS_AMBIGUAS" in _codigos(limpia) and limpia.solicitud.ventas == []


def test_promocion_con_punto_ambiguo_advierte():
    cuerpo = _pred(familias=[{"familia_id": "abarrotes", "en_promocion": "2.000"}])
    limpia = procesar_prediccion(cuerpo, _catalogo())
    assert "PROMOCION_AMBIGUA" in _codigos(limpia)
    assert limpia.solicitud.familias[0].en_promocion == 2


def test_ventas_atipicas_se_advierten_y_no_se_guardan():
    limpia = procesar_observaciones(_obs(ventas=99_999), _catalogo())
    assert "VENTAS_ATIPICAS" in _codigos(limpia) and limpia.solicitud.ventas == []
    assert limpia.calidad.estado == "advertencia"
    # 5 × 100 + 20 = 520 es el límite: hasta ahí se guarda.
    limpia = procesar_observaciones(_obs(ventas=520), _catalogo())
    assert "VENTAS_ATIPICAS" not in _codigos(limpia) and len(limpia.solicitud.ventas) == 1


def test_promocion_atipica_se_acota_al_maximo_historico():
    cuerpo = _pred(familias=[{"familia_id": "abarrotes", "en_promocion": 2_147_483_647}])
    limpia = procesar_prediccion(cuerpo, _catalogo())
    assert "PROMOCION_ATIPICA" in _codigos(limpia)
    assert limpia.solicitud.familias[0].en_promocion == 25  # se acota al límite 2×10+5
    cuerpo = _pred(familias=[{"familia_id": "abarrotes", "en_promocion": 25}])
    assert "PROMOCION_ATIPICA" not in _codigos(procesar_prediccion(cuerpo, _catalogo()))


@pytest.mark.parametrize("valor", ["1e1", "5-0", "1.000", "T5.0"])
def test_tienda_sin_lectura_inequivoca_se_rechaza(valor):
    with pytest.raises(SolicitudRechazada):
        normalizar_tienda(valor, _catalogo())


# --- Estructura tolerante ------------------------------------------------------------------------


def test_un_solo_item_sin_lista_se_trata_como_lista():
    cuerpo = _pred(familias={"familia_id": "abarrotes", "en_promocion": 3})
    limpia = procesar_prediccion(cuerpo, _catalogo())
    assert "LISTA_UN_ITEM" in _codigos(limpia)
    assert {f.familia_id: f.en_promocion for f in limpia.solicitud.familias}["abarrotes"] == 3


def test_item_como_texto_json_se_decodifica():
    cuerpo = _pred(familias=[json.dumps({"familia_id": "abarrotes", "en_promocion": 4})])
    limpia = procesar_prediccion(cuerpo, _catalogo())
    assert "ITEM_COMO_TEXTO" in _codigos(limpia)
    assert {f.familia_id: f.en_promocion for f in limpia.solicitud.familias}["abarrotes"] == 4


def test_mensaje_de_duplicados_se_resume():
    familias = [{"familia_id": "abarrotes", "en_promocion": i} for i in range(200)]
    limpia = procesar_prediccion(_pred(familias=familias), _catalogo())
    detalles = [o.detalle for o in limpia.calidad.observaciones]
    assert max(len(d) for d in detalles) < 400 and any("200 en total" in d for d in detalles)


# --- Operación: Airflow, salud, reentreno ---------------------------------------------------------


def _airflow_que_responde(manejar) -> AirflowClient:
    return AirflowClient("http://airflow", "u", "p", transporte=httpx.MockTransport(manejar))


def test_cliente_airflow_con_respuesta_no_json_no_lanza_excepciones_crudas():
    cliente = _airflow_que_responde(lambda r: httpx.Response(200, text="<html>arrancando"))
    assert cliente.salud() is False
    with pytest.raises(AirflowNoDisponible):
        cliente.dag_activo("reentrenamiento")


def test_reentrenar_con_scheduler_caido_es_503():
    def manejar(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/monitor/health":
            return httpx.Response(
                200,
                json={"metadatabase": {"status": "healthy"}, "scheduler": {"status": "unhealthy"}},
            )
        if request.url.path == "/auth/token":
            return httpx.Response(201, json={"access_token": "t"})
        return httpx.Response(200, json={"is_paused": False})

    from novamercado.servicio import reentreno

    with pytest.raises(reentreno.NoSePuedeIniciar, match="scheduler"):
        reentreno.iniciar(
            _ConexionConBloqueo(),
            _airflow_que_responde(manejar),
            "reentrenamiento",
            mlflow_disponible=True,
            version_vigente="1",
        )


class _ConexionConBloqueo:
    """Lo mínimo que `iniciar` usa de la conexión antes de comprobar Airflow."""

    def execute(self, *_a, **_k):
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def commit(self):
        return None

    def transaction(self):
        from contextlib import nullcontext

        return nullcontext()


def test_id_vacio_responde_404():
    cliente, _ = _cliente(modelo=_modelo(), salud=TODO_OK)
    with cliente:
        r = cliente.get("/admin/reentrenar/", follow_redirects=False)
    assert r.status_code == 404 and "detail" in r.json()


def test_health_degradado_si_la_version_del_alias_no_se_pudo_instalar():
    codigo, cuerpo = resumir(EstadoSalud(TODO_OK), True, version_pendiente="5")
    assert codigo == 200 and cuerpo["estado"] == "degradado"
    assert cuerpo["componentes"]["modelo_al_dia"] is False
    assert resumir(EstadoSalud(TODO_OK), True)[1]["estado"] == "ok"


def test_gestor_marca_y_limpia_la_version_pendiente(tmp_path):
    from novamercado.servicio.cache import CacheModelos
    from novamercado.servicio.cargador import GestorModelo

    class RegistroFalso:
        version = "2"

        def version_con_alias(self, nombre, alias):
            return self.version

        def descargar(self, nombre, version, destino):
            if version == "2":
                raise RuntimeError("artefacto corrupto")
            destino.mkdir(parents=True, exist_ok=True)
            (destino / "MLmodel").write_text("x")
            return destino

    registro = RegistroFalso()
    gestor = GestorModelo(
        registro, CacheModelos(tmp_path), nombre="m", alias="champion", cargador=lambda r: object()
    )
    gestor.cache.preparar()
    assert gestor.sincronizar() is False and gestor.version_pendiente == "2"
    registro.version = "3"
    assert gestor.sincronizar() is True and gestor.version_pendiente is None


def test_modelo_que_no_devuelve_una_familia_es_error_no_cero():
    from novamercado.contrato import SolicitudPrediccion
    from novamercado.servicio import prediccion as mod

    class Incompleto:
        def predict(self, entrada):
            return pd.DataFrame(
                {"familia_id": ["abarrotes"], "venta_esperada": [1.0], "cantidad_sugerida": [1]}
            )

    solicitud = SolicitudPrediccion(
        tienda_id=1,
        fecha=date(2025, 7, 2),
        familias=[
            {"familia_id": "abarrotes", "en_promocion": 0},
            {"familia_id": "bebidas", "en_promocion": 0},
        ],
    )
    modelo = replace(_modelo(), modelo=Incompleto())
    original = mod.leer_panel
    mod.leer_panel = lambda *a, **k: pd.DataFrame(
        columns=["tienda_id", "fecha", "familia_id", "ventas", "en_promocion"]
    )
    try:
        with pytest.raises(mod.ErrorModelo, match="bebidas"):
            mod.predecir(None, modelo, solicitud)
    finally:
        mod.leer_panel = original


def test_baseline_guarda_su_corte():
    from novamercado.modelo.baseline import ajustar_baseline

    fechas = pd.date_range("2025-01-01", periods=70, freq="D")
    historia = pd.DataFrame(
        {
            "tienda_id": 1,
            "fecha": fechas,
            "familia_id": "abarrotes",
            "ventas": 10.0,
            "en_promocion": 0.0,
        }
    )
    modelo = ajustar_baseline(historia, {"abarrotes": (1.0, 1.0)})
    assert modelo.fecha_corte == fechas[-1] + pd.Timedelta(days=1)


# --- Segunda ronda -------------------------------------------------------------------------------


def test_observacion_dentro_del_historico_no_cambia_la_calidad():
    """El ejemplo del Anexo (2025-11-17, dentro del histórico) debe seguir respondiendo `ok`."""
    datos = {
        "tienda_id": 1,
        "fecha": "2025-06-15",
        "ventas": [{"familia_id": f, "ventas": 10} for f in _catalogo().familias],
    }
    limpia = procesar_observaciones(json.dumps(datos), _catalogo())
    assert limpia.calidad.estado == "ok" and limpia.calidad.observaciones == []
    assert "OBSERVACION_EN_HISTORICO" in _codigos(limpia)  # queda en `correcciones` (BD)


def test_clave_nombre_descriptiva_no_reemplaza_a_familia_id():
    cuerpo = _obs()
    datos = json.loads(cuerpo)
    datos["ventas"][0]["nombre"] = "Abarrotes secos"
    limpia = procesar_observaciones(json.dumps(datos), _catalogo())
    assert [v.familia_id for v in limpia.solicitud.ventas] == ["abarrotes"]


def test_copia_nula_de_una_clave_no_tapa_el_valor():
    cuerpo = (
        '{"tienda_id":null,"tienda":1,"fecha":"2025-07-02","familias":'
        '[{"familia_id":"abarrotes","en_promocion":null,"promocion":4}]}'
    )
    limpia = procesar_prediccion(cuerpo, _catalogo())
    assert limpia.solicitud.tienda_id == 1
    assert {f.familia_id: f.en_promocion for f in limpia.solicitud.familias}["abarrotes"] == 4
    assert "PROMOCION_FALTANTE" not in _codigos(limpia)


def test_faltante_nombra_las_claves_no_reconocidas():
    cuerpo = '{"tienda_codigoX":1,"fecha":"2025-07-02","familias":[{"familia_id":"abarrotes"}]}'
    with pytest.raises(SolicitudRechazada) as exc:
        procesar_prediccion(cuerpo, _catalogo())
    assert "tienda_codigoX" in exc.value.detail


def test_lista_enorme_se_rechaza():
    familias = [{"familia_id": f"x{i}", "en_promocion": 0} for i in range(201)]
    with pytest.raises(SolicitudRechazada) as exc:
        procesar_prediccion(_pred(familias=familias), _catalogo())
    assert exc.value.codigo == "LISTA_GRANDE"


def test_familia_aproximada_no_se_guarda_en_observaciones_y_exacta_gana_en_prediccion():
    datos = json.loads(_obs(familia="abarrote"))
    datos["ventas"].append({"familia_id": "lacteos", "ventas": 5})
    limpia = procesar_observaciones(json.dumps(datos), _catalogo())
    assert "FAMILIA_APROXIMADA_OBS" in _codigos(limpia)
    assert [v.familia_id for v in limpia.solicitud.ventas] == ["lacteos"]
    familias = [
        {"familia_id": "abarrotes", "en_promocion": 2},
        {"familia_id": "abarrote", "en_promocion": 9},
    ]
    limpia = procesar_prediccion(_pred(familias=familias), _catalogo())
    assert {f.familia_id: f.en_promocion for f in limpia.solicitud.familias}["abarrotes"] == 2


def test_zona_horaria_de_otro_dia_se_rechaza_en_observaciones():
    with pytest.raises(SolicitudRechazada) as exc:
        procesar_observaciones(_obs(fecha="2025-07-02T03:00:00Z"), _catalogo())
    assert exc.value.codigo == "FECHA_ZONA_OBS"
    limpia = procesar_observaciones(_obs(fecha="2025-07-02T12:00:00Z"), _catalogo())
    assert "FECHA_ZONA" not in _codigos(limpia)


def test_coma_con_tres_decimales_en_promocion_es_ambigua():
    cuerpo = _pred(familias=[{"familia_id": "abarrotes", "en_promocion": "2,500"}])
    limpia = procesar_prediccion(cuerpo, _catalogo())
    assert "PROMOCION_AMBIGUA" in _codigos(limpia)
    assert {f.familia_id: f.en_promocion for f in limpia.solicitud.familias}["abarrotes"] == 3
    # En ventas, "1,292" sigue siendo miles (E10).
    assert procesar_observaciones(_obs(ventas="1,292"), replace(_catalogo(), max_ventas={}))


def test_item_que_es_solo_la_familia_y_ventas_sin_clave_reconocida():
    limpia = procesar_prediccion(_pred(familias=["abarrotes"]), _catalogo())
    assert "ITEM_SOLO_FAMILIA" in _codigos(limpia)
    cuerpo = json.dumps(
        {"tienda_id": 1, "fecha": "2025-07-02", "ventas": [{"familia_id": "abarrotes", "monto": 5}]}
    )
    limpia = procesar_observaciones(cuerpo, _catalogo())
    detalles = " ".join(o.detalle for o in limpia.calidad.observaciones)
    assert "no llegó la clave 'ventas'" in detalles and "monto" in detalles


@pytest.mark.parametrize("valor", ["T2.0", "Tienda 2.00", "2,0", "T02"])
def test_sufijo_decimal_nulo_en_tienda(valor):
    assert normalizar_tienda(valor, _catalogo())[0] == 2


def test_post_reentrenar_con_barra_final_no_es_405():
    cliente, _ = _cliente(modelo=_modelo(), salud=TODO_OK)
    with cliente:
        r = cliente.post("/admin/reentrenar/", follow_redirects=False)
    assert r.status_code in (202, 503)


def test_espacio_de_ancho_cero_y_guion_unicode():
    assert interpretar_fecha("\u200b2025-07-02")[0] == date(2025, 7, 2)
    limpia = procesar_observaciones(_obs(ventas="\u22124"), _catalogo())
    assert "VENTAS_NEGATIVAS" in _codigos(limpia)  # "−4" es negativo, no texto ilegible


def test_ventanas_solo_con_dias_completos():
    from novamercado.modelo.validacion import ventanas_evaluacion

    dias = list(pd.date_range("2026-06-01", periods=21, freq="D"))
    filas = [d for d in dias[:14] for _ in range(10)] + [d for d in dias[14:] for _ in range(1)]
    v = ventanas_evaluacion(filas, dias[0])
    assert [len(x) for x in v] == [7, 7] and v[-1][-1] == dias[13]  # los días de 1 fila no cuentan


def test_huella_de_entrenamiento_cambia_con_una_observacion_y_no_con_el_orden():
    from novamercado.modelo.ciclo import huella_entrenamiento

    h = pd.DataFrame(
        {
            "tienda_id": [1, 1],
            "fecha": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "familia_id": ["a", "a"],
            "ventas": [1.0, 2.0],
            "en_promocion": [0.0, float("nan")],
        }
    )
    assert huella_entrenamiento(h) == huella_entrenamiento(h.iloc[::-1])
    assert huella_entrenamiento(h) != huella_entrenamiento(h.assign(ventas=[1.0, 3.0]))
