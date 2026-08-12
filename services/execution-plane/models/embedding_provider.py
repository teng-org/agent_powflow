import hashlib
import math
import os


class EmbeddingProvider:
    def __init__(self) -> None:
        self.default_dimensions = 256
        self.embedding_mode = (os.getenv("EMBEDDING_MODE", "api_strict") or "api_strict").strip().lower()

    def max_input_chars(self, tenant_id: str) -> int | None:
        if self.embedding_mode == "local_only":
            return None
        from models.ai_gateway import ai_gateway
        endpoint = ai_gateway._resolve_endpoint(tenant_id=tenant_id, pool="main", task="embedding")
        if not endpoint:
            return None
        if endpoint.provider not in {"siliconflow", "openai", "openai_compatible"}:
            return None
        raw = (os.getenv("EMBEDDING_MAX_CHARS", "4000") or "").strip()
        try:
            value = int(raw)
        except Exception:
            value = 4000
        if value <= 0:
            return None
        return value

    def local_embed(self, text: str, dimensions: int | None = None) -> list[float]:
        size = int(dimensions or self.default_dimensions)
        vector = [0.0] * size
        tokens = [token for token in (text or "").lower().split() if token]
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "big") % size] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed(
        self,
        tenant_id: str,
        text: str,
        task: str = "embedding",
        kb_id: str = "",
        doc_id: str = "",
        local_dimensions: int | None = None,
    ) -> list[float]:
        fallback_dimensions = int(local_dimensions or self.default_dimensions)
        mode = self.embedding_mode
        if mode == "local_only":
            return self.local_embed(text, dimensions=fallback_dimensions)

        from models.ai_gateway import ai_gateway
        strict = True
        return ai_gateway.embed_text(
            tenant_id=tenant_id,
            text=text,
            pool="main",
            task=task,
            strict=strict,
            local_dimensions=fallback_dimensions,
            kb_id=kb_id,
            doc_id=doc_id,
        )


embedding_provider = EmbeddingProvider()
