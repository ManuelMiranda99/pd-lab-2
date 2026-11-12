"""Smoke de punta a punta contra un stack levantado (`docker compose up`).

Comprueba lo que revisará el evaluador, con tiempos:

1. `/health` en `ok` en ≤ 10 min y `/modelo` con el contrato.
2. Predicción válida (200, `ok`, 12 familias, `cantidad_sugerida` entero ≥ 0, versión texto).
3. E01 (`"T05"`) → 200 `corregido` con las mismas predicciones que la limpia.
4. Rechazos 422 (tienda inexistente, fecha imposible, cuerpos rotos) y nunca 500.
5. Observaciones idempotentes: el reenvío idéntico guarda 0 registros.
6. Reentreno: 202 con id, un segundo POST devuelve el mismo id, termina en ≤ 10 min con el
   contrato de estado; id inventado → 404.
7. Con `--mlflow`: `stop mlflow` → `degradado` en < 1 min y la predicción sigue en 200;
   `start mlflow` → `ok` en < 2 min.

**Escribe en la BD** (una observación y un reentreno): correrlo contra un stack de prueba (CI,
simulador), no contra el que se va a entregar.

Uso:
    python scripts/smoke.py [--url http://localhost:8000] [--fecha 2026-03-10] [--mlflow]
Sale con código 1 si algo falla. Guarda el resumen en `--salida` si se indica.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from comun_e2e import CUERPOS_ROTOS, Cliente, Compose
from novamercado.sintetico import solicitud_observaciones, solicitud_prediccion

LIMITE_LISTO_S = 600
LIMITE_REENTRENO_S = 600
LIMITE_DEGRADADO_S = 60
LIMITE_RECUPERA_S = 120


class Smoke:
    def __init__(self, cliente: Cliente) -> None:
        self.c = cliente
        self.resultados: list[dict] = []

    def comprobar(self, nombre: str, ok: bool, detalle: str = "") -> bool:
        self.resultados.append({"prueba": nombre, "ok": bool(ok), "detalle": detalle})
        print(f"[{'OK' if ok else 'FALLA'}] {nombre}" + (f" — {detalle}" if detalle else ""))
        return ok

    def correr(self, fecha: str, tienda: int, con_mlflow: bool, proyecto: str | None) -> bool:
        c = self.c
        # 1. listo
        t = c.esperar_health("ok", LIMITE_LISTO_S, intervalo=5)
        self.comprobar("health ok ≤ 10 min", t is not None, f"{t:.0f} s" if t is not None else "")
        m = c.modelo()
        self.comprobar("GET /modelo", m.codigo == 200 and not m.problemas, json.dumps(m.cuerpo))

        # 2. predicción válida
        limpia = solicitud_prediccion(tienda_id=tienda, fecha=fecha)
        p = c.prediccion(limpia)
        cuerpo = p.cuerpo if isinstance(p.cuerpo, dict) else {}
        self.comprobar(
            "predicción válida",
            p.codigo == 200
            and not p.problemas
            and cuerpo.get("calidad") == {"estado": "ok", "observaciones": []}
            and len(cuerpo.get("predicciones", [])) == 12
            and cuerpo.get("tienda_id") == tienda
            and cuerpo.get("fecha") == fecha,
            f"{p.codigo} en {p.ms:.0f} ms, versión {cuerpo.get('version_modelo')!r}",
        )

        # 3. E01 equivalente
        e01 = dict(limpia, tienda_id=f"T{tienda:02d}")
        r = c.prediccion(e01)
        rc = r.cuerpo if isinstance(r.cuerpo, dict) else {}
        self.comprobar(
            "E01 tienda 'T0N' → corregido, mismas predicciones",
            r.codigo == 200
            and rc.get("calidad", {}).get("estado") == "corregido"
            and rc.get("predicciones") == cuerpo.get("predicciones"),
        )

        # 4. rechazos
        casos = {
            "tienda inexistente": dict(limpia, tienda_id=99),
            "fecha imposible": dict(limpia, fecha="2026-02-30"),
            "sin tienda": {k: v for k, v in limpia.items() if k != "tienda_id"},
        }
        for nombre, sol in casos.items():
            r = c.prediccion(sol)
            self.comprobar(f"422 {nombre}", r.codigo == 422 and not r.problemas)
        for nombre, crudo, cab in CUERPOS_ROTOS:
            r = c.prediccion(None, crudo=crudo, headers=cab)
            self.comprobar(f"422 cuerpo {nombre}", r.codigo == 422 and not r.problemas)

        # 5. observaciones idempotentes
        obs = solicitud_observaciones(tienda_id=tienda, fecha=fecha)
        o1, o2 = c.observaciones(obs), c.observaciones(obs)
        g1 = o1.cuerpo.get("registros_guardados") if isinstance(o1.cuerpo, dict) else None
        g2 = o2.cuerpo.get("registros_guardados") if isinstance(o2.cuerpo, dict) else None
        self.comprobar(
            "observaciones idempotentes",
            o1.codigo == 200 and o2.codigo == 200 and g2 == 0 and not o2.problemas,
            f"primera {g1}, reenvío {g2}",
        )

        # 6. reentreno
        n = c.estado_reentreno("no-existe-este-id")
        self.comprobar("id inventado → 404", n.codigo == 404 and not n.problemas)
        a = c.reentrenar()
        id_ = a.cuerpo.get("id") if isinstance(a.cuerpo, dict) else None
        self.comprobar("POST /admin/reentrenar → 202", a.codigo == 202 and bool(id_), str(id_))
        if id_:
            b = c.reentrenar()
            otro = b.cuerpo.get("id") if isinstance(b.cuerpo, dict) else None
            self.comprobar("segundo POST con uno en curso → mismo id", otro == id_, str(otro))
            seg, fin = c.esperar_reentreno(id_, LIMITE_REENTRENO_S)
            f = fin.cuerpo if isinstance(fin.cuerpo, dict) else {}
            self.comprobar(
                "reentreno termina ≤ 10 min",
                f.get("estado") in ("exitoso", "fallido") and not fin.problemas,
                f"{seg:.0f} s → {json.dumps(f, ensure_ascii=False)}",
            )

        # 7. caída de MLflow
        if con_mlflow:
            compose = Compose(proyecto)
            compose("stop", "mlflow")
            try:
                t = c.esperar_health("degradado", LIMITE_DEGRADADO_S, intervalo=1)
                self.comprobar("MLflow caído → degradado < 1 min", t is not None, f"{t}")
                r = c.prediccion(limpia)
                self.comprobar("predicción con MLflow caído", r.codigo == 200, f"{r.ms:.0f} ms")
            finally:
                compose("start", "mlflow")
            t = c.esperar_health("ok", LIMITE_RECUPERA_S, intervalo=1)
            self.comprobar("MLflow de vuelta → ok < 2 min", t is not None, f"{t}")

        self.comprobar(
            "todas las respuestas cumplen el contrato",
            not c.problemas and not any(cod >= 500 and cod != 503 for _, cod in c.codigos),
            "; ".join(c.problemas[:5]),
        )
        return all(r["ok"] for r in self.resultados)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--fecha", default="2026-03-10", help="fecha de la solicitud de prueba")
    ap.add_argument("--tienda", type=int, default=3)
    ap.add_argument("--mlflow", action="store_true", help="probar stop/start del servicio mlflow")
    ap.add_argument("--proyecto", default=None, help="nombre del proyecto de compose (-p)")
    ap.add_argument("--salida", default=None, help="archivo JSON con el resumen")
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    smoke = Smoke(Cliente(args.url))
    ok = smoke.correr(args.fecha, args.tienda, args.mlflow, args.proyecto)
    resumen = {
        "ok": ok,
        "segundos": round(time.monotonic() - t0, 1),
        "pruebas": smoke.resultados,
        "codigos": {f"{e} {c}": n for (e, c), n in sorted(smoke.c.codigos.items())},
    }
    if args.salida:
        Path(args.salida).parent.mkdir(parents=True, exist_ok=True)
        Path(args.salida).write_text(json.dumps(resumen, indent=2, ensure_ascii=False) + "\n")
    fallas = sum(not r["ok"] for r in smoke.resultados)
    print(f"\n{len(smoke.resultados) - fallas}/{len(smoke.resultados)} pruebas OK")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
