from __future__ import annotations

import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import httpx


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from models.api_pool import ApiEndpoint
from models.reranker_provider import RerankerProvider, RerankerUnavailable


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.request = httpx.Request("POST", "https://provider.example/v1/rerank")

    def raise_for_status(self):
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("failed", request=self.request, response=response)

    def json(self):
        return self._body


class _Client:
    outcomes: list[_Response] = []
    payloads: list[dict] = []

    def __init__(self, timeout):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def post(self, url, json, headers):
        self.payloads.append(json)
        return self.outcomes.pop(0)


class RerankerProviderTest(TestCase):
    def setUp(self):
        _Client.outcomes = []
        _Client.payloads = []
        self.provider = RerankerProvider()
        self.endpoint = ApiEndpoint(
            endpoint_id="rerank-test", name="Rerank", provider="siliconflow",
            base_url="https://provider.example/v1", model="BAAI/bge-reranker-v2-m3",
            task="rerank", api_key="secret", retry_max=0,
        )

    def test_real_provider_order_and_scores_are_preserved(self):
        _Client.outcomes = [_Response(200, {"results": [
            {"index": 1, "relevance_score": 0.96}, {"index": 0, "relevance_score": 0.31},
        ]})]
        with (
            patch("models.reranker_provider.api_pool_manager.list_candidates_for_tenant", return_value=[self.endpoint]),
            patch("models.reranker_provider.httpx.Client", _Client),
            patch("models.reranker_provider.kb_store.acquire_api_request_slot", return_value=2),
            patch("models.reranker_provider.kb_store.release_api_request_slot"),
            patch("models.reranker_provider.ai_gateway._record_api_usage"),
        ):
            result = self.provider.rerank(
                tenant_id="tenant", kb_id="kb", query="query",
                items=[
                    {"chunk_id": "a", "text": "first", "score": 0.8, "scores": {"rrf": 0.03}, "rank_method": "rrf"},
                    {"chunk_id": "b", "text": "second", "score": 0.7, "scores": {"rrf": 0.02}, "rank_method": "rrf"},
                ],
                top_n=2,
            )
        self.assertEqual(["b", "a"], [item["chunk_id"] for item in result["items"]])
        self.assertEqual(0.96, result["items"][0]["scores"]["rerank"])
        self.assertEqual(0.7, result["items"][0]["scores"]["pre_rerank"])
        self.assertEqual("rrf_rerank", result["items"][0]["rank_method"])
        self.assertEqual("BAAI/bge-reranker-v2-m3", _Client.payloads[0]["model"])

    def test_missing_endpoint_is_explicitly_unavailable(self):
        with patch("models.reranker_provider.api_pool_manager.list_candidates_for_tenant", return_value=[]):
            with self.assertRaises(RerankerUnavailable):
                self.provider.rerank(
                    tenant_id="tenant", kb_id="kb", query="query",
                    items=[{"chunk_id": "a", "text": "first"}], top_n=1,
                )
