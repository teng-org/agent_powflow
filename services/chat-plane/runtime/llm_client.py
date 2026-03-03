from __future__ import annotations

import json
from typing import AsyncGenerator, Dict, List, Tuple

import httpx

from .api_pool import chat_api_pool_manager
from .config import get_setting


class LlmClient:
    def __init__(self) -> None:
        self.base_url = str(get_setting("chat_llm_base_url", "https://api.siliconflow.cn/v1")).rstrip("/")
        self.model = str(get_setting("chat_llm_model", "deepseek-ai/DeepSeek-V3.2"))
        chat_key = str(get_setting("chat_llm_api_key", "") or "").strip()
        kb_key = str(get_setting("siliconflow_api_key", "") or "").strip()
        self.api_key = chat_key or kb_key
        self.timeout_seconds = int(get_setting("chat_llm_timeout_seconds", 120))

    def _headers(self, api_key: str) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def resolve_request_target(self) -> Tuple[str, str, str, str]:
        endpoint = chat_api_pool_manager.get_default("chat_llm")
        base_url = self.base_url
        model = self.model
        api_key = self.api_key
        key_source = "runtime_config.chat_llm_api_key_or_siliconflow_api_key"
        if endpoint:
            base_url = endpoint.base_url.rstrip("/")
            model = endpoint.model
            endpoint_key = str(endpoint.api_key or "").strip()
            if endpoint_key:
                api_key = endpoint_key
                key_source = f"chat_api_pool.endpoint:{endpoint.endpoint_id}"
            else:
                key_source = f"chat_api_pool.endpoint:{endpoint.endpoint_id}(fallback-runtime-key)"
        return base_url, model, api_key, key_source

    def resolve_embedding_target(self) -> Tuple[str, str, str, str]:
        endpoint = chat_api_pool_manager.get_default("chat_embedding")
        base_url = str(get_setting("chat_embedding_base_url", self.base_url)).rstrip("/")
        model = str(get_setting("chat_embedding_model", "BAAI/bge-m3"))
        api_key = str(get_setting("chat_embedding_api_key", "") or "").strip() or self.api_key
        key_source = "runtime_config.chat_embedding_api_key_or_siliconflow_api_key"
        if endpoint:
            base_url = endpoint.base_url.rstrip("/")
            model = endpoint.model
            endpoint_key = str(endpoint.api_key or "").strip()
            if endpoint_key:
                api_key = endpoint_key
                key_source = f"chat_api_pool.endpoint:{endpoint.endpoint_id}"
            else:
                key_source = f"chat_api_pool.endpoint:{endpoint.endpoint_id}(fallback-runtime-key)"
        return base_url, model, api_key, key_source

    def runtime_meta(self) -> Dict[str, str]:
        base_url, model, api_key, key_source = self.resolve_request_target()
        emb_base_url, emb_model, emb_api_key, emb_key_source = self.resolve_embedding_target()
        return {
            "base_url": base_url,
            "model": model,
            "key_source": key_source,
            "api_key_configured": "yes" if bool(api_key) else "no",
            "embedding_base_url": emb_base_url,
            "embedding_model": emb_model,
            "embedding_key_source": emb_key_source,
            "embedding_api_key_configured": "yes" if bool(emb_api_key) else "no",
        }

    async def stream_chat(self, messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
        base_url, model, api_key, _ = self.resolve_request_target()
        # If key missing, return deterministic mock stream for dev.
        if not api_key:
            mock = "当前未配置 chat_llm_api_key，已返回 mock 回复。"
            for token in mock.split("，"):
                yield token + "，"
            return

        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=self._headers(api_key), json=payload) as resp:
                resp.raise_for_status()
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except Exception:
                        continue
                    delta = (((obj.get("choices") or [{}])[0]).get("delta") or {}).get("content")
                    if delta:
                        yield str(delta)

    async def complete_text(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int = 400,
        temperature: float = 0.2,
    ) -> str:
        base_url, model, api_key, _ = self.resolve_request_target()
        if not api_key:
            # deterministic fallback for dev without configured keys
            merged = "\n".join(str(m.get("content") or "") for m in messages[-3:])
            return (merged[:400] + "...") if len(merged) > 400 else merged
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=self._headers(api_key), json=payload)
            resp.raise_for_status()
            obj = resp.json()
            content = (((obj.get("choices") or [{}])[0]).get("message") or {}).get("content")
            return str(content or "").strip()

    async def embed_text(self, text: str) -> List[float]:
        base_url, model, api_key, _ = self.resolve_embedding_target()
        t = str(text or "").strip()
        if not t:
            return []
        if not api_key:
            # deterministic local fallback for dev (small pseudo-vector)
            import hashlib

            digest = hashlib.sha256(t.encode("utf-8")).digest()
            out: List[float] = []
            for i in range(0, len(digest), 4):
                part = digest[i : i + 4]
                if len(part) < 4:
                    break
                n = int.from_bytes(part, byteorder="big", signed=False)
                out.append(((n % 20000) / 10000.0) - 1.0)
            return out
        url = f"{base_url}/embeddings"
        payload = {"model": model, "input": t}
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=self._headers(api_key), json=payload)
            resp.raise_for_status()
            obj = resp.json()
            data = obj.get("data") or []
            if not data:
                return []
            emb = (data[0] or {}).get("embedding") or []
            return [float(x) for x in emb]


llm_client = LlmClient()
