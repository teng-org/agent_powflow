from __future__ import annotations

from typing import Dict

import httpx

from .config import get_int, get_setting


async def search_kb(
    tenant_id: str,
    actor_id: str,
    trace_id: str,
    kb_id: str,
    query: str,
    top_k: int,
    rank_method: str = "vector",
    rerank_enabled: bool = False,
    rerank_k: int = 30,
    rerank_required: bool = False,
) -> Dict:
    base = str(get_setting("execution_api_base_url", "http://execution-api:8090")).rstrip("/")
    url = f"{base}/api/v1/retrieval/search"
    payload = {
        "kb_id": kb_id,
        "query": query,
        "top_k": top_k,
        "final_k": top_k,
        "candidate_k": min(max(top_k * 5, 20), 200),
        "rank_method": rank_method,
        "max_chunks_per_doc": 3,
        "context_window": 1,
        "rerank_enabled": bool(rerank_enabled),
        "rerank_k": max(top_k, min(int(rerank_k or 30), 100)),
        "rerank_required": bool(rerank_required),
    }
    headers = {
        "X-Tenant-Id": tenant_id,
        "X-Actor-Id": actor_id,
        "X-Trace-Id": trace_id,
        "X-Source-Service": "chat-plane",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(get_int("chat_retrieval_timeout_seconds", 30))
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            detail = resp.text[:1200]
            raise RuntimeError(f"retrieval search failed: HTTP {resp.status_code} {detail}")
        data = resp.json()
    return {
        "evidence": list(data.get("evidence") or []),
        "retrieval": dict(data.get("retrieval") or {}),
    }
