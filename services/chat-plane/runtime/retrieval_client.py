from __future__ import annotations

from typing import Dict, List

import httpx

from .config import get_int, get_setting


async def search_kb(tenant_id: str, actor_id: str, trace_id: str, kb_id: str, query: str, top_k: int) -> List[Dict]:
    base = str(get_setting("execution_api_base_url", "http://execution-api:8090")).rstrip("/")
    url = f"{base}/api/v1/retrieval/search"
    payload = {"kb_id": kb_id, "query": query, "top_k": top_k}
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
        resp.raise_for_status()
        data = resp.json()
    return list(data.get("evidence") or [])
