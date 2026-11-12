"""Genera la evidencia de funcionamiento que pide el enunciado, contra un stack de prueba.

1. **API**: respuestas a solicitudes válidas y defectuosas (catálogo E01–E10 y problemas que no
   están en el catálogo), cada una validada contra el contrato del Anexo.
2. **Reentrenamiento**: una ejecución que **no promueve** (sin datos nuevos) y otra que **sí
   promueve** (tras 14 días de observaciones con un choque de demanda de +25 %), con el tiempo
   hasta que la API usa la versión nueva.
3. **Resiliencia**: `stop mlflow` → `/health` degradado, la predicción sigue en 200 y reentrenar
   responde 503; `start mlflow` → `ok`.
4. **MLflow**: experimentos, runs (métricas y linaje) y versiones del modelo con su alias.
5. **Airflow**: ejecuciones del DAG `reentrenamiento` con el estado de cada tarea.

Las solicitudes se construyen aquí (no se copian los ejemplos publicados) y las observaciones
son sintéticas: la predicción del propio servicio × 1.25. **Escribe en la BD**: se corre en un
proyecto de Compose aparte con volúmenes nuevos, nunca contra el stack que se entrega.

Uso (con el stack principal detenido, porque comparten puertos):
    PYTHONPATH=src python scripts/evidencia.py --levantar --proyecto novamercado-evidencia

Deja `evidencia/api/`, `evidencia/reentrenamiento/`, `evidencia/mlflow/`, `evidencia/airflow/`
y el resumen `evidencia/evidencia.md`. No usa el reloj del sistema para decidir sobre fechas.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from comun_datos import cargar, dir_datos
from comun_e2e import RAIZ, Cliente, Compose, esperar

FECHA = date(2026, 3, 1)  # primer día posterior al histórico
TIENDA = 5
DIAS_CHOQUE = 14
FACTOR_CHOQUE = 1.25
PROMOS = {"bebidas": 8, "snacks": 9, "lacteos": 3}


def j(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False)


class Evidencia:
    def __init__(self, a: argparse.Namespace) -> None:
        self.a = a
        self.c = Cliente(a.url)
        self.compose = Compose(a.proyecto, {"DATOS_DIR": str(a.datos_dir), "HISTORICO_HASTA": ""})
        d = cargar(a.datos_dir)
        self.familias = sorted(d.familias["familia_id"])
        self.nombres = dict(zip(d.familias["familia_id"], d.familias["nombre"], strict=True))
        self.tiendas = sorted(int(t) for t in d.tiendas["tienda_id"])
        self.salida = Path(a.salida)
        self.casos: list[dict] = []
        self.reentrenos: list[dict] = []
        self.resiliencia: list[dict] = []
        self.arranque: dict = {}

    # --- solicitudes ---

    def limpia(self, tienda: int = TIENDA, fecha: date = FECHA) -> dict:
        return {
            "tienda_id": tienda,
            "fecha": fecha.isoformat(),
            "familias": [
                {"familia_id": f, "en_promocion": PROMOS.get(f, 0)} for f in self.familias
            ],
        }

    def obs_desde(self, pred: dict, factor: float = FACTOR_CHOQUE) -> dict:
        return {
            "tienda_id": pred["tienda_id"],
            "fecha": pred["fecha"],
            "ventas": [
                {"familia_id": p["familia_id"], "ventas": round(p["venta_esperada"] * factor)}
                for p in pred["predicciones"]
            ],
        }

    def caso(
        self,
        endpoint: str,
        codigo: str,
        nombre: str,
        esperado: str,
        cuerpo: Any,
        crudo: bytes | None = None,
        cabeceras: dict | None = None,
    ) -> dict:
        if endpoint == "prediccion":
            r = (
                self.c.prediccion(cuerpo, crudo=crudo, headers=cabeceras or {})
                if crudo is not None
                else self.c.prediccion(cuerpo)
            )
        else:
            r = (
                self.c.observaciones(cuerpo, crudo=crudo, headers=cabeceras or {})
                if crudo is not None
                else self.c.observaciones(cuerpo)
            )
        cuerpo_r = r.cuerpo if isinstance(r.cuerpo, dict) else {}
        obtenido = (
            cuerpo_r.get("calidad", {}).get("estado", "?") if r.codigo == 200 else str(r.codigo)
        )
        reg = {
            "endpoint": f"POST /v1/{endpoint}",
            "codigo": codigo,
            "caso": nombre,
            "esperado": esperado,
            "obtenido": obtenido,
            "coincide": obtenido == esperado,
            "http": r.codigo,
            "contrato_ok": not r.problemas,
            "solicitud": crudo.decode("latin-1") if crudo is not None else cuerpo,
            "respuesta": r.cuerpo,
        }
        self.casos.append(reg)
        marca = "OK" if reg["coincide"] and reg["contrato_ok"] else "FALLA"
        print(f"[{marca}] {codigo:<5} {nombre}: {obtenido} (esperado {esperado})")
        return r.cuerpo if isinstance(r.cuerpo, dict) else {}

    # --- 1. API --------------------------------------------------------------------------------

    def api(self) -> dict:
        L = self.limpia()

        def con(**cambios: Any) -> dict:
            s = copy.deepcopy(L)
            s.update(cambios)
            return s

        def fam(i: int, **cambios: Any) -> dict:
            s = copy.deepcopy(L)
            s["familias"][i].update(cambios)
            return s

        i_lac = self.familias.index("lacteos")
        i_fv = self.familias.index("frutas_verduras")
        i_beb = self.familias.index("bebidas")
        P = "prediccion"
        valida = self.caso(P, "—", "solicitud válida", "ok", L)
        self.caso(P, "E01", "tienda_id 'T05'", "corregido", con(tienda_id="T05"))
        self.caso(P, "E01", "tienda_id '5'", "corregido", con(tienda_id="5"))
        s = fam(i_lac, familia_id="LÁCTEOS")
        s["familias"][i_fv]["familia_id"] = "Frutas y Verduras"
        self.caso(P, "E02", "'LÁCTEOS' y 'Frutas y Verduras'", "corregido", s)
        self.caso(
            P, "E02", "'lacteos ' con espacio", "corregido", fam(i_lac, familia_id="lacteos ")
        )
        s = fam(i_beb, en_promocion="8")
        s["familias"][self.familias.index("snacks")]["en_promocion"] = 9.0
        self.caso(P, "E03", "promoción '8' y 9.0", "corregido", s)
        self.caso(
            P, "E03", "promoción 2.5 (con pérdida)", "advertencia", fam(i_beb, en_promocion=2.5)
        )
        self.caso(
            P, "E04", "fecha '2026-03-01T00:00:00'", "corregido", con(fecha="2026-03-01T00:00:00")
        )
        self.caso(P, "E04", "fecha '15/03/2026'", "corregido", con(fecha="15/03/2026"))
        self.caso(
            P, "E04", "fecha ambigua '05/03/2026' (DD/MM)", "advertencia", con(fecha="05/03/2026")
        )
        s = copy.deepcopy(L)
        s["familias"] = [f for f in s["familias"] if f["familia_id"] not in ("bebe", "mascotas")]
        self.caso(P, "E05", "faltan 'bebe' y 'mascotas'", "advertencia", s)
        s = copy.deepcopy(L)
        s["familias"].append({"familia_id": "abarrotes", "en_promocion": 3})
        self.caso(P, "E06", "'abarrotes' repetida con promoción distinta", "advertencia", s)
        s = {"tienda": TIENDA, **{k: v for k, v in L.items() if k != "tienda_id"}}
        self.caso(P, "E07", "'tienda' en vez de 'tienda_id'", "corregido", s)
        # No catalogados (reglas generales R1–R9 de DECISIONES §1.2)
        s = {"TiendaId": TIENDA, "Fecha": L["fecha"], "Familias": L["familias"]}
        self.caso(P, "nc", "claves en CamelCase", "corregido", s)
        self.caso(P, "nc", "fecha entera 20260301", "corregido", con(fecha=20260301))
        self.caso(P, "nc", "claves desconocidas (se ignoran)", "ok", con(origen="pos-v2", lote=17))
        s = copy.deepcopy(L)
        s["familias"].append({"familia_id": "juguetes", "en_promocion": 1})
        self.caso(P, "nc", "familia desconocida 'juguetes'", "advertencia", s)
        self.caso(P, "nc", "promoción negativa -3", "advertencia", fam(i_beb, en_promocion=-3))
        self.caso(P, "nc", "promoción 'muchas'", "advertencia", fam(i_beb, en_promocion="muchas"))
        self.caso(P, "nc", "promoción null", "advertencia", fam(i_beb, en_promocion=None))
        self.caso(
            P,
            "nc",
            "fecha fuera de la cobertura de feriados",
            "advertencia",
            con(fecha="2027-02-15"),
        )
        self.caso(P, "nc", "tienda inexistente 99", "422", con(tienda_id=99))
        self.caso(P, "nc", "fecha imposible '2026-02-30'", "422", con(fecha="2026-02-30"))
        self.caso(P, "nc", "fecha ilegible 'mañana'", "422", con(fecha="mañana"))
        self.caso(
            P, "nc", "tienda 10 antes de su apertura", "422", con(tienda_id=10, fecha="2025-01-10")
        )
        self.caso(P, "nc", "lista de familias vacía", "422", con(familias=[]))
        self.caso(
            P,
            "nc",
            "JSON roto",
            "422",
            None,
            crudo=b'{"tienda_id": 5, "fecha": ',
            cabeceras={"content-type": "application/json"},
        )
        self.caso(
            P,
            "nc",
            "cuerpo que no es objeto",
            "422",
            None,
            crudo=b"[1, 2, 3]",
            cabeceras={"content-type": "application/json"},
        )

        OBS = "observaciones"
        obs = self.obs_desde(valida)
        self.caso(OBS, "—", "observación válida (12 familias nuevas)", "ok", obs)
        self.caso(OBS, "E09", "reenvío idéntico (registros_guardados = 0)", "ok", obs)
        s = copy.deepcopy(obs)
        s["tienda_id"] = "T05"
        s["ventas"][0]["ventas"] = s["ventas"][0]["ventas"] + 7
        self.caso(OBS, "E01", "tienda 'T05' corrigiendo un valor (1 modificado)", "corregido", s)
        s2 = copy.deepcopy(s)
        s2["tienda_id"] = TIENDA
        s2["ventas"][1]["ventas"] = -4
        self.caso(OBS, "E08", "ventas negativas (no se guarda esa familia)", "advertencia", s2)
        s3 = copy.deepcopy(s2)
        s3["ventas"][1]["ventas"] = obs["ventas"][1]["ventas"]
        s3["ventas"][i_beb]["ventas"] = f"{s3['ventas'][i_beb]['ventas']:,}"
        i_car = self.familias.index("carnes")
        s3["ventas"][i_car]["ventas"] = f"{s3['ventas'][i_car]['ventas']},0"
        self.caso(OBS, "E10", "'1,234' y '812,0'", "corregido", s3)
        s4 = copy.deepcopy(s3)
        s4["ventas"][3]["ventas"] = "50,5"
        self.caso(OBS, "E10", "'50,5' (decimal con pérdida: no se guarda)", "advertencia", s4)
        s5 = self.obs_desde(valida)
        s5["fecha"] = "2026-03-02"
        s5["ventas"].append({"familia_id": "juguetes", "ventas": 3})
        self.caso(OBS, "nc", "familia desconocida en observaciones", "advertencia", s5)
        self.caso(OBS, "nc", "observación de tienda inexistente", "422", {**obs, "tienda_id": 42})
        return valida

    # --- 2. reentrenamiento ---

    def reentrenar(self, etiqueta: str) -> dict:
        antes = self.c.modelo().cuerpo
        a = self.c.reentrenar()
        id_ = a.cuerpo.get("id") if isinstance(a.cuerpo, dict) else None
        print(f"Reentreno '{etiqueta}': {a.codigo} id={id_}")
        reg: dict[str, Any] = {
            "etiqueta": etiqueta,
            "post": {"http": a.codigo, "respuesta": a.cuerpo},
            "modelo_antes": antes,
        }
        if id_:
            seg, fin = self.c.esperar_reentreno(id_, 600)
            reg.update(segundos=round(seg, 1), estado=fin.cuerpo, contrato_ok=not fin.problemas)
            print(f"  terminó en {seg:.0f} s: {j(fin.cuerpo)}")
            if isinstance(fin.cuerpo, dict) and fin.cuerpo.get("promovido"):
                v = fin.cuerpo.get("version")
                t = esperar(lambda: self.c.modelo().cuerpo.get("version") == v, 120, 1)
                reg["segundos_hasta_en_uso"] = None if t is None else round(t, 1)
                print(f"  versión {v} en uso a los {t} s")
        reg["modelo_despues"] = self.c.modelo().cuerpo
        self.reentrenos.append(reg)
        return reg

    def choque(self) -> None:
        """14 días: predicción de las 10 tiendas y observación con +25 % sobre lo predicho."""
        for k in range(DIAS_CHOQUE):
            d = FECHA + timedelta(days=k)
            for t in self.tiendas:
                p = self.c.prediccion(self.limpia(t, d))
                if p.codigo == 200:
                    self.c.observaciones(self.obs_desde(p.cuerpo))
        print(f"Choque: {DIAS_CHOQUE} días × {len(self.tiendas)} tiendas con ×{FACTOR_CHOQUE}")

    # --- 3. resiliencia ---

    def caida_mlflow(self) -> None:
        def paso(nombre: str, r: Any, **extra: Any) -> None:
            self.resiliencia.append(
                {"paso": nombre, "http": r.codigo, "respuesta": r.cuerpo, **extra}
            )
            print(f"  {nombre}: {r.codigo} {j(r.cuerpo)[:160]}")

        self.compose("stop", "mlflow")
        try:
            t = self.c.esperar_health("degradado", 60, 1)
            paso("GET /health tras stop mlflow", self.c.health(), segundos=t)
            paso("POST /v1/prediccion con MLflow caído", self.c.prediccion(self.limpia()))
            paso("GET /modelo con MLflow caído", self.c.modelo())
            paso("POST /admin/reentrenar con MLflow caído", self.c.reentrenar())
        finally:
            self.compose("start", "mlflow")
        t = self.c.esperar_health("ok", 120, 1)
        paso("GET /health tras start mlflow", self.c.health(), segundos=t)

    # --- 4 y 5. MLflow y Airflow ---

    def mlflow(self) -> dict:
        m = httpx.Client(base_url=self.a.mlflow_url, timeout=20)
        exp = m.post("/api/2.0/mlflow/experiments/search", json={"max_results": 50}).json()
        ids = [e["experiment_id"] for e in exp.get("experiments", [])]
        runs = (
            m.post(
                "/api/2.0/mlflow/runs/search",
                json={
                    "experiment_ids": ids,
                    "max_results": 200,
                    "order_by": ["attributes.start_time ASC"],
                },
            )
            .json()
            .get("runs", [])
        )
        modelo = (
            m.get("/api/2.0/mlflow/registered-models/get", params={"name": "novamercado"})
            .json()
            .get("registered_model", {})
        )
        versiones = m.get(
            "/api/2.0/mlflow/model-versions/search",
            params={"filter": "name='novamercado'", "max_results": 200},
        ).json()
        resumen_runs = []
        claves_param = (
            "id_reentreno",
            "modelo",
            "fecha_min",
            "fecha_max",
            "filas",
            "observaciones",
            "commit",
            "version_vigente",
            "datos_sha256",
        )
        for r in runs:
            met = {x["key"]: x["value"] for x in r["data"].get("metrics", [])}
            par = {x["key"]: x["value"] for x in r["data"].get("params", [])}
            tag = {x["key"]: x["value"] for x in r["data"].get("tags", [])}
            resumen_runs.append(
                {
                    "run_id": r["info"]["run_id"],
                    "nombre": r["info"].get("run_name"),
                    "estado": r["info"]["status"],
                    "rol": tag.get("rol"),
                    "decision": tag.get("decision"),
                    "metricas": {
                        k: met[k]
                        for k in (
                            "costo_validacion",
                            "costo_baseline",
                            "costo_vigente",
                            "ventanas_ganadas",
                            "wape",
                            "mae",
                            "rmse",
                            "sesgo",
                            "promovido",
                        )
                        if k in met
                    },
                    "linaje": {k: par[k] for k in claves_param if k in par},
                }
            )
        out = {
            "experimentos": exp.get("experiments", []),
            "modelo_registrado": {
                "nombre": modelo.get("name"),
                "aliases": modelo.get("aliases", []),
            },
            "versiones": [
                {
                    "version": v["version"],
                    "run_id": v.get("run_id"),
                    "estado": v.get("status"),
                    "aliases": v.get("aliases")
                    or [
                        a["alias"]
                        for a in modelo.get("aliases", [])
                        if a["version"] == v["version"]
                    ],
                    "tags": {t["key"]: t["value"] for t in v.get("tags", [])},
                }
                for v in sorted(
                    versiones.get("model_versions", []), key=lambda v: int(v["version"])
                )
            ],
            "runs": resumen_runs,
        }
        self.escribir("mlflow/mlflow.json", out)
        return out

    def airflow(self) -> dict:
        base = self.a.airflow_url
        h = httpx.Client(base_url=base, timeout=20)
        tok = h.post(
            "/auth/token",
            json={
                "username": os.environ.get("AIRFLOW_ADMIN_USUARIO", "airflow"),
                "password": os.environ.get("AIRFLOW_ADMIN_PASSWORD", "airflow"),
            },
        ).json()["access_token"]
        h.headers["Authorization"] = f"Bearer {tok}"
        dag = h.get("/api/v2/dags/reentrenamiento").json()
        runs = (
            h.get(
                "/api/v2/dags/reentrenamiento/dagRuns",
                params={"order_by": "start_date", "limit": 50},
            )
            .json()
            .get("dag_runs", [])
        )
        salida = []
        for r in runs:
            ti = (
                h.get(f"/api/v2/dags/reentrenamiento/dagRuns/{r['dag_run_id']}/taskInstances")
                .json()
                .get("task_instances", [])
            )
            salida.append(
                {
                    "dag_run_id": r["dag_run_id"],
                    "estado": r["state"],
                    "inicio": r.get("start_date"),
                    "fin": r.get("end_date"),
                    "tareas": [
                        {
                            "tarea": t["task_id"],
                            "estado": t["state"],
                            "duracion_s": t.get("duration"),
                        }
                        for t in sorted(ti, key=lambda t: t.get("start_date") or "")
                    ],
                }
            )
        out = {
            "dag": {
                k: dag.get(k)
                for k in ("dag_id", "is_paused", "description", "timetable_summary", "tags")
            },
            "ejecuciones": salida,
        }
        self.escribir("airflow/dag_runs.json", out)
        return out

    # --- salida ---

    def escribir(self, ruta: str, datos: Any) -> None:
        p = self.salida / ruta
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(datos, indent=2, ensure_ascii=False, default=str) + "\n")

    def markdown(self, ml: dict, af: dict) -> str:
        L: list[str] = ["# Evidencia de funcionamiento", ""]
        L.append(
            "Generada con `scripts/evidencia.py` sobre un stack levantado desde cero con los datos "
            "publicados (proyecto de Compose aparte). Las solicitudes y observaciones son "
            "construidas por el script; cada respuesta se validó contra los modelos Pydantic del "
            "contrato del Anexo."
        )
        L += ["", "## 1. Arranque en frío", "", f"- {j(self.arranque)}", ""]
        L += [
            "## 2. Respuestas de la API (válidas y defectuosas)",
            "",
            "`nc` = problema **no catalogado** (se resuelve con las reglas generales de "
            "DECISIONES §1.2). Detalle completo de cada solicitud y respuesta en "
            "[`api/respuestas.json`](api/respuestas.json).",
            "",
            "| Endpoint | Código | Caso | Esperado | Obtenido | Contrato | Qué informó |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in self.casos:
            r = c["respuesta"] if isinstance(c["respuesta"], dict) else {}
            if c["http"] == 200:
                obs = r.get("calidad", {}).get("observaciones", [])
                det = "; ".join(f"`{o['campo']}` {o['accion']}: {o['detalle']}" for o in obs[:3])
                if "registros_guardados" in r:
                    det = f"registros_guardados={r['registros_guardados']}" + (
                        f"; {det}" if det else ""
                    )
            else:
                d = r.get("detail")
                det = d if isinstance(d, str) else j(d)[:160]
            det = (det or "—").replace("|", "\\|").replace("\n", " ")
            ok = "✅" if c["coincide"] and c["contrato_ok"] else "❌"
            L.append(
                f"| {c['endpoint'].split('/')[-1]} | {c['codigo']} | {c['caso']} | "
                f"{c['esperado']} | {c['obtenido']} {ok} | "
                f"{'cumple' if c['contrato_ok'] else 'NO'} | {det} |"
            )
        v = next((c for c in self.casos if c["caso"] == "solicitud válida"), None)
        e = next((c for c in self.casos if c["codigo"] == "E02"), None)
        for titulo, c in (
            ("Ejemplo completo: solicitud válida", v),
            ("Ejemplo completo: E02 reparado", e),
        ):
            if c:
                L += [
                    "",
                    f"### {titulo}",
                    "",
                    "Solicitud:",
                    "",
                    "```json",
                    json.dumps(c["solicitud"], indent=2, ensure_ascii=False),
                    "```",
                    "",
                    "Respuesta:",
                    "",
                    "```json",
                    json.dumps(c["respuesta"], indent=2, ensure_ascii=False),
                    "```",
                ]
        L += ["", "## 3. Reentrenamiento (endpoint de administración + DAG)", ""]
        for r in self.reentrenos:
            L += [
                f"### {r['etiqueta']}",
                "",
                f"- `POST /admin/reentrenar` → {r['post']['http']} {j(r['post']['respuesta'])}",
                f"- Modelo antes: {j(r['modelo_antes'])}",
                f"- `GET /admin/reentrenar/{{id}}` al terminar ({r.get('segundos')} s): "
                f"{j(r.get('estado'))}",
                f"- Modelo después: {j(r['modelo_despues'])}",
            ]
            if "segundos_hasta_en_uso" in r:
                L.append(
                    f"- Versión nueva en uso por la API (sin reinicio) a los "
                    f"{r['segundos_hasta_en_uso']} s de terminar"
                )
            L.append("")
        L += [
            "## 4. Caída temporal de MLflow",
            "",
            "| Paso | HTTP | Respuesta | Segundos |",
            "|---|---|---|---|",
        ]
        for p in self.resiliencia:
            L.append(
                f"| {p['paso']} | {p['http']} | `{j(p['respuesta'])[:140]}` | "
                f"{'' if p.get('segundos') is None else round(p['segundos'], 1)} |"
            )
        L += [
            "",
            "## 5. MLflow: experimentos, runs y versiones",
            "",
            f"Modelo registrado `{ml['modelo_registrado']['nombre']}`, alias: "
            f"{j(ml['modelo_registrado']['aliases'])}. Detalle en "
            "[`mlflow/mlflow.json`](mlflow/mlflow.json).",
            "",
            "| Versión | Alias | Promovido | Run |",
            "|---|---|---|---|",
        ]
        for v in ml["versiones"]:
            L.append(
                f"| {v['version']} | {', '.join(v['aliases']) or '—'} | "
                f"{v['tags'].get('promovido', '—')} | `{v['run_id']}` |"
            )
        L += [
            "",
            "| Run | Rol | Costo validación | Costo baseline | Costo vigente | WAPE | "
            "Observaciones | Decisión |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in ml["runs"]:
            m = r["metricas"]

            def f(k: str, m: dict = m) -> str:
                return f"{m[k]:,.0f}" if k in m else "—"

            wape = f"{m['wape']:.3f}" if "wape" in m else "—"
            L.append(
                f"| {r['nombre']} | {r['rol']} | {f('costo_validacion')} | {f('costo_baseline')} "
                f"| {f('costo_vigente')} | {wape} | {r['linaje'].get('observaciones', '—')} "
                f"| {r['decision'] or '—'} |"
            )
        L += [
            "",
            "## 6. Airflow: ejecuciones del DAG `reentrenamiento`",
            "",
            "Detalle en [`airflow/dag_runs.json`](airflow/dag_runs.json).",
            "",
            "| Ejecución | Estado | Tareas |",
            "|---|---|---|",
        ]
        for r in af["ejecuciones"]:
            tareas = " → ".join(
                f"{t['tarea']} ({t['estado']}, {t['duracion_s'] or 0:.0f} s)" for t in r["tareas"]
            )
            L.append(f"| `{r['dag_run_id']}` | {r['estado']} | {tareas} |")
        fallas = [c for c in self.casos if not (c["coincide"] and c["contrato_ok"])]
        L += [
            "",
            "## Resumen",
            "",
            f"- {len(self.casos)} casos de API, {len(fallas)} con diferencias; problemas de "
            f"contrato en toda la corrida: {len(self.c.problemas)}.",
            f"- Códigos: {j({f'{e} {c}': n for (e, c), n in sorted(self.c.codigos.items())})}",
            "",
        ]
        return "\n".join(L)

    # --- corrida ---

    def correr(self) -> int:
        if self.a.levantar:
            vols = subprocess.run(
                ["docker", "volume", "ls", "-q", "--filter", f"name={self.a.proyecto}_"],
                capture_output=True,
                text=True,
            )
            if vols.stdout.split():
                raise SystemExit(f"{self.a.proyecto} ya tiene volúmenes: usa otro --proyecto")
            t0 = time.monotonic()
            self.compose("up", "-d")
            t = self.c.esperar_health("ok", 600, 3)
            self.arranque = {
                "segundos_hasta_ok": None if t is None else round(time.monotonic() - t0),
                "modelo": self.c.modelo().cuerpo,
                "health": self.c.health().cuerpo,
            }
            print(f"Arranque: {j(self.arranque)}")
        self.reentrenar("Sin datos nuevos → no se promueve")
        self.api()
        self.choque()
        self.reentrenar(f"{DIAS_CHOQUE} días con demanda +25 % → se promueve")
        self.caida_mlflow()
        self.escribir("api/respuestas.json", self.casos)
        self.escribir(
            "reentrenamiento/reentrenos.json",
            {
                "arranque": self.arranque,
                "reentrenos": self.reentrenos,
                "caida_mlflow": self.resiliencia,
            },
        )
        ml, af = self.mlflow(), self.airflow()
        (self.salida / "evidencia.md").write_text(self.markdown(ml, af))
        fallas = [c for c in self.casos if not (c["coincide"] and c["contrato_ok"])]
        print(
            f"\n{len(self.casos) - len(fallas)}/{len(self.casos)} casos como se esperaba; "
            f"problemas de contrato: {len(self.c.problemas)}"
        )
        return 0 if not fallas and not self.c.problemas else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--datos", default=None, help="carpeta con los CSV (por defecto ./data)")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--mlflow-url", default="http://localhost:5001")
    ap.add_argument("--airflow-url", default="http://localhost:8080")
    ap.add_argument("--proyecto", default="novamercado-evidencia")
    ap.add_argument("--levantar", action="store_true", help="levantar un stack nuevo")
    ap.add_argument("--salida", default=str(RAIZ / "evidencia"))
    a = ap.parse_args(argv)
    a.datos_dir = dir_datos(a.datos).resolve()
    return Evidencia(a).correr()


if __name__ == "__main__":
    sys.exit(main())
