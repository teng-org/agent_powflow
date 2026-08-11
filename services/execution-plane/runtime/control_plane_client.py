import os

import httpx


class ControlPlaneCallbackError(RuntimeError):
    pass


class ControlPlaneClient:
    def __init__(self) -> None:
        self.base_url = os.getenv("CONTROL_PLANE_INTERNAL_URL", "http://localhost:8099").rstrip("/")
        self.token = os.getenv("CONTROL_PLANE_INTERNAL_TOKEN", "")

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Token": self.token}

    def append_event(self, run_id: str, event: dict) -> dict:
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/internal/runs/{run_id}/events",
                headers=self._headers(),
                json=event,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ControlPlaneCallbackError(f"control-plane event callback failed: {exc}") from exc

    def append_task_event(self, task_id: str, event: dict) -> dict:
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/internal/tasks/{task_id}/events",
                headers=self._headers(),
                json=event,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ControlPlaneCallbackError(f"control-plane task event callback failed: {exc}") from exc

    def get_execution_context(self, run_id: str) -> dict:
        try:
            response = httpx.get(
                f"{self.base_url}/api/v1/internal/runs/{run_id}/execution-context",
                headers=self._headers(),
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()["item"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ControlPlaneCallbackError(f"control-plane context lookup failed: {exc}") from exc

    def suspend_for_approval(self, run_id: str, payload: dict) -> dict:
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/internal/runs/{run_id}/approvals/suspend",
                headers=self._headers(),
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()["item"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ControlPlaneCallbackError(f"control-plane approval suspension failed: {exc}") from exc

    def claim_execution_lease(self, run_id: str, payload: dict) -> dict:
        return self._lease_mutation(run_id, "claim", payload)

    def heartbeat_execution_lease(self, run_id: str, payload: dict) -> dict:
        return self._lease_mutation(run_id, "heartbeat", payload)

    def release_execution_lease(self, run_id: str, payload: dict) -> dict:
        return self._lease_mutation(run_id, "release", payload)

    def record_dead_letter(self, payload: dict) -> dict:
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/internal/run-dead-letters",
                headers=self._headers(),
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ControlPlaneCallbackError(f"control-plane dead-letter callback failed: {exc}") from exc

    def _lease_mutation(self, run_id: str, action: str, payload: dict) -> dict:
        try:
            response = httpx.post(
                f"{self.base_url}/api/v1/internal/runs/{run_id}/lease/{action}",
                headers=self._headers(),
                json=payload,
                timeout=10.0,
            )
            response.raise_for_status()
            return response.json()["item"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ControlPlaneCallbackError(f"control-plane lease {action} failed: {exc}") from exc


control_plane_client = ControlPlaneClient()
