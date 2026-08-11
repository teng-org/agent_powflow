from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import httpx


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from models.ai_gateway import AiGateway
from models.api_pool import ApiEndpoint


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.request = httpx.Request("POST", "https://provider.example/v1/embeddings")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("provider rejected request", request=self.request, response=response)

    def json(self) -> dict:
        return self._body


class _Client:
    outcomes: list[object] = []
    timeouts: list[float] = []
    calls = 0

    def __init__(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict, headers: dict):
        del url, json, headers
        type(self).calls += 1
        outcome = type(self).outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class AiGatewayEmbeddingRetryTest(TestCase):
    def setUp(self) -> None:
        _Client.outcomes = []
        _Client.timeouts = []
        _Client.calls = 0
        self.gateway = AiGateway()
        self.endpoint = ApiEndpoint(
            endpoint_id="embedding-test",
            name="Embedding Test",
            provider="openai_compatible",
            base_url="https://provider.example/v1",
            model="embedding-model",
            task="embedding",
            api_key="secret",
            timeout_seconds=75,
            retry_max=2,
        )

    def _call(self) -> list[float]:
        with (
            patch("models.ai_gateway.httpx.Client", _Client),
            patch("models.ai_gateway.kb_store.acquire_api_request_slot", return_value=1),
            patch("models.ai_gateway.kb_store.release_api_request_slot"),
            patch.object(self.gateway, "_record_api_usage"),
            patch("models.ai_gateway.time.sleep"),
        ):
            return self.gateway._embed_openai_compatible(
                self.endpoint,
                "text",
                tenant_id="tenant",
                kb_id="kb",
                doc_id="doc",
                pool="main",
                task_category="doc_embedding",
            )

    def test_read_timeout_is_retried_with_endpoint_timeout(self):
        request = httpx.Request("POST", "https://provider.example/v1/embeddings")
        _Client.outcomes = [
            httpx.ReadTimeout("slow provider", request=request),
            _Response(200, {"data": [{"embedding": [0.1, 0.2]}], "usage": {"total_tokens": 2}}),
        ]

        self.assertEqual([0.1, 0.2], self._call())
        self.assertEqual(2, _Client.calls)
        self.assertEqual([75.0, 75.0], _Client.timeouts)

    def test_non_retryable_4xx_fails_once(self):
        _Client.outcomes = [_Response(400, {"error": "bad input"})]

        with self.assertRaises(httpx.HTTPStatusError):
            self._call()
        self.assertEqual(1, _Client.calls)
