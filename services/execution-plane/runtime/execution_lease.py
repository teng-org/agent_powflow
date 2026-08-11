import threading
import time
import uuid

from runtime.control_plane_client import ControlPlaneCallbackError


class ExecutionLeaseLost(RuntimeError):
    pass


class ExecutionLeaseGuard:
    def __init__(self, client, run_id: str, tenant_id: str, worker_id: str) -> None:
        self.client = client
        self.run_id = run_id
        self.tenant_id = tenant_id
        self.worker_id = worker_id
        self.lease_token = str(uuid.uuid4())
        self.duration_seconds = 30.0
        self.heartbeat_seconds = 10.0
        self._last_success = 0.0
        self._lost_reason = ""
        self._stop = threading.Event()
        self._abort = threading.Event()
        self._thread: threading.Thread | None = None

    def claim(self) -> dict:
        item = self.client.claim_execution_lease(
            self.run_id,
            {
                "tenant_id": self.tenant_id,
                "worker_id": self.worker_id,
                "lease_token": self.lease_token,
            },
        )
        self.duration_seconds = max(5.0, float(item.get("lease_duration_seconds") or 30))
        self.heartbeat_seconds = max(
            1.0,
            min(float(item.get("heartbeat_interval_seconds") or 10), self.duration_seconds / 2),
        )
        if item.get("acquired"):
            self._last_success = time.monotonic()
        return item

    def start(self) -> None:
        if self._last_success <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"run-lease-{self.run_id[:12]}",
            daemon=True,
        )
        self._thread.start()

    def assert_valid(self) -> None:
        if self.should_abort():
            raise ExecutionLeaseLost(self._lost_reason)

    def should_abort(self) -> bool:
        if self._lost_reason:
            self._abort.set()
            return True
        if self._last_success > 0 and time.monotonic() - self._last_success >= self.duration_seconds:
            self._lost_reason = "execution lease expired without a successful heartbeat"
            self._abort.set()
            return True
        return self._abort.is_set()

    @property
    def lost_reason(self) -> str:
        return self._lost_reason

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(2.0, self.heartbeat_seconds))
        if self._last_success <= 0:
            return
        try:
            self.client.release_execution_lease(
                self.run_id,
                {"tenant_id": self.tenant_id, "lease_token": self.lease_token},
            )
        except ControlPlaneCallbackError:
            pass

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            try:
                item = self.client.heartbeat_execution_lease(
                    self.run_id,
                    {"tenant_id": self.tenant_id, "lease_token": self.lease_token},
                )
                if not item.get("acquired"):
                    self._lost_reason = f"execution lease lost: {item.get('reason', 'unknown')}"
                    self._abort.set()
                    return
                self._last_success = time.monotonic()
            except ControlPlaneCallbackError:
                if time.monotonic() - self._last_success >= self.duration_seconds:
                    self._lost_reason = "execution lease heartbeat could not reach Control Plane before expiry"
                    self._abort.set()
                    return
