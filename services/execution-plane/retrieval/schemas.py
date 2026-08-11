from pydantic import BaseModel, Field


class IngestDocumentRequest(BaseModel):
    tenant_id: str
    kb_id: str
    doc_id: str
    task_id: str | None = None
    filename: str
    content: str
    chunk_method: str = Field(default="fixed")
    chunk_delimiter: str | None = None
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=500)
    auto_summary: bool = False


class SearchRequest(BaseModel):
    tenant_id: str
    kb_id: str
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    doc_ids: list[str] | None = None
    rank_method: str = Field(default="vector", pattern="^(vector|hybrid|rrf|keyword)$")
    candidate_k: int | None = Field(default=None, ge=1, le=200)
    final_k: int | None = Field(default=None, ge=1, le=50)
    max_chunks_per_doc: int = Field(default=3, ge=1, le=20)
    context_window: int = Field(default=1, ge=0, le=2)
    rerank_enabled: bool = False
    rerank_k: int = Field(default=30, ge=1, le=100)
    rerank_required: bool = False
    # Internal/admin-only override used to evaluate a shadow index before it is active.
    index_revision: str | None = Field(default=None, pattern="^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
