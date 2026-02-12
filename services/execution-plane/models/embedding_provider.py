import hashlib
import math
import os

import httpx

from models.api_pool import api_pool_manager


class EmbeddingProvider:
    def __init__(self) -> None:
        self.default_dimensions = 256
        self.embedding_mode = (os.getenv("EMBEDDING_MODE", "api_prefer") or "api_prefer").strip().lower()
        self.api_pool_config = os.getenv("API_POOL_DEFAULT_CONFIG", "/app/services/execution-plane/config/api_pool.default.json")
        self._api_pool_bootstrapped = False

    def _ensure_api_pool_loaded(self) -> None:
        if self._api_pool_bootstrapped:
            return
        try:
            api_pool_manager.bootstrap(os.path.abspath(self.api_pool_config))
            self._api_pool_bootstrapped = True
        except Exception:
            # keep lazy retry behavior on next call
            self._api_pool_bootstrapped = False

    def _local_embedding(self, text: str, dimensions: int = 256) -> list[float]:
        """Deterministic local fallback embedding for dev/testing."""
        vec = [0.0] * dimensions
        tokens = [t for t in text.lower().split() if t]
        if not tokens:
            return vec

        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()  # nosec B324
            idx = int.from_bytes(digest[:4], "big") % dimensions
            vec[idx] += 1.0

        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _siliconflow_embed(
        self, endpoint_base: str, api_key: str, model: str, text: str, dimensions: int | None = None
    ) -> list[float]:
        url = endpoint_base.rstrip("/") + "/embeddings"
        payload: dict = {
            "model": model,
            "input": text,
            "encoding_format": "float",
        }
        if dimensions is not None:
            payload["dimensions"] = dimensions

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            body = resp.json()

        data = body.get("data", [])
        if not data:
            raise ValueError("SiliconFlow embedding response has empty data")
        vector = data[0].get("embedding", [])
        if not vector:
            raise ValueError("SiliconFlow embedding vector missing")
        return [float(x) for x in vector]

    def max_input_chars(self, tenant_id: str) -> int | None:
        """
        Conservative guard for remote embedding APIs.
        This is char-based (not exact token count), used only to avoid 413.
        """
        self._ensure_api_pool_loaded()
        endpoint = api_pool_manager.get_default_for_tenant(tenant_id=tenant_id, task="embedding")
        if not endpoint:
            return None
        if endpoint.provider != "siliconflow":
            return None
        raw = (os.getenv("EMBEDDING_MAX_CHARS", "380") or "").strip()
        try:
            value = int(raw)
        except Exception:
            value = 380
        if value <= 0:
            return None
        return value

    def embed(self, tenant_id: str, text: str) -> list[float]:
        mode = self.embedding_mode
        if mode == "local_only":
            return self._local_embedding(text, self.default_dimensions)

        self._ensure_api_pool_loaded()
        endpoint = api_pool_manager.get_default_for_tenant(tenant_id=tenant_id, task="embedding")
        if not endpoint:
            if mode == "api_strict":
                raise RuntimeError("embedding_mode=api_strict but no embedding endpoint is configured")
            return self._local_embedding(text, self.default_dimensions)

        api_key = endpoint.api_key or os.getenv("SILICONFLOW_API_KEY", "")
        if endpoint.provider == "siliconflow" and api_key:
            try:
                return self._siliconflow_embed(
                    endpoint_base=endpoint.base_url,
                    api_key=api_key,
                    model=endpoint.model,
                    text=text,
                )
            except Exception:
                if mode == "api_strict":
                    raise
                return self._local_embedding(text, self.default_dimensions)

        if mode == "api_strict":
            raise RuntimeError("embedding_mode=api_strict but current embedding endpoint is invalid")
        return self._local_embedding(text, self.default_dimensions)


embedding_provider = EmbeddingProvider()
