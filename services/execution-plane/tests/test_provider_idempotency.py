import pathlib
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.provider_idempotency import canonical_hash, stable_invocation_key


class ProviderIdempotencyTest(unittest.TestCase):
    def test_key_is_stable_for_semantic_identity(self):
        args = dict(tenant_id="tenant-a", run_id="run-a", workflow_checksum="abc", node_id="llm-1", operation="chat")
        self.assertEqual(stable_invocation_key(**args), stable_invocation_key(**args))
        self.assertNotEqual(stable_invocation_key(**args), stable_invocation_key(**{**args, "node_id": "llm-2"}))

    def test_canonical_hash_ignores_object_key_order(self):
        self.assertEqual(canonical_hash({"b": 2, "a": 1}), canonical_hash({"a": 1, "b": 2}))

    def test_provider_http_call_is_interrupted_when_cancelled(self):
        try:
            from models.ai_gateway import AiGateway
        except ModuleNotFoundError as exc:
            if exc.name == "psycopg":
                self.skipTest("local lightweight test environment does not include psycopg")
            raise

        closed = threading.Event()
        cancel = threading.Event()

        class BlockingClient:
            def __init__(self, timeout): self.timeout = timeout
            def post(self, *args, **kwargs):
                closed.wait(2)
                raise RuntimeError("client closed")
            def close(self): closed.set()

        gateway = AiGateway()
        timer = threading.Timer(0.05, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with patch("models.ai_gateway.httpx.Client", BlockingClient):
                with self.assertRaisesRegex(RuntimeError, "TASK_CANCELLED"):
                    gateway._post_json_interruptible(
                        url="https://provider.invalid", payload={}, headers={}, timeout_seconds=60,
                        should_cancel=cancel.is_set,
                    )
        finally:
            timer.cancel()
        self.assertTrue(closed.is_set())
        self.assertLess(time.monotonic() - started, 0.6)


if __name__ == "__main__":
    unittest.main()
