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
