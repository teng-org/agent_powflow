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


class AgentMemoryConfig(BaseModel):
    mode: Literal["none", "short_term", "long_term"] = "long_term"
    preset: Literal["private_assistant", "customer_service", "consulting", "stateless", "custom"] = "private_assistant"
    recent_messages_limit: int = Field(default=12, ge=1, le=50)
    context_token_budget: int = Field(default=1800, ge=200, le=8000)
    compress_trigger_messages: int = Field(default=24, ge=8, le=200)
    summary_enabled: bool = True
    user_profile_enabled: bool = True
    episodic_enabled: bool = True
    session_recall_enabled: bool = True
    memory_item_max: int = Field(default=200, ge=20, le=2000)


class AgentPromptVariable(BaseModel):
    key: Literal["current_datetime"] = "current_datetime"
    enabled: bool = True
    calendar: Literal["gregorian", "lunar", "both"] = "both"
    timezone: str = Field(default="Asia/Shanghai", max_length=64)


class AgentSkillConfig(BaseModel):
    skill_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=128)
    version: int = Field(default=1, ge=1)
    instructions: str = Field(default="", max_length=30000)
    description: str = Field(default="", max_length=1000)
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    output_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    max_tokens: int = Field(default=800, ge=64, le=8192)


class AgentMcpToolConfig(BaseModel):
    connection_id: str = Field(min_length=1, max_length=128)
    connection_name: str = Field(default="", max_length=128)
    endpoint_url: str = Field(default="", max_length=2000)
    auth: Dict[str, str] = Field(default_factory=dict)
    tool_name: str = Field(min_length=1, max_length=256)
    title: str = Field(default="", max_length=256)
    description: str = Field(default="", max_length=2000)
    input_schema: Dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    annotations: Dict[str, Any] = Field(default_factory=dict)


class AgentJavaScriptToolConfig(BaseModel):
    tool_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=128)
    description: str = Field(default="", max_length=1000)
    version: int = Field(default=1, ge=1)
    definition: Dict[str, Any] = Field(default_factory=dict)


class AgentToolPolicy(BaseModel):
    mode: Literal["none", "auto", "required"] = "auto"
    max_rounds: int = Field(default=3, ge=1, le=8)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    result_max_chars: int = Field(default=12000, ge=500, le=50000)
    allow_write_tools: bool = False
    failure_mode: Literal["continue", "fail"] = "continue"


class AgentRuntimeConfig(BaseModel):
    agent_id: Optional[str] = Field(default=None, max_length=128)
    role: str = Field(default="", max_length=500)
    instructions: str = Field(default="", max_length=30000)
    response_style: str = Field(default="", max_length=2000)
    model_endpoint_id: Optional[str] = Field(default=None, max_length=128)
    temperature: float = Field(default=0.2, ge=0, le=2)
    top_p: float = Field(default=1.0, gt=0, le=1)
    max_tokens: int = Field(default=4096, ge=64, le=32768)
    prohibited_rules: str = Field(default="", max_length=12000)
    retrieval: AgentRetrievalConfig = Field(default_factory=AgentRetrievalConfig)
    reply_examples: List[AgentReplyExample] = Field(default_factory=list, max_length=8)
    memory: AgentMemoryConfig = Field(default_factory=AgentMemoryConfig)
    prompt_variables: List[AgentPromptVariable] = Field(default_factory=list, max_length=8)
    skills: List[AgentSkillConfig] = Field(default_factory=list, max_length=20)
    mcp_tools: List[AgentMcpToolConfig] = Field(default_factory=list, max_length=50)
    javascript_tools: List[AgentJavaScriptToolConfig] = Field(default_factory=list, max_length=50)
    tool_policy: AgentToolPolicy = Field(default_factory=AgentToolPolicy)


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
