from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class ChatMessage(BaseModel):
    message_id: str
    session_id: str
    role: Role
    content: str
    created_at: str


class ChatSession(BaseModel):
    session_id: str
    tenant_id: str
    actor_id: str
    title: str
    kb_id: Optional[str] = None
    created_at: str
    updated_at: str


class ChatRunState(BaseModel):
    run_id: str
    session_id: str
    tenant_id: str
    actor_id: str
    mode: str
    status: Literal["RUNNING", "COMPLETED", "FAILED", "CANCELLED"] = "RUNNING"
    stage: str = "PENDING"
    progress_current: int = 0
    progress_total: int = 0
    error_message: Optional[str] = None
    started_at: str
    updated_at: str
    completed_at: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=64000)
    use_kb: bool = True
    kb_id: Optional[str] = None
    top_k: int = 6
    rank_method: Literal["vector", "hybrid", "rrf"] = "vector"
    pipeline_id: str = "default-chat-v1"
    metadata: Dict[str, str] = Field(default_factory=dict)


class ChatContext(BaseModel):
    tenant_id: str
    actor_id: str
    trace_id: str
    source_service: str
    session: ChatSession
    run: ChatRunState
    request: ChatRequest
    messages: List[ChatMessage] = Field(default_factory=list)
    memory_text: str = ""
    memory_short_summary: str = ""
    memory_long_summary: str = ""
    memory_user_profile: str = ""
    memory_items: List[Dict] = Field(default_factory=list)
    retrieval_items: List[Dict] = Field(default_factory=list)
    prompt_messages: List[Dict[str, str]] = Field(default_factory=list)
    response_text: str = ""


class NodeResult(BaseModel):
    ok: bool = True
    stage: str = ""
    info: Dict[str, object] = Field(default_factory=dict)
