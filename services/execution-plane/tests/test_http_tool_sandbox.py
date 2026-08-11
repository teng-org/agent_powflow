from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from types import SimpleNamespace

import httpx


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from runtime.safe_http import HttpPolicy, SafeHttpExecutor, UnsafeHttpRequest
from runtime.workflow_runtime import execute_workflow


class _ToolFaultState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mode = "success"
        self.failures_before_success = 0
        self.delay_seconds = 0.0
        self.request_count = 0
        self.idempotency_keys: list[str] = []

    def next(self, key: str) -> int:
        with self.lock:
            self.request_count += 1
            self.idempotency_keys.append(key)
            return self.request_count


class _ToolHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:
        state: _ToolFaultState = self.server.fault_state  # type: ignore[attr-defined]
        request_no = state.next(str(self.headers.get("Idempotency-Key") or ""))
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if state.mode == "unauthorized":
            self._send(401, b'{"error":"unauthorized"}')
            return
        if state.mode == "flaky_429" and request_no <= state.failures_before_success:
            self._send(429, b'{"error":"rate limited"}')
            return
        if state.mode == "disconnect":
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
            return
        if state.delay_seconds:
            time.sleep(state.delay_seconds)
        self._send(200, b'{"ok":true,"source":"tool-sandbox"}')


class _InvocationStore:
    def __init__(self) -> None:
        self.cached_response = None
        self.claims: list[dict] = []
        self.failures: list[str] = []

    def claim(self, **kwargs):
        self.claims.append(kwargs)
        return SimpleNamespace(
            invocation_key=kwargs["invocation_key"],
            owner_token="owner",
            cached_response=self.cached_response,
        )

    def succeed(self, _claim, response) -> None:
        self.cached_response = response

    def fail(self, _claim, error: str) -> None:
        self.failures.append(error)


class HttpToolSandboxIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _ToolFaultState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ToolHandler)
        self.server.fault_state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/side-effect"
        self.executor = SafeHttpExecutor(
            policy=HttpPolicy(
                allowed_hosts=("127.0.0.1",),
                allow_http=True,
                allow_private_networks=True,
                max_timeout_seconds=2,
            )
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def _payload(self, *, max_attempts: int = 2) -> dict:
        return {
            "tenant_id": "tenant-a",
            "run_id": "run-sandbox",
            "workflow_checksum": "b" * 64,
            "workflow_version_no": 1,
            "workflow_dsl": {
                "nodes": [
                    {"id": "start", "type": "start"},
                    {
                        "id": "tool",
                        "type": "tool.http",
                        "config": {
                            "url": self.url,
                            "method": "POST",
                            "body": {"message": "${text}"},
                            "timeout_seconds": 1,
                            "retry": {
                                "max_attempts": max_attempts,
                                "initial_delay_ms": 0,
                                "max_delay_ms": 0,
                                "retry_on": "transient",
                            },
                        },
                    },
                    {"id": "end", "type": "end"},
                ]
            },
            "input_payload": {"text": "hello"},
        }

    def test_429_retries_with_the_same_idempotency_key(self) -> None:
        self.state.mode = "flaky_429"
        self.state.failures_before_success = 1
        store = _InvocationStore()
        result = execute_workflow(self._payload(), invocation_store=store, http_executor=self.executor)
        self.assertEqual(2, self.state.request_count)
        self.assertEqual(1, len(set(self.state.idempotency_keys)))
        self.assertTrue(self.state.idempotency_keys[0])
        self.assertEqual(1, result["metrics"]["node_retry_count"])
        self.assertEqual(1, len(store.failures))

    def test_401_is_a_hard_failure_and_is_not_retried(self) -> None:
        self.state.mode = "unauthorized"
        with self.assertRaises(UnsafeHttpRequest) as failure:
            execute_workflow(self._payload(max_attempts=3), invocation_store=_InvocationStore(), http_executor=self.executor)
        self.assertEqual(401, failure.exception.status_code)
        self.assertFalse(failure.exception.retryable)
        self.assertEqual(1, self.state.request_count)

    def test_successful_side_effect_is_cached_across_redelivery(self) -> None:
        store = _InvocationStore()
        first = execute_workflow(self._payload(), invocation_store=store, http_executor=self.executor)
        second = execute_workflow(self._payload(), invocation_store=store, http_executor=self.executor)
        self.assertEqual(1, self.state.request_count)
        self.assertEqual(first["output_payload"]["answer"], second["output_payload"]["answer"])
        self.assertEqual(1, second["metrics"]["tool_cache_hits"])

    def test_real_transport_disconnect_is_retryable(self) -> None:
        self.state.mode = "disconnect"
        with self.assertRaises(httpx.TransportError):
            execute_workflow(self._payload(max_attempts=2), invocation_store=_InvocationStore(), http_executor=self.executor)
        self.assertEqual(2, self.state.request_count)

    def test_real_timeout_is_retryable_and_bounded(self) -> None:
        self.state.delay_seconds = 1.3
        started = time.monotonic()
        with self.assertRaises(httpx.TimeoutException):
            execute_workflow(self._payload(max_attempts=2), invocation_store=_InvocationStore(), http_executor=self.executor)
        self.assertEqual(2, self.state.request_count)
        self.assertLess(time.monotonic() - started, 2.8)


if __name__ == "__main__":
    unittest.main()
