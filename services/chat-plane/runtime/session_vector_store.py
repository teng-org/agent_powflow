from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from .config import get_int, get_setting


class SessionVectorStore:
    def __init__(self) -> None:
        self.base_url = str(get_setting("qdrant_url", "http://qdrant:6333")).rstrip("/")
        self.collection = str(get_setting("chat_session_vector_collection", "chat_session_segments")).strip()
        if not self.collection:
            self.collection = "chat_session_segments"
        self.timeout_seconds = int(get_setting("chat_session_vector_timeout_seconds", 20))
        self._ensured_dim: Optional[int] = None

    async def ensure_collection(self, vector_dim: int) -> bool:
        if vector_dim <= 0:
            return False
        if self._ensured_dim == vector_dim:
            return True
        url = f"{self.base_url}/collections/{self.collection}"
        payload = {
            "vectors": {
                "size": int(vector_dim),
                "distance": "Cosine",
            }
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.put(url, json=payload)
            # 200 created/updated; 409 if exists with same params in some versions.
            if resp.status_code not in (200, 201, 202, 409):
                return False
        self._ensured_dim = vector_dim
        return True

    async def upsert_segment(self, segment_id: str, vector: List[float], payload: Dict[str, Any]) -> bool:
        if not vector:
            return False
        ok = await self.ensure_collection(len(vector))
        if not ok:
            return False
        url = f"{self.base_url}/collections/{self.collection}/points?wait=false"
        body = {
            "points": [
                {
                    "id": str(segment_id),
                    "vector": [float(x) for x in vector],
                    "payload": payload,
                }
            ]
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.put(url, json=body)
            return resp.status_code in (200, 201, 202)

    async def search(
        self,
        vector: List[float],
        tenant_id: str,
        actor_id: str,
        *,
        limit: int = 4,
        session_id: Optional[str] = None,
        exclude_session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not vector:
            return []
        url = f"{self.base_url}/collections/{self.collection}/points/search"
        flt: Dict[str, Any] = {
            "must": [
                {"key": "tenant_id", "match": {"value": str(tenant_id)}},
                {"key": "actor_id", "match": {"value": str(actor_id)}},
            ]
        }
        if session_id:
            flt["must"].append({"key": "session_id", "match": {"value": str(session_id)}})
        if exclude_session_id:
            flt["must_not"] = [{"key": "session_id", "match": {"value": str(exclude_session_id)}}]
        body = {
            "vector": [float(x) for x in vector],
            "limit": max(1, int(limit)),
            "with_payload": True,
            "with_vector": False,
            "filter": flt,
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=body)
            if resp.status_code not in (200, 201):
                return []
            obj = resp.json() or {}
        items = obj.get("result") or []
        out: List[Dict[str, Any]] = []
        for it in items:
            payload = it.get("payload") or {}
            out.append(
                {
                    "segment_id": str(it.get("id") or payload.get("segment_id") or ""),
                    "session_id": str(payload.get("session_id") or ""),
                    "source_message_ids": list(payload.get("source_message_ids") or []),
                    "text": str(payload.get("text") or ""),
                    "text_preview": str(payload.get("text_preview") or "")[:220],
                    "token_estimate": int(payload.get("token_estimate") or 0),
                    "keywords": list(payload.get("keywords") or []),
                    "score": float(it.get("score") or 0.0),
                    "source": "vector",
                }
            )
        return out


session_vector_store = SessionVectorStore()
