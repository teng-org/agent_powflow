from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from models.ai_gateway import ai_gateway
from models.api_pool import ApiEndpoint, api_pool_manager
from retrieval.store import kb_store


class RerankerUnavailable(RuntimeError):
    pass


class RerankerProvider:
    SUPPORTED_PROVIDERS = {"siliconflow", "jina", "cohere", "openai_compatible", "rerank_compatible"}

    @staticmethod
    def _results(body: dict[str, Any], item_count: int) -> list[tuple[int, float]]:
        raw_results = body.get("results")
        if not isinstance(raw_results, list):
            raw_results = body.get("data")
        if not isinstance(raw_results, list):
            raise ValueError("reranker response has no results array")
        parsed: list[tuple[int, float]] = []
        seen: set[int] = set()
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            index_value = raw.get("index", raw.get("document_index"))
            score_value = raw.get("relevance_score", raw.get("score"))
            try:
                index = int(index_value)
                score = float(score_value)
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= item_count or index in seen:
                continue
            seen.add(index)
            parsed.append((index, score))
        if not parsed:
            raise ValueError("reranker response contains no valid scored result")
        return parsed

    def _call_endpoint(
        self,
        endpoint: ApiEndpoint,
        *,
        tenant_id: str,
        kb_id: str,
        query: str,
        items: list[dict],
        top_n: int,
    ) -> dict:
        provider = str(endpoint.provider or "").strip().lower()
        if provider not in self.SUPPORTED_PROVIDERS:
            raise RuntimeError(f"unsupported reranker provider: {endpoint.provider}")
        documents = [str(item.get("text") or "")[:4000] for item in items]
        url = ai_gateway._endpoint_url(endpoint, "/rerank")
        payload = {
            "model": endpoint.model,
            "query": query,
            "documents": documents,
            "top_n": max(1, min(int(top_n or 1), len(documents))),
            "return_documents": False,
        }
        headers = {"Content-Type": "application/json"}
        api_key = ai_gateway._endpoint_api_key(endpoint)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        holder_id = str(uuid.uuid4())
        endpoint_id = str(endpoint.endpoint_id or f"{provider}:{endpoint.model}")
        slot_no = kb_store.acquire_api_request_slot(
            endpoint_id=endpoint_id,
            holder_id=holder_id,
            tenant_id=tenant_id,
            task_category="rerank",
            max_slots=max(1, int(endpoint.max_concurrency or 8)),
            wait_seconds=min(max(1, int(endpoint.timeout_seconds or 30)), 120),
            ttl_seconds=min(max(10, int(endpoint.timeout_seconds or 30) + 10), 600),
        )
        timeout_seconds = float(min(max(5, int(endpoint.timeout_seconds or 30)), 180))
        retry_max = max(0, min(int(endpoint.retry_max or 0), 3))
        body: dict[str, Any] = {}
        started = time.perf_counter()
        try:
            for attempt in range(retry_max + 1):
                try:
                    with httpx.Client(timeout=timeout_seconds) as client:
                        response = client.post(url, json=payload, headers=headers)
                        response.raise_for_status()
                        body = response.json()
                    ai_gateway._record_api_usage(
                        tenant_id=tenant_id,
                        kb_id=kb_id,
                        doc_id="",
                        pool="main",
                        task_category="rerank",
                        endpoint=endpoint,
                        api_key=api_key,
                        api_path="/rerank",
                        success=True,
                        usage_tokens=ai_gateway._parse_usage_tokens(body),
                    )
                    break
                except Exception as exc:
                    ai_gateway._record_api_usage(
                        tenant_id=tenant_id,
                        kb_id=kb_id,
                        doc_id="",
                        pool="main",
                        task_category="rerank",
                        endpoint=endpoint,
                        api_key=api_key,
                        api_path="/rerank",
                        success=False,
                        error_reason=str(exc),
                    )
                    status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    retryable = status is None or status == 429 or status >= 500
                    if attempt >= retry_max or not retryable:
                        raise
                    time.sleep(min(0.25 * (2 ** attempt), 1.0))
        finally:
            kb_store.release_api_request_slot(endpoint_id=endpoint_id, slot_no=slot_no, holder_id=holder_id)
        ranked: list[dict] = []
        for rank, (index, score) in enumerate(self._results(body, len(items)), start=1):
            item = dict(items[index])
            scores = dict(item.get("scores") or {})
            scores["pre_rerank"] = float(item.get("score") or 0.0)
            scores["rerank"] = score
            scores["rerank_rank"] = rank
            item["scores"] = scores
            item["score"] = score
            item["rank_method"] = f"{item.get('rank_method') or 'retrieval'}_rerank"
            ranked.append(item)
        return {
            "items": ranked,
            "meta": {
                "status": "succeeded",
                "endpoint_id": endpoint.endpoint_id,
                "provider": endpoint.provider,
                "model": endpoint.model,
                "candidate_count": len(items),
                "returned_count": len(ranked),
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            },
        }

    def rerank(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        query: str,
        items: list[dict],
        top_n: int,
    ) -> dict:
        if not items:
            return {"items": [], "meta": {"status": "skipped", "candidate_count": 0, "returned_count": 0}}
        candidates = api_pool_manager.list_candidates_for_tenant(tenant_id, task="rerank")
        if not candidates:
            raise RerankerUnavailable("no enabled rerank endpoint is configured for this tenant")
        errors: list[str] = []
        for endpoint in candidates:
            try:
                return self._call_endpoint(
                    endpoint,
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    query=query,
                    items=items,
                    top_n=top_n,
                )
            except Exception as exc:
                errors.append(f"{endpoint.endpoint_id}: {exc}")
        raise RuntimeError("; ".join(errors) or "all rerank endpoints failed")


reranker_provider = RerankerProvider()
