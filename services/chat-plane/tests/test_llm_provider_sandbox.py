from __future__ import annotations

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx


CHAT_ROOT = Path(__file__).resolve().parents[1]
if str(CHAT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHAT_ROOT))

from runtime.api_pool import ChatApiEndpoint, ChatApiPoolManager
from runtime.llm_client import LlmClient


class _FaultState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mode = "success"
        self.failures_before_success = 0
        self.delay_seconds = 0.0
        self.request_count = 0
        self.inflight = 0
        self.max_inflight = 0
        self.authorization_headers: list[str] = []

    def begin(self, authorization: str) -> int:
        with self.lock:
            self.request_count += 1
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.authorization_headers.append(authorization)
            return self.request_count

    def end(self) -> None:
        with self.lock:
            self.inflight -= 1


class _SandboxHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str = "application/json", **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self) -> None:
        state: _FaultState = self.server.fault_state  # type: ignore[attr-defined]
        request_no = state.begin(str(self.headers.get("Authorization") or ""))
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            if state.mode == "unauthorized":
                self._send(401, b'{"error":"invalid api key"}')
                return
            if state.mode == "flaky_429" and request_no <= state.failures_before_success:
                self._send(429, b'{"error":"rate limited"}', Retry_After="0")
                return
            if state.mode == "disconnect" and request_no <= state.failures_before_success:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if state.delay_seconds:
                time.sleep(state.delay_seconds)
            if bool(payload.get("stream")):
                body = (
                    b'data: {"choices":[{"delta":{"content":"sandbox "},"finish_reason":null}]}\n\n'
                    b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n'
                    b'data: [DONE]\n\n'
                )
                self._send(200, body, "text/event-stream")
            else:
                body = json.dumps(
                    {
                        "choices": [{"message": {"content": "sandbox ok"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    }
                ).encode()
                self._send(200, body)
        finally:
            state.end()


class LlmProviderSandboxIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = _FaultState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _SandboxHandler)
        self.server.fault_state = self.state  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pool = ChatApiPoolManager()
        self.pool._postgres_dsn = ""
        self.pool._state_path = Path(self.temp_dir.name) / "pool.json"
        endpoint = self.pool.upsert_endpoint(
            ChatApiEndpoint(
                endpoint_id="sandbox",
                name="Local fault sandbox",
                provider="openai_compatible",
                base_url=f"http://127.0.0.1:{self.server.server_port}",
                model="sandbox-model",
                task="chat_llm",
                api_key="sandbox-secret",
                max_concurrency=4,
                retry_max=1,
            )
        )
        endpoint.timeout_seconds = 1

        settings = {
            "chat_llm_timeout_seconds": 1,
            "chat_request_retry_max": 1,
            "chat_stream_retry_max": 1,
            "chat_provider_retry_initial_ms": 0,
            "chat_provider_retry_max_ms": 0,
            "chat_stream_partial_success_min_chars": 1200,
            "chat_http_keepalive_enabled": False,
        }

        def setting(name: str, default=None):
            return settings.get(name, default)

        self.patchers = [
            patch.dict(os.environ, {"CHAT_PROVIDER_MODE": "api_strict"}),
            patch("runtime.llm_client.chat_api_pool_manager", self.pool),
            patch("runtime.llm_client.get_setting", side_effect=setting),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.client = LlmClient()

    def tearDown(self) -> None:
        asyncio.run(self.client.aclose())
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp_dir.cleanup()

    @staticmethod
    def _messages() -> list[dict[str, str]]:
        return [{"role": "user", "content": "sandbox request"}]

    def test_non_stream_429_retries_and_preserves_auth(self) -> None:
        self.state.mode = "flaky_429"
        self.state.failures_before_success = 1
        result = asyncio.run(self.client.complete_text(self._messages(), tenant_id="tenant-a"))
        self.assertEqual("sandbox ok", result)
        self.assertEqual(2, self.state.request_count)
        self.assertEqual(["Bearer sandbox-secret"] * 2, self.state.authorization_headers)
        logs = self.pool.list_call_logs(endpoint_id="sandbox", limit=10)
        self.assertEqual({200, 429}, {row["status_code"] for row in logs})

    def test_stream_429_is_retryable_and_status_is_audited(self) -> None:
        self.state.mode = "flaky_429"
        self.state.failures_before_success = 1

        async def collect() -> str:
            return "".join([token async for token in self.client.stream_chat(self._messages(), tenant_id="tenant-a")])

        self.assertEqual("sandbox ok", asyncio.run(collect()))
        self.assertEqual(2, self.state.request_count)
        logs = self.pool.list_call_logs(endpoint_id="sandbox", limit=10)
        self.assertEqual({200, 429}, {row["status_code"] for row in logs})

    def test_authentication_failure_is_not_retried(self) -> None:
        self.state.mode = "unauthorized"
        with self.assertRaises(httpx.HTTPStatusError) as failure:
            asyncio.run(self.client.complete_text(self._messages(), tenant_id="tenant-a"))
        self.assertEqual(401, failure.exception.response.status_code)
        self.assertEqual(1, self.state.request_count)

    def test_stream_disconnect_retries_on_real_transport(self) -> None:
        self.state.mode = "disconnect"
        self.state.failures_before_success = 1

        async def collect() -> str:
            return "".join([token async for token in self.client.stream_chat(self._messages(), tenant_id="tenant-a")])

        self.assertEqual("sandbox ok", asyncio.run(collect()))
        self.assertEqual(2, self.state.request_count)

    def test_timeout_retries_then_surfaces_timeout(self) -> None:
        self.state.delay_seconds = 1.3
        with self.assertRaises(httpx.TimeoutException):
            asyncio.run(self.client.complete_text(self._messages(), tenant_id="tenant-a"))
        self.assertEqual(2, self.state.request_count)

    def test_configured_provider_concurrency_is_not_globally_serialized(self) -> None:
        self.state.delay_seconds = 0.15

        async def invoke_twice() -> list[str]:
            return await asyncio.gather(
                self.client.complete_text(self._messages(), tenant_id="tenant-a"),
                self.client.complete_text(self._messages(), tenant_id="tenant-a"),
            )

        self.assertEqual(["sandbox ok", "sandbox ok"], asyncio.run(invoke_twice()))
        self.assertGreaterEqual(self.state.max_inflight, 2)

    def test_exact_endpoint_probe_reports_stream_timings(self) -> None:
        result = asyncio.run(self.client.probe_endpoint("sandbox", max_tokens=8, total_timeout_seconds=5))

        self.assertEqual("sandbox", result["endpoint_id"])
        self.assertEqual("PASS", result["result_status"])
        self.assertGreater(result["total_ms"], 0)
        self.assertGreater(result["first_token_ms"], 0)
        self.assertEqual(1, self.state.request_count)


if __name__ == "__main__":
    unittest.main()
