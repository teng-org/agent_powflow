from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

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


class AgentReplyExample(BaseModel):
    user: str = Field(default="", max_length=4000)
    assistant: str = Field(default="", max_length=8000)


class AgentRetrievalConfig(BaseModel):
    enabled: bool = True
    top_k: int = Field(default=6, ge=1, le=50)
    rank_method: Literal["vector", "hybrid", "rrf", "keyword"] = "vector"
    score_threshold: float = Field(default=0, ge=0, le=1)
    rerank_enabled: bool = False
    rerank_k: int = Field(default=30, ge=1, le=100)
    rerank_required: bool = False


class AgentRuntimeConfig(BaseModel):
    agent_id: Optional[str] = Field(default=None, max_length=128)
    role: str = Field(default="", max_length=500)
    instructions: str = Field(default="", max_length=30000)
    response_style: str = Field(default="", max_length=2000)
    model_endpoint_id: Optional[str] = Field(default=None, max_length=128)
    temperature: float = Field(default=0.2, ge=0, le=2)
    top_p: float = Field(default=1.0, gt=0, le=1)
    max_tokens: int = Field(default=4096, ge=64, le=32768)
    retrieval: AgentRetrievalConfig = Field(default_factory=AgentRetrievalConfig)
    reply_examples: List[AgentReplyExample] = Field(default_factory=list, max_length=8)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=64000)
    use_kb: bool = True
    use_agent: Optional[bool] = None
    kb_id: Optional[str] = None
    top_k: int = 6
    rank_method: Literal["vector", "hybrid", "rrf", "keyword"] = "vector"
    rerank_enabled: bool = False
    rerank_k: int = Field(default=30, ge=1, le=100)
    rerank_required: bool = False
    pipeline_id: str = "default-chat-v1"
    metadata: Dict[str, str] = Field(default_factory=dict)
    agent_config: Optional[AgentRuntimeConfig] = None


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
    retrieval_query: str = ""
    retrieval_query_meta: Dict[str, Any] = Field(default_factory=dict)
    retrieval_items: List[Dict] = Field(default_factory=list)
    retrieval_meta: Dict[str, Any] = Field(default_factory=dict)
    session_retrieval_items: List[Dict] = Field(default_factory=list)
    keyword_context_items: List[Dict] = Field(default_factory=list)
    agent_tool_steps: List[Dict[str, Any]] = Field(default_factory=list)
    agent_tool_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    prompt_messages: List[Dict[str, str]] = Field(default_factory=list)
    response_text: str = ""


class NodeResult(BaseModel):
    ok: bool = True
    stage: str = ""
    info: Dict[str, object] = Field(default_factory=dict)
