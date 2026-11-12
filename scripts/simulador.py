"""Simulador de la operación diaria **sin fuga**, con los escenarios que pide el enunciado.

El stack se levanta con el histórico truncado (`HISTORICO_HASTA=<corte>`, en un proyecto de
Compose con volúmenes nuevos) y se simulan los días retenidos como lo haría el evaluador:

- cada día `D`: primero las observaciones de `D-1` (si `D-1` fue un día simulado), después las
  solicitudes de predicción de `D` de cada tienda abierta, con las promociones reales del día;
- errores aleatorios: variantes equivalentes (catálogo E01–E04/E07/E10 e inventadas), que deben
  dar **las mismas** predicciones que la limpia; variantes sucias (E05, E06, E08, inventadas) que
  deben cumplir el contrato; rechazos (422) y cuerpos rotos; solicitudes y envíos repetidos (E09);
- escenarios: hueco entre el corte y el primer día (`--inicio`), choque de nivel (`--choque`),
  reentrenos periódicos (`--reentrenar-cada`), caída de MLflow (`--caida-mlflow`) y
  `down`/`up` sin `-v` (`--reinicio`).

Cada respuesta se valida contra el contrato. Al final compara el costo de las sugerencias del
servicio con el del baseline (media del mismo día de la semana con cuantil por familia, ajustado
con lo mismo que ve el servicio: histórico hasta el corte + observaciones enviadas `< D`) y
guarda el reporte en `--salida` (JSON + Markdown).

Uso (los datos reales se leen en local para conocer la verdad; nunca se versionan):
    python scripts/simulador.py --levantar --proyecto novamercado-sim \\
        --corte 2025-10-31 --inicio 2025-11-28 --fin 2026-02-28 \\
        --choque 1.2 --choque-desde 2026-02-01 --reentrenar-cada 14 \\
        --caida-mlflow 2026-01-10 --reinicio 2026-01-24

Nunca borra volúmenes: con `--levantar` exige un proyecto sin volúmenes previos.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from comun_datos import cargar, dir_datos
from comun_e2e import (
    CUERPOS_ROTOS,
    EQUIVALENTES_OBSERVACION,
    EQUIVALENTES_PREDICCION,
    RAIZ,
    RECHAZOS,
    SUCIAS_PREDICCION,
    Cliente,
    Compose,
    estado_esperado,
    mutar,
)
from novamercado.modelo.baseline import VENTANA_PANEL_DIAS, ajustar_baseline
from novamercado.modelo.metricas import costo_total, metricas_tecnicas

LIMITE_EN_USO_S = 120


def _por_familia(cuerpo: dict) -> dict[str, tuple[float, int]]:
    return {
        p["familia_id"]: (p["venta_esperada"], p["cantidad_sugerida"])
        for p in cuerpo.get("predicciones", [])
    }


class Simulador:
    def __init__(self, args: argparse.Namespace) -> None:
        self.a = args
        self.rng = random.Random(args.semilla)
        self.c = Cliente(args.url)
        self.compose = Compose(
            args.proyecto,
            {"HISTORICO_HASTA": args.corte.isoformat(), "DATOS_DIR": str(args.datos_dir)},
        )
        d = cargar(args.datos_dir)
        self.costos = {
            r.familia_id: (float(r.costo_faltante), float(r.costo_sobrante))
            for r in d.costos.itertuples()
        }
        h = d.historico.copy()
        h["fecha"] = pd.to_datetime(h["fecha"])
        h["ventas"] = h["ventas"].astype(float)
        self.verdad = self._aplicar_choque(h)
        self.apertura = {int(t.tienda_id): t.fecha_apertura.date() for t in d.tiendas.itertuples()}
        # Lo que ve el servicio: histórico hasta el corte + observaciones enviadas.
        self.conocido = h[h["fecha"] <= pd.Timestamp(args.corte)].copy()
        self.baseline = ajustar_baseline(self.conocido, self.costos)
        self.filas: list[dict] = []  # una por (tienda, fecha, familia) predicha
        self.chequeos: Counter[str] = Counter()
        self.fallas: list[str] = []
        self.eventos: list[dict] = []
        self.reentrenos: list[dict] = []
        self.ultimo_dia_observado: date | None = None
        self.tipos_error: Counter[str] = Counter()

    def _aplicar_choque(self, h: pd.DataFrame) -> pd.DataFrame:
        v = h.copy()
        if self.a.choque != 1.0 and self.a.choque_desde:
            m = v["fecha"] >= pd.Timestamp(self.a.choque_desde)
            v.loc[m, "ventas"] = (v.loc[m, "ventas"] * self.a.choque).round()
        return v

    # --- utilidades ----------------------------------------------------------------------------

    def chequear(self, nombre: str, ok: bool, detalle: str = "") -> None:
        self.chequeos[f"{nombre}:{'ok' if ok else 'falla'}"] += 1
        if not ok:
            self.fallas.append(f"{nombre}: {detalle}")
            print(f"  [FALLA] {nombre}: {detalle}")

    def evento(self, tipo: str, **datos) -> None:
        self.eventos.append({"tipo": tipo, **datos})
        print(f"  · {tipo}: {json.dumps(datos, ensure_ascii=False, default=str)}")

    def tiendas_abiertas(self, d: date) -> list[int]:
        return sorted(t for t, a in self.apertura.items() if a <= d)

    def dia(self, d: date) -> pd.DataFrame:
        return self.verdad[self.verdad["fecha"] == pd.Timestamp(d)]

    # --- un día --------------------------------------------------------------------------------

    def observar(self, d: date) -> None:
        """Envía las observaciones reales (con choque) de `d` de todas las tiendas."""
        reales = self.dia(d)
        for t, g in reales.groupby("tienda_id"):
            limpia = {
                "tienda_id": int(t),
                "fecha": d.isoformat(),
                "ventas": [
                    {"familia_id": r.familia_id, "ventas": int(r.ventas)} for r in g.itertuples()
                ],
            }
            u = self.rng.random()
            if u < self.a.prob_error / 2:  # E08: devolución negativa y luego la corrección
                malo = dict(limpia, ventas=[dict(x) for x in limpia["ventas"]])
                self.rng.choice(malo["ventas"])["ventas"] = -self.rng.randint(1, 20)
                r = self.c.observaciones(malo)
                self.tipos_error["obs_E08"] += 1
                self.chequear(
                    "E08 guarda 11 y advierte",
                    r.codigo == 200
                    and r.cuerpo.get("registros_guardados") == len(limpia["ventas"]) - 1
                    and r.cuerpo["calidad"]["estado"] == "advertencia",
                    str(r.cuerpo)[:200],
                )
                r = self.c.observaciones(limpia)
                self.chequear(
                    "corrección tras E08 guarda 1", r.cuerpo.get("registros_guardados") == 1
                )
                continue
            if u < self.a.prob_error:
                nombre, cuerpo = mutar(EQUIVALENTES_OBSERVACION, limpia, self.rng)
                self.tipos_error[f"obs_{nombre}"] += 1
            else:
                cuerpo = limpia
            r = self.c.observaciones(cuerpo)
            self.chequear(
                "observación guarda todo",
                r.codigo == 200 and r.cuerpo.get("registros_guardados") == len(limpia["ventas"]),
                f"{r.codigo} {str(r.cuerpo)[:200]}",
            )
            if self.rng.random() < self.a.prob_repetida:  # E09
                r2 = self.c.observaciones(limpia)
                self.tipos_error["obs_E09"] += 1
                self.chequear(
                    "E09 reenvío idéntico guarda 0",
                    r2.codigo == 200
                    and r2.cuerpo.get("registros_guardados") == 0
                    and r2.cuerpo["calidad"]["estado"] == "ok",
                    str(r2.cuerpo)[:200],
                )
        nuevas = reales.assign(ventas=reales["ventas"].astype(float))
        self.conocido = pd.concat([self.conocido, nuevas], ignore_index=True)
        self.ultimo_dia_observado = d

    def predecir(self, d: date) -> None:
        reales = self.dia(d)
        assert self.ultimo_dia_observado is None or self.ultimo_dia_observado < d  # sin fuga
        panel = self.conocido[
            (self.conocido["fecha"] < pd.Timestamp(d))
            & (self.conocido["fecha"] >= pd.Timestamp(d) - pd.Timedelta(days=VENTANA_PANEL_DIAS))
        ]
        for t in self.tiendas_abiertas(d):
            g = reales[reales["tienda_id"] == t]
            if g.empty:
                continue
            limpia = {
                "tienda_id": t,
                "fecha": d.isoformat(),
                "familias": [
                    {"familia_id": r.familia_id, "en_promocion": int(r.en_promocion)}
                    for r in g.itertuples()
                ],
            }
            # Primero lo sucio (si toca): la última solicitud aceptada es la limpia (§6.3).
            if self.rng.random() < self.a.prob_error:
                nombre, sucia = mutar(SUCIAS_PREDICCION, limpia, self.rng)
                self.tipos_error[f"pred_{nombre}"] += 1
                r = self.c.prediccion(sucia)
                self.chequear(f"{nombre} sin 500", r.codigo in (200, 422), str(r.codigo))
            if self.rng.random() < self.a.prob_error / 2:
                nombre, mala = mutar(RECHAZOS, limpia, self.rng)
                self.tipos_error[f"pred_{nombre}"] += 1
                r = self.c.prediccion(mala)
                self.chequear(f"{nombre} → 422", r.codigo == 422, f"{r.codigo} {r.cuerpo}")
            if self.rng.random() < self.a.prob_error / 4:
                nombre, crudo, cab = self.rng.choice(CUERPOS_ROTOS)
                self.tipos_error[f"pred_cuerpo_{nombre}"] += 1
                r = self.c.prediccion(None, crudo=crudo, headers=cab)
                self.chequear(f"cuerpo {nombre} → 422", r.codigo == 422, str(r.codigo))

            r = self.c.prediccion(limpia)
            if r.codigo != 200:
                self.chequear("predicción limpia 200", False, f"{r.codigo} {r.cuerpo}")
                continue
            principal = _por_familia(r.cuerpo)
            self.chequear(
                "predicción limpia ok",
                r.cuerpo["calidad"]["estado"] == "ok" and set(principal) == set(g["familia_id"]),
            )
            if self.rng.random() < self.a.prob_error:
                nombre, equiv = mutar(EQUIVALENTES_PREDICCION, limpia, self.rng)
                self.tipos_error[f"pred_{nombre}"] += 1
                r2 = self.c.prediccion(equiv)
                self.chequear(
                    f"{nombre} equivalente",
                    r2.codigo == 200
                    and _por_familia(r2.cuerpo) == principal
                    and r2.cuerpo["calidad"]["estado"] in estado_esperado(nombre),
                    f"{r2.codigo} {json.dumps(r2.cuerpo, ensure_ascii=False)[:300]}",
                )
            if self.rng.random() < self.a.prob_repetida:
                r3 = self.c.prediccion(limpia)
                self.tipos_error["pred_repetida"] += 1
                self.chequear(
                    "solicitud repetida → mismas predicciones",
                    r3.codigo == 200 and _por_familia(r3.cuerpo) == principal,
                )
            base = self.baseline.predecir(
                g.assign(en_promocion=g["en_promocion"].astype(int)), panel
            ).set_index("familia_id")
            for fila in g.itertuples():
                venta, cant = principal[fila.familia_id]
                self.filas.append(
                    {
                        "fecha": d,
                        "tienda_id": t,
                        "familia_id": fila.familia_id,
                        "ventas": float(fila.ventas),
                        "venta_esperada": venta,
                        "cantidad_sugerida": cant,
                        "base_venta": float(base.at[fila.familia_id, "venta_esperada"]),
                        "base_cantidad": int(base.at[fila.familia_id, "cantidad_sugerida"]),
                        "version": r.cuerpo["version_modelo"],
                    }
                )

    # --- escenarios ------------------------------------------------------------------------------

    def reentrenar(self, d: date, *, esperar_503: bool = False) -> None:
        antes = self.c.modelo().cuerpo
        r = self.c.reentrenar()
        if esperar_503:
            self.chequear("reentreno con MLflow caído → 503", r.codigo == 503, str(r.cuerpo))
            return
        if r.codigo != 202:
            self.chequear("reentreno → 202", False, f"{r.codigo} {r.cuerpo}")
            return
        id_ = r.cuerpo["id"]
        seg, fin = self.c.esperar_reentreno(id_)
        f = fin.cuerpo
        registro = {"despues_de": d.isoformat(), "segundos": round(seg, 1), **f}
        self.chequear("reentreno ≤ 10 min", f.get("estado") != "en_curso" and seg <= 600)
        self.chequear("reentreno exitoso", f.get("estado") == "exitoso", str(f))
        if f.get("promovido"):
            nueva = str(f.get("version"))
            t = (
                0.0
                if self.c.modelo().cuerpo.get("version") == nueva
                else _esperar_version(self.c, nueva)
            )
            registro["segundos_hasta_en_uso"] = t
            self.chequear(
                "modelo promovido en uso ≤ 2 min", t is not None and t <= LIMITE_EN_USO_S, str(t)
            )
        else:
            despues = self.c.modelo().cuerpo
            self.chequear("sin promoción el vigente no cambia", despues == antes, str(despues))
        self.reentrenos.append(registro)
        print(f"  · reentreno: {json.dumps(registro, ensure_ascii=False)}")

    def caida_mlflow(self, d: date) -> None:
        self.compose("stop", "mlflow")
        t = self.c.esperar_health("degradado", 60, intervalo=1)
        self.chequear("MLflow caído → degradado < 1 min", t is not None, str(t))
        self.evento("mlflow_detenido", fecha=d.isoformat(), segundos_hasta_degradado=t)

    def vuelta_mlflow(self, d: date) -> None:
        self.reentrenar(d, esperar_503=True)
        self.compose("start", "mlflow")
        t = self.c.esperar_health("ok", 120, intervalo=1)
        self.chequear("MLflow de vuelta → ok < 2 min", t is not None, str(t))
        self.evento("mlflow_iniciado", fecha=d.isoformat(), segundos_hasta_ok=t)

    def reinicio(self, d: date) -> None:
        version = self.c.modelo().cuerpo
        self.compose("down")
        t0 = time.monotonic()
        self.compose("up", "-d")
        t = self.c.esperar_health("ok", 600, intervalo=3)
        self.chequear("down/up → ok ≤ 10 min", t is not None, str(t))
        self.chequear("down/up conserva el modelo", self.c.modelo().cuerpo == version)
        self.evento(
            "down_up", fecha=d.isoformat(), segundos=round(time.monotonic() - t0, 1), modelo=version
        )
        # Las observaciones de ayer siguen guardadas: reenviarlas guarda 0.
        if self.ultimo_dia_observado:
            dia = self.dia(self.ultimo_dia_observado)
            t = int(dia["tienda_id"].iloc[0])
            g = dia[dia["tienda_id"] == t]
            r = self.c.observaciones(
                {
                    "tienda_id": t,
                    "fecha": self.ultimo_dia_observado.isoformat(),
                    "ventas": [
                        {"familia_id": x.familia_id, "ventas": int(x.ventas)}
                        for x in g.itertuples()
                    ],
                }
            )
            self.chequear(
                "tras down/up las observaciones persisten",
                r.cuerpo.get("registros_guardados") == 0,
                str(r.cuerpo),
            )

    # --- ciclo -----------------------------------------------------------------------------------

    def levantar(self) -> None:
        existentes = subprocess.run(
            ["docker", "volume", "ls", "-q", "--filter", f"name={self.a.proyecto}_"],
            capture_output=True,
            text=True,
        ).stdout.split()
        if existentes:
            raise SystemExit(
                f"El proyecto {self.a.proyecto} ya tiene volúmenes ({', '.join(existentes)}): "
                "usa otro --proyecto (este script nunca borra volúmenes)."
            )
        t0 = time.monotonic()
        self.compose("up", "-d")
        t = self.c.esperar_health("ok", 600, intervalo=3)
        self.chequear("arranque en frío → ok ≤ 10 min", t is not None, str(t))
        self.evento(
            "arranque", segundos=round(time.monotonic() - t0, 1), modelo=self.c.modelo().cuerpo
        )

    def verificar_corte(self) -> None:
        """Aborta si el modelo vigente vio datos posteriores al corte (habría fuga).

        Lee del Registry de MLflow el parámetro `fecha_max` del run del alias. Pasa, por ejemplo,
        si las imágenes son anteriores a `HISTORICO_HASTA` o si el volumen ya tenía datos.
        """
        import httpx

        m = httpx.Client(base_url=self.a.mlflow_url, timeout=10)
        mv = m.get(
            "/api/2.0/mlflow/registered-models/alias",
            params={"name": "novamercado", "alias": "champion"},
        ).json()["model_version"]
        run = m.get("/api/2.0/mlflow/runs/get", params={"run_id": mv["run_id"]}).json()["run"]
        params = {x["key"]: x["value"] for x in run["data"]["params"]}
        fecha_max = date.fromisoformat(params["fecha_max"])
        ok = fecha_max <= self.a.corte
        self.chequear("modelo inicial sin datos posteriores al corte", ok, str(fecha_max))
        self.evento("corte_verificado", fecha_max_modelo=fecha_max, corte=self.a.corte)
        if not ok:
            raise SystemExit(
                f"El modelo v{mv['version']} vio datos hasta {fecha_max} > corte {self.a.corte}: "
                "reconstruir las imágenes (docker compose build) y usar un proyecto nuevo."
            )

    def correr(self) -> dict:
        a = self.a
        if a.inicio <= a.corte:
            raise SystemExit("--inicio debe ser posterior a --corte (si no, habría fuga)")
        if a.levantar:
            self.levantar()
        self.verificar_corte()
        t0 = time.monotonic()
        d, n = a.inicio, 0
        while d <= a.fin:
            n += 1
            print(f"{d} ({n})")
            if a.caida_mlflow == d:
                self.caida_mlflow(d)
            if a.reinicio == d:
                self.reinicio(d)
            # Ventas de ayer (los días del hueco antes de --inicio nunca se observan).
            previo = d - timedelta(days=1)
            if previo >= a.inicio and self.ultimo_dia_observado != previo:
                self.observar(previo)
            self.predecir(d)
            if a.caida_mlflow == d:
                self.vuelta_mlflow(d)
            if a.reentrenar_cada and n % a.reentrenar_cada == 0:
                self.observar(d)  # el reentreno de la noche ya tiene las ventas del día
                self.reentrenar(d)
            d += timedelta(days=1)
        return self.reporte(time.monotonic() - t0)

    def reporte(self, segundos: float) -> dict:
        df = pd.DataFrame(self.filas)
        base = df.assign(venta_esperada=df["base_venta"], cantidad_sugerida=df["base_cantidad"])
        c_mod, c_base = costo_total(df, self.costos), costo_total(base, self.costos)
        mes = df["fecha"].map(lambda x: x.strftime("%Y-%m"))
        por_mes = {
            m: {
                "modelo": round(costo_total(df[mes == m], self.costos)),
                "baseline": round(costo_total(base[mes == m], self.costos)),
            }
            for m in sorted(mes.unique())
        }
        por_familia = {
            f: {
                "modelo": round(costo_total(df[df.familia_id == f], self.costos)),
                "baseline": round(costo_total(base[base.familia_id == f], self.costos)),
            }
            for f in sorted(df["familia_id"].unique())
        }
        lat = {
            e: {
                "n": len(v),
                "p50_ms": round(statistics.median(v), 1),
                "p95_ms": round(float(np.percentile(v, 95)), 1),
            }
            for e, v in self.c.latencias.items()
        }
        return {
            "configuracion": {
                k: (v.isoformat() if isinstance(v, date) else v)
                for k, v in vars(self.a).items()
                if k not in ("datos_dir", "url", "mlflow_url", "salida", "datos")
            },
            "duracion_s": round(segundos, 1),
            "dias_simulados": int(df["fecha"].nunique()),
            "filas_predichas": len(df),
            "costo": {
                "modelo": round(c_mod),
                "baseline": round(c_base),
                "diferencia_relativa": round(c_mod / c_base - 1, 4),
            },
            "costo_por_mes": por_mes,
            "costo_por_familia": por_familia,
            "metricas_modelo": {k: round(v, 4) for k, v in metricas_tecnicas(df).items()},
            "metricas_baseline": {k: round(v, 4) for k, v in metricas_tecnicas(base).items()},
            "versiones_usadas": dict(Counter(df["version"])),
            "reentrenos": self.reentrenos,
            "eventos": self.eventos,
            "codigos_http": {f"{e} {c}": n for (e, c), n in sorted(self.c.codigos.items())},
            "errores_inyectados": dict(sorted(self.tipos_error.items())),
            "chequeos": dict(sorted(self.chequeos.items())),
            "problemas_de_contrato": self.c.problemas[:50],
            "fallas": self.fallas[:50],
            "latencias": lat,
        }


def _esperar_version(c: Cliente, version: str) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < LIMITE_EN_USO_S + 30:
        if c.modelo().cuerpo.get("version") == version:
            return round(time.monotonic() - t0, 1)
        time.sleep(2)
    return None


def a_markdown(r: dict) -> str:
    c = r["costo"]
    lineas = [
        "# Simulación de la operación (sin fuga)",
        "",
        f"Configuración: `{json.dumps(r['configuracion'], ensure_ascii=False)}`",
        "",
        f"- Días simulados: {r['dias_simulados']} · filas predichas: {r['filas_predichas']} · "
        f"duración: {r['duracion_s']} s",
        f"- **Costo de las sugerencias: modelo {c['modelo']:,} vs baseline {c['baseline']:,} "
        f"({c['diferencia_relativa']:+.1%})**",
        f"- Problemas de contrato: {len(r['problemas_de_contrato'])} · fallas: {len(r['fallas'])}",
        "",
        "## Costo por mes",
        "",
        "| Mes | Modelo | Baseline | Diferencia |",
        "|---|---:|---:|---:|",
    ]
    for m, v in r["costo_por_mes"].items():
        lineas.append(
            f"| {m} | {v['modelo']:,} | {v['baseline']:,} "
            f"| {v['modelo'] / v['baseline'] - 1:+.1%} |"
        )
    lineas += [
        "",
        "## Reentrenos",
        "",
        "| Después de | s | Estado | Promovido | Versión | s hasta en uso |",
        "|---|---:|---|---|---|---:|",
    ]
    for x in r["reentrenos"]:
        lineas.append(
            f"| {x['despues_de']} | {x['segundos']} | {x.get('estado')} | {x.get('promovido')} | "
            f"{x.get('version')} | {x.get('segundos_hasta_en_uso', '')} |"
        )
    lineas += ["", "## Eventos", ""]
    lineas += [f"- {json.dumps(e, ensure_ascii=False, default=str)}" for e in r["eventos"]]
    lineas += ["", "## Chequeos", ""]
    lineas += [f"- {k}: {v}" for k, v in r["chequeos"].items()]
    if r["fallas"]:
        lineas += ["", "## Fallas", ""] + [f"- {f}" for f in r["fallas"]]
    lineas += ["", "## Latencias", ""]
    lineas += [f"- {e}: {json.dumps(v)}" for e, v in r["latencias"].items()]
    return "\n".join(lineas) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    fecha = date.fromisoformat
    ap.add_argument("--datos", default=None, help="carpeta con los CSV (por defecto ./data)")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--mlflow-url", default="http://localhost:5001")
    ap.add_argument("--proyecto", default="novamercado-sim", help="proyecto de compose (-p)")
    ap.add_argument("--levantar", action="store_true", help="levantar el stack con el corte")
    ap.add_argument("--corte", type=fecha, required=True, help="HISTORICO_HASTA del bootstrap")
    ap.add_argument("--inicio", type=fecha, required=True, help="primer día con solicitudes")
    ap.add_argument("--fin", type=fecha, required=True)
    ap.add_argument("--choque", type=float, default=1.0, help="factor de nivel (p. ej. 1.2)")
    ap.add_argument("--choque-desde", type=fecha, default=None)
    ap.add_argument("--reentrenar-cada", type=int, default=0, help="días entre reentrenos")
    ap.add_argument("--caida-mlflow", type=fecha, default=None)
    ap.add_argument("--reinicio", type=fecha, default=None, help="día con down/up sin -v")
    ap.add_argument("--prob-error", type=float, default=0.2)
    ap.add_argument("--prob-repetida", type=float, default=0.1)
    ap.add_argument("--semilla", type=int, default=2026)
    ap.add_argument("--salida", default=str(RAIZ / "evidencia" / "simulacion"))
    args = ap.parse_args(argv)
    args.datos_dir = dir_datos(args.datos).resolve()
    sim = Simulador(args)
    r = sim.correr()
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    (salida / "simulacion.json").write_text(
        json.dumps(r, indent=2, ensure_ascii=False, default=str) + "\n"
    )
    (salida / "simulacion.md").write_text(a_markdown(r))
    print(a_markdown(r))
    return 0 if not r["fallas"] and not r["problemas_de_contrato"] else 1


if __name__ == "__main__":
    sys.exit(main())
