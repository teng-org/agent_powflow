import os

from models.ai_gateway import ai_gateway


class EmbeddingProvider:
    def __init__(self) -> None:
        self.default_dimensions = 256
        self.embedding_mode = (os.getenv("EMBEDDING_MODE", "api_prefer") or "api_prefer").strip().lower()

    def max_input_chars(self, tenant_id: str) -> int | None:
        endpoint = ai_gateway._resolve_endpoint(tenant_id=tenant_id, pool="main", task="embedding")
        if not endpoint:
            return None
        if endpoint.provider not in {"siliconflow", "openai", "openai_compatible"}:
            return None
        raw = (os.getenv("EMBEDDING_MAX_CHARS", "380") or "").strip()
        try:
            value = int(raw)
        except Exception:
            value = 380
        if value <= 0:
            return None
        return value

    def embed(
        self,
        tenant_id: str,
        text: str,
        task: str = "embedding",
        kb_id: str = "",
        doc_id: str = "",
    ) -> list[float]:
        mode = self.embedding_mode
        if mode == "local_only":
            return ai_gateway.embed_text(
                tenant_id=tenant_id,
                text=text,
                pool="main",
                task=task,
                strict=False,
                local_dimensions=self.default_dimensions,
                kb_id=kb_id,
                doc_id=doc_id,
            )

        strict = mode == "api_strict"
        return ai_gateway.embed_text(
            tenant_id=tenant_id,
            text=text,
            pool="main",
            task=task,
            strict=strict,
            local_dimensions=self.default_dimensions,
            kb_id=kb_id,
            doc_id=doc_id,
        )


embedding_provider = EmbeddingProvider()
