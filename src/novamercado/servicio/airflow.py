"""Cliente mínimo de la API REST de Airflow 3 (`/api/v2`, autenticación con token JWT)."""

from __future__ import annotations

import threading

import httpx


class AirflowNoDisponible(RuntimeError):
    """Airflow no respondió o rechazó la operación."""


def _json(r: httpx.Response) -> dict:
    """Cuerpo JSON (objeto) de una respuesta; si no lo es (Airflow arrancando, proxy), Airflow se
    considera no disponible en vez de lanzar una excepción no controlada."""
    try:
        datos = r.json()
    except ValueError as exc:
        raise AirflowNoDisponible(
            f"Airflow respondió algo que no es JSON ({r.status_code})"
        ) from exc
    if not isinstance(datos, dict):
        raise AirflowNoDisponible(f"Airflow respondió un JSON inesperado ({r.status_code})")
    return datos


class AirflowClient:
    def __init__(
        self,
        base_url: str,
        usuario: str,
        password: str,
        *,
        timeout: float = 5.0,
        transporte: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.usuario = usuario
        self.password = password
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transporte)
        self._token: str | None = None
        self._bloqueo = threading.Lock()

    def cerrar(self) -> None:
        self._http.close()

    # --- autenticación -----------------------------------------------------------------------

    def _obtener_token(self) -> str:
        try:
            r = self._http.post(
                "/auth/token", json={"username": self.usuario, "password": self.password}
            )
        except httpx.HTTPError as exc:
            raise AirflowNoDisponible(f"Airflow no responde: {exc}") from exc
        if r.status_code not in (200, 201):
            raise AirflowNoDisponible(f"No se obtuvo token de Airflow ({r.status_code})")
        token = _json(r).get("access_token")
        if not isinstance(token, str) or not token:
            raise AirflowNoDisponible("Airflow respondió sin token")
        return token

    def _pedir(self, metodo: str, ruta: str, **kwargs) -> httpx.Response:
        for intento in range(2):
            with self._bloqueo:
                if self._token is None:
                    self._token = self._obtener_token()
                token = self._token
            try:
                r = self._http.request(
                    metodo, ruta, headers={"Authorization": f"Bearer {token}"}, **kwargs
                )
            except httpx.HTTPError as exc:
                raise AirflowNoDisponible(f"Airflow no responde: {exc}") from exc
            if r.status_code == 401 and intento == 0:
                with self._bloqueo:
                    self._token = None  # token vencido: se renueva una vez
                continue
            return r
        return r

    # --- operaciones -------------------------------------------------------------------------

    def salud(self) -> bool:
        """`GET /api/v2/monitor/health`: metadatabase y scheduler sanos (no requiere token)."""
        try:
            r = self._http.get("/api/v2/monitor/health")
        except httpx.HTTPError:
            return False
        if r.status_code != 200:
            return False
        try:
            datos = _json(r)
        except AirflowNoDisponible:
            return False
        return all(
            (datos.get(c) or {}).get("status") == "healthy" for c in ("metadatabase", "scheduler")
        )

    def dag_activo(self, dag_id: str) -> bool:
        """El DAG existe y no está pausado."""
        r = self._pedir("GET", f"/api/v2/dags/{dag_id}")
        return r.status_code == 200 and not _json(r).get("is_paused", True)

    def disparar(self, dag_id: str, run_id: str) -> None:
        r = self._pedir(
            "POST",
            f"/api/v2/dags/{dag_id}/dagRuns",
            json={"dag_run_id": run_id, "logical_date": None, "conf": {}},
        )
        if r.status_code not in (200, 201):
            raise AirflowNoDisponible(
                f"Airflow rechazó el disparo ({r.status_code}): {r.text[:200]}"
            )

    def estado_run(self, dag_id: str, run_id: str) -> str | None:
        """Estado del dag run (`queued`, `running`, `success`, `failed`) o None si no existe."""
        r = self._pedir("GET", f"/api/v2/dags/{dag_id}/dagRuns/{run_id}")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise AirflowNoDisponible(f"Airflow respondió {r.status_code}")
        return _json(r).get("state")
