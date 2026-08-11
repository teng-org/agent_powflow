import pathlib
import sys
import time
import unittest


SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from runtime.execution_lease import ExecutionLeaseGuard, ExecutionLeaseLost


class FakeControlPlaneClient:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.released = []

    def claim_execution_lease(self, run_id: str, payload: dict) -> dict:
        return {
            "run_id": run_id,
            "acquired": self.acquired,
            "reason": "ACQUIRED" if self.acquired else "LEASE_HELD",
            "lease_duration_seconds": 30,
            "heartbeat_interval_seconds": 10,
        }

    def heartbeat_execution_lease(self, run_id: str, payload: dict) -> dict:
        return {"acquired": True, "reason": "RENEWED"}

    def release_execution_lease(self, run_id: str, payload: dict) -> dict:
        self.released.append((run_id, payload))
        return {"acquired": False, "reason": "RELEASED"}


class ExecutionLeaseGuardTest(unittest.TestCase):
    def test_claim_and_release_use_same_opaque_token(self) -> None:
        client = FakeControlPlaneClient()
        guard = ExecutionLeaseGuard(client, "run-1", "tenant-a", "worker-a")

        result = guard.claim()
        guard.close()

        self.assertTrue(result["acquired"])
        self.assertEqual(1, len(client.released))
        self.assertEqual(guard.lease_token, client.released[0][1]["lease_token"])

    def test_rejected_claim_does_not_release_another_workers_lease(self) -> None:
        client = FakeControlPlaneClient(acquired=False)
        guard = ExecutionLeaseGuard(client, "run-1", "tenant-a", "worker-b")

        result = guard.claim()
        guard.close()

        self.assertFalse(result["acquired"])
        self.assertEqual([], client.released)

    def test_expired_local_heartbeat_fails_closed(self) -> None:
        guard = ExecutionLeaseGuard(FakeControlPlaneClient(), "run-1", "tenant-a", "worker-a")
        guard._last_success = time.monotonic() - 6
        guard.duration_seconds = 5

        with self.assertRaises(ExecutionLeaseLost):
            guard.assert_valid()
        self.assertTrue(guard.should_abort())

    def test_rejected_heartbeat_sets_active_abort_signal(self) -> None:
        class RejectingHeartbeatClient(FakeControlPlaneClient):
            def heartbeat_execution_lease(self, run_id: str, payload: dict) -> dict:
                return {"acquired": False, "reason": "RUN_TERMINAL"}

        guard = ExecutionLeaseGuard(RejectingHeartbeatClient(), "run-1", "tenant-a", "worker-a")
        guard.claim()
        guard.heartbeat_seconds = 0.01
        guard.start()
        deadline = time.monotonic() + 1
        while not guard.should_abort() and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            self.assertTrue(guard.should_abort())
            with self.assertRaisesRegex(ExecutionLeaseLost, "RUN_TERMINAL"):
                guard.assert_valid()
        finally:
            guard.close()


if __name__ == "__main__":
    unittest.main()
