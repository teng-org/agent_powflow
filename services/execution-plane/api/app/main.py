import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from typing import Dict
from html.parser import HTMLParser
from urllib.parse import quote, urlparse

from docx import Document
import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from psycopg import sql
from pypdf import PdfReader
from pydantic import BaseModel, Field
from starlette.responses import FileResponse, PlainTextResponse, StreamingResponse

from api.app.audit_logging import (
    AuditSink,
    build_base_record,
    extra_log_types,
    summarize_body,
    summarize_error_response_body,
)
from models.api_pool import ApiEndpoint, api_pool_manager, aux_api_pool_manager
from retrieval.schemas import SearchRequest
from retrieval.schema_migrations import require_database_current
from retrieval.index_lifecycle import (
    IndexLifecycleError,
    IndexQualityGateFailed,
    index_lifecycle,
)
from retrieval.service import preprocess_text_for_chunk, search, search_with_meta
from retrieval.store import kb_store
from runtime.agent_runtime import AgentRunRequest, agent_runtime
from runtime.jwt_keyring import JwtKeyRing, JwtTokenError, JwtVerification
from runtime.object_storage import object_storage
from runtime.workflow_secrets import (
    SecretVaultUnavailable,
    WorkflowSecretNotFound,
    workflow_secret_store,
)
from workers.app.celery_app import celery_app

try:
    from pymongo import MongoClient
except Exception:  # pragma: no cover
    MongoClient = None

app = FastAPI(title="execution-api", version="0.4.0")
SUPPORTED_CHUNK_METHODS = {"fixed", "sentence", "paragraph", "markdown", "delimiter"}
ADMIN_TOKEN = os.getenv("EXECUTION_ADMIN_TOKEN", "")
CONTROL_PLANE_INTERNAL_TOKEN = os.getenv("CONTROL_PLANE_INTERNAL_TOKEN", "")
ALERTMANAGER_WEBHOOK_TOKEN = os.getenv("ALERTMANAGER_WEBHOOK_TOKEN", "")
ADMIN_USERNAME = os.getenv("EXECUTION_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("EXECUTION_ADMIN_PASSWORD", "admin123")
ADMIN_ROLE = os.getenv("EXECUTION_ADMIN_ROLE", "PLATFORM_ADMIN")
JWT_TTL_SECONDS = int(os.getenv("EXECUTION_JWT_TTL_SECONDS", "43200"))
JWT_KEY_RING = JwtKeyRing.from_config(
    primary_id=os.getenv("EXECUTION_JWT_KEY_ID", "dev-primary"),
    primary_secret=os.getenv("EXECUTION_JWT_SECRET", "change-me-in-production"),
    previous_keys=os.getenv("EXECUTION_JWT_PREVIOUS_KEYS", ""),
    previous_expires_at=os.getenv("EXECUTION_JWT_PREVIOUS_KEYS_EXPIRES_AT", ""),
    legacy_accept_until=os.getenv("EXECUTION_JWT_LEGACY_ACCEPT_UNTIL", ""),
    issuer="agentpowflow-execution",
    audience="execution-plane",
)
AUDIT_ENABLED = os.getenv("EXECUTION_AUDIT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
AUDIT_BASE_DIR = os.getenv("EXECUTION_AUDIT_BASE_DIR", os.path.join("logs", "kb-audit"))
EXECUTION_ENV = os.getenv("EXECUTION_ENV", "dev")
LEGACY_KB_WRITE_ENV = "EXECUTION_LEGACY_KB_WRITE_ENABLED"
SUPPORTED_DBVIZ_TABLES = {
    "knowledge_bases",
    "documents",
    "chunks",
    "tasks",
    "task_events",
    "chunk_drafts",
    "system_settings",
    "api_token_usage",
    "api_usage_budgets",
    "api_usage_alerts",
    "chat_history_store_snapshots",
    "chat_sessions",
    "chat_messages",
    "chat_session_memory",
    "chat_user_profiles",
    "chat_memory_items",
    "chat_session_summaries",
    "chat_user_recent_activities",
    "chat_memory_fusion_logs",
    "chat_session_segments",
    "chat_runs",
    "system_log_entries",
    "config_schema",
    "config_values",
    "config_releases",
    "config_audit_logs",
    "execution_schema_migrations",
    "cross_store_repairs",
}
DBVIZ_NON_TENANT_TABLES = {
    "system_settings",
    "chat_history_store_snapshots",
    "chat_sessions",
    "chat_messages",
    "chat_session_memory",
    "chat_user_profiles",
    "chat_memory_items",
    "chat_runs",
    "config_schema",
    "config_releases",
    "config_audit_logs",
    "execution_schema_migrations",
}

cors_origins = os.getenv(
    "EXECUTION_CORS_ORIGINS",
    "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:5177,http://localhost:5180,http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175,http://127.0.0.1:5177,http://127.0.0.1:5180",
)
allowed_origins = [item.strip() for item in cors_origins.split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
audit_sink = AuditSink(enabled=AUDIT_ENABLED, base_dir=AUDIT_BASE_DIR, service="execution-api", env=EXECUTION_ENV)


def _send_persistent_task(name: str, payload: dict, queue: str, task_id: str) -> None:
    """Dispatch a task whose authoritative state/result is already persisted in PostgreSQL."""
    celery_app.send_task(name, args=[payload], queue=queue, task_id=task_id, ignore_result=True)


def _schedule_cross_store_repair() -> None:
    try:
        celery_app.send_task("app.tasks.repair_cross_store", queue="agent_queue", ignore_result=True)
    except Exception:
        # The PostgreSQL repair ledger remains authoritative during broker loss.
        pass


class ApiAdminLoginRequest(BaseModel):
    username: str
    password: str


class ApiAdminLoginResponse(BaseModel):
    token_type: str = "Bearer"
    access_token: str
    role: str = "PLATFORM_ADMIN"


class ApiCreateKbRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiRenameKbRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKbVisibilityRequest(BaseModel):
    visibility: str = Field(pattern="^(PRIVATE|SHARED_ALL)$")


class ApiKbBindingRequest(BaseModel):
    target_tenant_id: str = Field(min_length=1, max_length=128)


class ApiAgentRunRequest(BaseModel):
    kb_id: str
    question: str
    top_k: int = Field(default=5, ge=1, le=20)
    rank_method: str = Field(default="hybrid", pattern="^(vector|hybrid|rrf|keyword)$")
    context_window: int = Field(default=1, ge=0, le=2)
    rerank_enabled: bool = False
    rerank_k: int = Field(default=30, ge=1, le=100)
    rerank_required: bool = False
    conversation_id: str | None = None
    run_id: str | None = None
    allow_generation_without_evidence: bool = True


class ApiControlRunDispatchRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=64)
    actor_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=64)
    status: str = Field(pattern="^PENDING$")
    workflow_id: str = Field(min_length=1, max_length=64)
    workflow_version_id: str = Field(min_length=1, max_length=64)
    workflow_version_no: int = Field(ge=1)
    workflow_checksum: str = Field(pattern="^[0-9a-f]{64}$")
    workflow_dsl: dict
    workflow_layout: dict
    input_payload: dict
    approval_resume: dict | None = None


class ApiInternalTaskRevokeRequest(BaseModel):
    task_ids: list[str]


class ApiInternalDocumentDescribeRequest(BaseModel):
    doc_ids: list[str] = Field(min_length=1, max_length=500)


class ApiRagIndexBuildRequest(BaseModel):
    revision: str | None = Field(default=None, pattern="^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    require_evaluation: bool = False
    qdrant_batch_size: int = Field(default=16, ge=8, le=256)


class ApiRagIndexEvaluateRequest(BaseModel):
    cases: list[dict] = Field(min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=50)
    min_recall: float = Field(default=0.0, ge=0.0, le=1.0)
    min_mrr: float = Field(default=0.0, ge=0.0, le=1.0)
    min_ndcg: float = Field(default=0.0, ge=0.0, le=1.0)


class ApiRagIndexSwitchRequest(BaseModel):
    revision: str = Field(pattern="^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
    actor_id: str = Field(default="control-plane", min_length=1, max_length=128)


class ApiInternalTaskDispatchRequest(BaseModel):
    task_id: str = Field(min_length=1, max_length=256)
    task_type: str = Field(pattern="^(DOC_INGEST|KB_SUMMARIZE|DOC_SUMMARIZE|DOC_CHUNK|DOC_RECHUNK|DOC_REINDEX|CHUNK_COMMIT|DOC_MOVE|DOC_TAG|DOC_DELETE)$")
    tenant_id: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)
    trace_id: str = Field(min_length=1, max_length=160)
    source_service: str = Field(min_length=1, max_length=64)
    kb_id: str = Field(min_length=1, max_length=128)
    doc_id: str = Field(default="", max_length=128)
    filename: str = Field(default="", max_length=512)
    command: str = Field(default="", max_length=32)
    content: str = ""
    chunk_method: str = Field(default="fixed", pattern="^(fixed|sentence|paragraph|markdown|delimiter)$")
    chunk_delimiter: str | None = None
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=500)
    auto_summary: bool = False
    object_bucket: str = ""
    content_object_key: str = ""
    content_object_sha256: str = ""
    source_object_key: str = ""
    source_object_sha256: str = ""
    source_object_size_bytes: int = Field(default=0, ge=0)
    extracted_object_size_bytes: int = Field(default=0, ge=0)
    target_kb_id: str = Field(default="", max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=50)
    tag_mode: str = Field(default="add", pattern="^(add|replace)$")
    batch_id: str = Field(default="", max_length=128)
    batch_name: str = Field(default="", max_length=256)


class ApiSearchRequest(BaseModel):
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


class ApiIngestRequest(BaseModel):
    doc_id: str | None = None
    filename: str | None = None
    content: str
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=500)
    auto_summary: bool = False


class ApiReindexRequest(BaseModel):
    chunk_method: str = Field(default="fixed")
    chunk_delimiter: str | None = None
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=500)


class ApiChunkDraftRequest(BaseModel):
    action: str = Field(pattern="^(delete|edit)$")
    text: str | None = Field(default=None, max_length=1_000_000)


class ApiPoolUpsertRequest(BaseModel):
    endpoint_id: str
    name: str
    provider: str
    base_url: str
    model: str
    task: str = "llm"
    api_key: str = ""
    api_instance_id: str = ""
    api_instance_name: str = ""
    api_instance_remark: str = ""
    tenant_id: str = "*"
    enabled: bool = True
    frozen: bool = False
    priority: int = Field(default=100, ge=1)
    weight: int = Field(default=100, ge=1)
    max_concurrency: int = Field(default=8, ge=1)
    timeout_seconds: int = Field(default=120, ge=5)
    retry_max: int = Field(default=2, ge=0)
    circuit_fail_threshold: int = Field(default=5, ge=1)
    circuit_open_seconds: int = Field(default=30, ge=5)
    input_per_1k: float = Field(default=0.0, ge=0.0)
    output_per_1k: float = Field(default=0.0, ge=0.0)
    expires_at: str | None = None
    rotation_state: str = ""
    rotation_note: str = ""


API_CONSUMER_ENDPOINTS = [
    {
        "task": "rerank",
        "pool": "main",
        "label": "RAG 候选重排",
        "capability": "rerank",
        "description": "使用 Cross-Encoder 对 Dense/词法融合候选进行最终相关性重排。",
    },
    {
        "task": "embedding",
        "pool": "main",
        "label": "知识库向量嵌入",
        "capability": "embedding",
        "description": "文档入库、检索查询向量化和 RAG 召回。",
    },
    {
        "task": "llm",
        "pool": "main",
        "label": "执行平面通用 LLM",
        "capability": "llm",
        "description": "执行平面 Agent 或需要通用生成能力的后端任务。",
    },
    {
        "task": "summary_llm",
        "pool": "aux",
        "label": "文档/知识库总结 LLM",
        "capability": "llm",
        "description": "文档摘要、知识库总结、失败任务修复等辅助生成任务。",
    },
    {
        "task": "summary_embedding",
        "pool": "aux",
        "label": "总结辅助 Embedding",
        "capability": "embedding",
        "description": "总结链路中需要向量化的辅助任务。",
    },
]


class ApiKeyRotateRequest(BaseModel):
    new_api_key: str = Field(min_length=1)
    expires_at: str | None = None
    note: str = ""


class ApiWorkflowSecretCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9._-]+$")
    value: str = Field(min_length=1, max_length=8192)
    description: str = Field(default="", max_length=500)


class ApiWorkflowSecretRotateRequest(BaseModel):
    value: str = Field(min_length=1, max_length=8192)
    description: str | None = Field(default=None, max_length=500)


class ApiKeyLifecycleEvaluateRequest(BaseModel):
    pool: str = Field(default="all", pattern="^(all|main|aux)$")
    task: str = ""
    tenant_scope: str = Field(default="current", pattern="^(current|all)$")
    near_expiry_days: int = Field(default=7, ge=0, le=90)
    expiring_only: bool = False
    limit: int = Field(default=500, ge=1, le=5000)
    warn_days: str | None = None


class ApiUploadSettingsRequest(BaseModel):
    max_file_size_mb: int = Field(default=50, ge=1, le=200)


class ApiRuntimeConfigUpdateRequest(BaseModel):
    updates: dict[str, object] = Field(default_factory=dict)
    versioned: bool = True
    expected_latest_release_id: int | None = None


class ApiConfigDraftPatchRequest(BaseModel):
    scope_type: str = Field(default="PLATFORM", pattern="^(PLATFORM|TENANT|USER)$")
    scope_id: str = Field(default="platform")
    updates: dict[str, object] = Field(default_factory=dict)
    expected_latest_release_id: int | None = None


class ApiConfigPublishRequest(BaseModel):
    scope_type: str = Field(default="PLATFORM", pattern="^(PLATFORM|TENANT|USER)$")
    scope_id: str = Field(default="platform")
    changed_keys: list[str] = Field(default_factory=list)
    note: str = ""
    expected_latest_release_id: int | None = None
    idempotency_key: str = ""
    source_page: str = ""
    client_version: str = ""
    request_id: str = ""


class ApiConfigValidateRequest(BaseModel):
    scope_type: str = Field(default="PLATFORM", pattern="^(PLATFORM|TENANT|USER)$")
    scope_id: str = Field(default="platform")
    updates: dict[str, object] = Field(default_factory=dict)


class ApiConfigImpactRequest(BaseModel):
    scope_type: str = Field(default="PLATFORM", pattern="^(PLATFORM|TENANT|USER)$")
    scope_id: str = Field(default="platform")
    updates: dict[str, object] = Field(default_factory=dict)


class ApiConfigRollbackRequest(BaseModel):
    release_id: int = Field(ge=1)


class ApiConfigSwitchDefaultRequest(BaseModel):
    scope_type: str = Field(default="PLATFORM", pattern="^(PLATFORM|TENANT|USER)$")
    scope_id: str = Field(default="platform")


class ApiChunkRunRequest(BaseModel):
    chunk_method: str = Field(default="fixed")
    chunk_delimiter: str | None = None
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=500)


class ApiBatchDocumentsRequest(BaseModel):
    operation: str = Field(pattern="^(chunk|embed|summarize|move|tag|delete)$")
    doc_ids: list[str] = Field(default_factory=list, min_length=1)
    chunk_method: str = Field(default="sentence")
    chunk_delimiter: str | None = None
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=500)
    target_kb_id: str | None = None
    tags: list[str] | None = None
    batch_name: str | None = None


class ApiDocumentTagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)
    mode: str = Field(default="add", pattern="^(add|replace)$")


class ApiFreezeRequest(BaseModel):
    frozen: bool = False


class ApiUsageBudgetUpsertRequest(BaseModel):
    scope_type: str = Field(pattern="^(TENANT|KB|MODEL|TASK_CATEGORY)$")
    scope_value: str = ""
    daily_token_limit: int = Field(default=0, ge=0)
    daily_cost_limit: float = Field(default=0.0, ge=0.0)
    enabled: bool = True


class ApiUsageAlertStatusRequest(BaseModel):
    status: str = Field(pattern="^(OPEN|ACK|MUTED|RESOLVED)$")


class ApiPricingVersionUpsertRequest(BaseModel):
    tenant_id: str = "*"
    model: str = "*"
    input_per_1k: float = Field(default=0.0, ge=0.0)
    output_per_1k: float = Field(default=0.0, ge=0.0)
    currency: str = "USD"
    effective_from: str | None = None
    note: str = ""


class ApiReconciliationEvaluateRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=90)
    threshold_ratio: float | None = Field(default=None, ge=0.0)
    min_estimated_cost_usd: float | None = Field(default=None, ge=0.0)
    dimension: str = Field(default="model", pattern="^(endpoint|task|day|api_path|model)$")
    limit: int = Field(default=500, ge=1, le=5000)


class _HtmlTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.parts.append(data.strip())

    def get_text(self) -> str:
        return "\n".join(self.parts)


def _decode_text_bytes(raw: bytes) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            text = raw.decode(encoding)
            if text:
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _strip_nul(text: str) -> str:
    # PostgreSQL TEXT cannot contain NUL bytes, and malformed PDF text can
    # include lone surrogate code points that cannot be encoded as UTF-8.
    return re.sub(r"[\x00\ud800-\udfff]", "", text or "")


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _validate_chunk_method(chunk_method: str) -> str:
    method = (chunk_method or "fixed").strip().lower()
    if method not in SUPPORTED_CHUNK_METHODS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid chunk_method, choose from {sorted(SUPPORTED_CHUNK_METHODS)}",
        )
    return method


def _parse_tenant_scope(scope: str, tenant_id: str) -> tuple[bool, str]:
    s = (scope or "current").strip().lower()
    return (s == "all", tenant_id)


def _parse_warn_days(raw: str | None) -> list[int]:
    text = str(raw or "").strip()
    if not text:
        return [7, 3, 1]
    out: list[int] = []
    for one in text.split(","):
        try:
            value = int(str(one).strip())
        except Exception:
            continue
        if value >= 0:
            out.append(value)
    out = sorted(set(out), reverse=True)
    return out or [7, 3, 1]


def _mongo_uri() -> str:
    return (
        os.getenv("MONGODB_URI", "").strip()
        or os.getenv("MONGO_URI", "").strip()
        or os.getenv("MONGO_URL", "").strip()
    )


def _mongo_db() -> str:
    return os.getenv("MONGODB_DB", "").strip() or os.getenv("MONGO_DB", "").strip() or "agentpowflow"


def _runtime_config_default_path() -> str:
    env_path = os.getenv("RUNTIME_CONFIG_DEFAULT_PATH", "").strip()
    if env_path:
        return env_path
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../config/runtime-config.default.json"))


def _runtime_config_user_path() -> str:
    env_path = os.getenv("RUNTIME_CONFIG_USER_PATH", "").strip()
    if env_path:
        return env_path
    legacy = os.getenv("RUNTIME_CONFIG_PATH", "").strip()
    if legacy:
        return legacy
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../config/runtime-config.user.json"))


def _runtime_config_legacy_path() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../config/runtime-config.json"))


def _load_json_dict(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load_runtime_config_json() -> dict:
    default_path = _runtime_config_default_path()
    user_path = _runtime_config_user_path()
    legacy_path = _runtime_config_legacy_path()

    default_data = _load_json_dict(default_path)
    # Backward compatibility: if new default file doesn't exist, fallback to legacy runtime-config.json.
    if not default_data:
        default_data = _load_json_dict(legacy_path)

    user_data = _load_json_dict(user_path)
    # Backward compatibility: if user file doesn't exist and user path is not legacy file, fallback to legacy file.
    if not user_data and os.path.abspath(user_path) != os.path.abspath(legacy_path):
        user_data = _load_json_dict(legacy_path)

    merged = dict(default_data)
    merged.update(user_data)
    return merged


def _save_runtime_config_json(data: dict) -> None:
    path = _runtime_config_user_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


RUNTIME_CONFIG_ALLOWED_KEYS = {
    "execution_admin_token",
    "execution_admin_username",
    "execution_admin_password",
    "execution_admin_role",
    "execution_jwt_secret",
    "execution_jwt_ttl_seconds",
    "execution_env",
    "execution_audit_enabled",
    "execution_audit_base_dir",
    "system_log_storage_path",
    "system_log_storage_threshold_mb",
    "system_log_storage_threshold_gb",
    "system_log_storage_threshold",
    "system_log_hot_retention_days",
    "system_log_archive_retention_days",
    "system_log_archive_enabled",
    "system_log_archive_period",
    "system_log_expired_action",
    "system_log_path_change_strategy",
    "system_log_saved_views",
    "system_log_index_sync_interval_seconds",
    "system_log_index_sync_days",
    "system_log_index_sync_max_files",
    "system_log_index_sync_max_lines_per_file",
    "system_log_index_sync_min_interval_seconds",
    "security_risk_rules",
    "security_alert_thresholds",
    "global_kb_id",
    "embedding_mode",
    "embedding_max_chars",
    "summary_segment_chars",
    "summary_segment_stable",
    "summary_segment_unit_max_chars",
    "summary_segment_overlap",
    "summary_segment_max_chunks",
    "summary_segment_concurrency",
    "summary_min_chars",
    "summary_max_chars",
    "summary_overview_max_chars",
    "summary_doc_max_tokens",
    "summary_repair_max_tokens",
    "summary_kb_batch_size",
    "summary_kb_batch_max_tokens",
    "summary_kb_max_chars",
    "summary_kb_final_max_tokens",
    "chat_system_prompt",
    "chat_default_top_k",
    "chat_default_rank_method",
    "chat_agent_enabled",
    "chat_agent_tool_planning_enabled",
    "chat_agent_aux_summary_tool_enabled",
    "chat_agent_tool_max_rounds",
    "chat_agent_summary_max_chunks",
    "chat_agent_summary_max_tokens",
    "chat_stream_max_tokens",
    "chat_fallback_completion_max_tokens",
    "chat_stream_partial_success_min_chars",
    "chat_stream_continuation_max_rounds",
    "chat_session_title_max_attempts",
    "chat_jwt_secret",
    "chat_allow_legacy_admin_token",
    "chat_history_store_backend",
    "chat_history_store_path",
    "chat_session_vector_collection",
    "chat_session_vector_timeout_seconds",
    "memory_auto_summary_enabled",
    "memory_hierarchical_enabled",
    "memory_user_profile_enabled",
    "memory_eviction_enabled",
    "memory_recent_messages_limit",
    "memory_context_char_budget",
    "memory_compress_trigger_messages",
    "memory_short_summary_max_chars",
    "memory_long_summary_max_chars",
    "memory_profile_max_chars",
    "memory_item_max",
    "memory_query_rewrite_enabled",
    "memory_query_rewrite_use_llm",
    "memory_query_rewrite_max_chars",
    "memory_query_rewrite_min_chars",
    "memory_query_rewrite_min_overlap_ratio",
    "memory_query_rewrite_max_len_ratio",
    "memory_session_rag_enabled",
    "memory_session_rag_top_k",
    "memory_session_keyword_top_k",
    "memory_session_vector_enabled",
    "memory_session_vector_top_k",
    "memory_session_fusion_weight_keyword",
    "memory_session_fusion_weight_vector",
    "memory_session_fusion_weight_recency",
    "memory_session_fusion_hybrid_bonus",
    "memory_session_fusion_recency_half_life_days",
    "memory_recent_profile_activity_limit",
    "memory_recent_activity_ttl_seconds",
    "memory_recent_activity_cleanup_interval_seconds",
    "api_slot_max_per_endpoint",
    "api_slot_wait_seconds",
    "api_slot_ttl_seconds",
    "api_daily_token_threshold",
    "api_daily_cost_threshold",
    "api_concurrency_threshold",
    "api_reconciliation_diff_ratio_threshold",
    "api_reconciliation_min_estimated_cost_usd",
    "api_key_expiry_alert_days",
    "api_key_expiry_alert_scan_limit",
    "api_key_alert_suppress_seconds",
    "api_key_alert_escalate_trigger_count",
    "api_key_alert_muted_seconds",
    "api_key_alert_notify_webhook_url",
    "api_key_alert_notify_cooldown_seconds",
    "api_key_alert_notify_timeout_seconds",
    "api_key_alert_notify_retry_max",
    "api_key_alert_notify_retry_backoff_ms",
    "execution_cors_origins",
}
NON_VERSIONED_CONFIG_KEYS = {
    "system_log_saved_views",
}
RUNTIME_CONFIG_VERSIONED_KEYS = {k for k in RUNTIME_CONFIG_ALLOWED_KEYS if k not in NON_VERSIONED_CONFIG_KEYS}
_CONFIG_SCHEMA_SEEDED = False

# Incrementally managed UI/validation hints for config keys.
CONFIG_SCHEMA_HINTS: dict[str, dict] = {
    "execution_env": {"enum": ["dev", "test", "prod"]},
    "embedding_mode": {"enum": ["api_strict", "local_only"]},
    "chat_default_rank_method": {"enum": ["vector", "hybrid", "keyword"]},
    "chat_history_store_backend": {"enum": ["postgres_prefer", "postgres", "sqlite", "json"]},
    "system_log_archive_period": {"enum": ["month", "week", "day"]},
    "system_log_expired_action": {"enum": ["delete", "keep"]},
    "system_log_path_change_strategy": {"enum": ["migrate", "keep", "delete"]},
    "system_log_index_sync_interval_seconds": {"min": 3, "max": 60, "step": 1},
    "system_log_index_sync_days": {"min": 1, "max": 30, "step": 1},
    "system_log_index_sync_max_files": {"min": 10, "max": 300, "step": 1},
    "system_log_index_sync_max_lines_per_file": {"min": 200, "max": 20000, "step": 100},
    "system_log_index_sync_min_interval_seconds": {"min": 0.1, "max": 10, "step": 0.1},
    "execution_jwt_ttl_seconds": {"min": 60, "step": 60},
    "embedding_max_chars": {"min": 100, "step": 10},
    "summary_segment_chars": {"min": 100, "step": 10},
    "summary_segment_unit_max_chars": {"min": 100, "step": 10},
    "summary_segment_overlap": {"min": 0, "step": 5},
    "summary_segment_max_chunks": {"min": 1, "step": 1},
    "summary_segment_concurrency": {"min": 1, "step": 1},
    "summary_min_chars": {"min": 1, "step": 1},
    "summary_max_chars": {"min": 1, "step": 1},
    "summary_overview_max_chars": {"min": 1, "step": 1},
    "summary_doc_max_tokens": {"min": 1, "step": 1},
    "summary_repair_max_tokens": {"min": 1, "step": 1},
    "summary_kb_batch_size": {"min": 1, "step": 1},
    "summary_kb_batch_max_tokens": {"min": 1, "step": 1},
    "summary_kb_max_chars": {"min": 1, "step": 1},
    "summary_kb_final_max_tokens": {"min": 1, "step": 1},
    "chat_default_top_k": {"min": 1, "step": 1},
    "chat_agent_tool_max_rounds": {"min": 1, "max": 8, "step": 1},
    "chat_agent_summary_max_chunks": {"min": 1, "max": 30, "step": 1},
    "chat_agent_summary_max_tokens": {"min": 160, "max": 2000, "step": 10},
    "chat_stream_max_tokens": {"min": 256, "max": 8192, "step": 128},
    "chat_fallback_completion_max_tokens": {"min": 512, "max": 8192, "step": 128},
    "chat_stream_partial_success_min_chars": {"min": 80, "max": 12000, "step": 100},
    "chat_stream_continuation_max_rounds": {"min": 0, "max": 5, "step": 1},
    "chat_session_title_max_attempts": {"min": 1, "max": 3, "step": 1},
    "chat_session_vector_timeout_seconds": {"min": 1, "step": 1},
    "memory_recent_messages_limit": {"min": 1, "step": 1},
    "memory_context_char_budget": {"min": 1000, "step": 100},
    "memory_compress_trigger_messages": {"min": 1, "step": 1},
    "memory_short_summary_max_chars": {"min": 1, "step": 1},
    "memory_long_summary_max_chars": {"min": 1, "step": 1},
    "memory_profile_max_chars": {"min": 1, "step": 1},
    "memory_item_max": {"min": 1, "step": 1},
    "memory_query_rewrite_max_chars": {"min": 1, "step": 1},
    "memory_query_rewrite_min_chars": {"min": 1, "step": 1},
    "memory_query_rewrite_min_overlap_ratio": {"min": 0.0, "max": 1.0, "step": 0.01},
    "memory_query_rewrite_max_len_ratio": {"min": 0.0, "step": 0.01},
    "memory_session_rag_top_k": {"min": 1, "step": 1},
    "memory_session_keyword_top_k": {"min": 1, "step": 1},
    "memory_session_vector_top_k": {"min": 1, "step": 1},
    "memory_session_fusion_weight_keyword": {"min": 0.0, "max": 1.0, "step": 0.01},
    "memory_session_fusion_weight_vector": {"min": 0.0, "max": 1.0, "step": 0.01},
    "memory_session_fusion_weight_recency": {"min": 0.0, "max": 1.0, "step": 0.01},
    "memory_session_fusion_hybrid_bonus": {"min": 0.0, "step": 0.01},
    "memory_session_fusion_recency_half_life_days": {"min": 0.0, "step": 0.1},
    "memory_recent_profile_activity_limit": {"min": 1, "step": 1},
    "memory_recent_activity_ttl_seconds": {"min": 1, "step": 1},
    "memory_recent_activity_cleanup_interval_seconds": {"min": 1, "step": 1},
    "api_slot_max_per_endpoint": {"min": 1, "step": 1},
    "api_slot_wait_seconds": {"min": 1, "step": 1},
    "api_slot_ttl_seconds": {"min": 1, "step": 1},
    "api_daily_token_threshold": {"min": 0, "step": 1000},
    "api_daily_cost_threshold": {"min": 0.0, "step": 0.01},
    "api_concurrency_threshold": {"min": 0, "step": 1},
    "api_reconciliation_diff_ratio_threshold": {"min": 0.0, "step": 0.01},
    "api_reconciliation_min_estimated_cost_usd": {"min": 0.0, "step": 0.01},
    "api_key_expiry_alert_days": {},
    "api_key_expiry_alert_scan_limit": {"min": 1, "step": 1},
    "api_key_alert_suppress_seconds": {"min": 0, "step": 1},
    "api_key_alert_escalate_trigger_count": {"min": 1, "step": 1},
    "api_key_alert_muted_seconds": {"min": 0, "step": 1},
    "api_key_alert_notify_webhook_url": {},
    "api_key_alert_notify_cooldown_seconds": {"min": 0, "step": 1},
    "api_key_alert_notify_timeout_seconds": {"min": 1, "step": 1},
    "api_key_alert_notify_retry_max": {"min": 0, "step": 1},
    "api_key_alert_notify_retry_backoff_ms": {"min": 0, "step": 50},
    "security_risk_rules": {},
    "security_alert_thresholds": {},
}

DEFAULT_SECURITY_RISK_RULES = [
    {
        "id": "auth-login-fail",
        "priority": 1000,
        "enabled": True,
        "method": "POST",
        "path_pattern": "/api/v1/admin/auth/login",
        "risk_level": "HIGH",
        "risk_tags": ["auth"],
        "decision": "ALERT",
        "reason_code": "AUTH_LOGIN_FAIL",
    },
    {
        "id": "config-publish",
        "priority": 900,
        "enabled": True,
        "method": "POST",
        "path_pattern": "/api/v1/admin/config/publish",
        "risk_level": "HIGH",
        "risk_tags": ["config_change"],
        "decision": "ALERT",
        "reason_code": "CONFIG_PUBLISH",
    },
    {
        "id": "config-rollback",
        "priority": 900,
        "enabled": True,
        "method": "POST",
        "path_pattern": "/api/v1/admin/config/rollback",
        "risk_level": "HIGH",
        "risk_tags": ["config_change", "destructive"],
        "decision": "ALERT",
        "reason_code": "CONFIG_ROLLBACK",
    },
    {
        "id": "tenant-boundary",
        "priority": 800,
        "enabled": True,
        "method": "*",
        "path_pattern": "/api/v1/admin/kbs/*/bindings",
        "risk_level": "HIGH",
        "risk_tags": ["tenant_boundary"],
        "decision": "ALERT",
        "reason_code": "TENANT_BOUNDARY_OPERATION",
    },
]


def _config_hot_reload_default(key: str) -> bool:
    k = (key or "").strip()
    if not k:
        return False
    # Credentials and auth strategy should be treated as restart-required by default.
    for token in ("api_key", "secret", "password", "_token"):
        if token in k:
            return False
    if k in {"execution_env", "execution_cors_origins"}:
        return False
    return True


def _enrich_config_schema_rows(rows: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for row in rows or []:
        item = dict(row)
        key = str(item.get("config_key") or "")
        hint = CONFIG_SCHEMA_HINTS.get(key, {})
        if key:
            item["versioned"] = key not in NON_VERSIONED_CONFIG_KEYS
            item["hot_reload"] = bool(hint.get("hot_reload", _config_hot_reload_default(key)))
            item["validation"] = {
                "min": hint.get("min"),
                "max": hint.get("max"),
                "step": hint.get("step"),
                "enum": hint.get("enum"),
            }
        enriched.append(item)
    return enriched


def _to_float(value) -> float | None:
    try:
        n = float(value)
        if n != n:  # NaN
            return None
        return n
    except Exception:
        return None


def _to_int(value) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _validate_config_updates(*, updates: dict, base_item: dict | None = None) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []
    merged = dict(base_item or {})
    merged.update(updates or {})

    def add_error(key: str, code: str, message: str, *, value=None, constraint=None):
        errors.append({"key": key, "code": code, "message": message, "value": value, "constraint": constraint})

    def add_warning(key: str, code: str, message: str, *, value=None, constraint=None):
        warnings.append({"key": key, "code": code, "message": message, "value": value, "constraint": constraint})

    for key, value in (updates or {}).items():
        hint = CONFIG_SCHEMA_HINTS.get(str(key), {})
        enum_vals = hint.get("enum")
        if enum_vals and value is not None and str(value) not in enum_vals:
            add_error(str(key), "enum", f"{key} must be one of: {', '.join(enum_vals)}", value=value, constraint={"enum": enum_vals})
        min_v = hint.get("min")
        max_v = hint.get("max")
        if min_v is not None or max_v is not None:
            num = _to_float(value)
            if num is None:
                add_error(str(key), "type", f"{key} must be numeric", value=value, constraint={"type": "number"})
            else:
                if min_v is not None and num < float(min_v):
                    add_error(str(key), "min", f"{key} cannot be less than {min_v}", value=value, constraint={"min": min_v})
                if max_v is not None and num > float(max_v):
                    add_error(str(key), "max", f"{key} cannot be greater than {max_v}", value=value, constraint={"max": max_v})
        if str(key) == "security_risk_rules":
            if not isinstance(value, list):
                add_error("security_risk_rules", "type", "security_risk_rules must be a list", value=value, constraint={"type": "list"})
            else:
                seen_ids: set[str] = set()
                seen_route_priority: dict[tuple[str, str, int], int] = {}
                prior_rules: list[tuple[int, str, str, int]] = []
                for i, row in enumerate(value[:500]):
                    if not isinstance(row, dict):
                        add_error("security_risk_rules", "type", f"rule[{i}] must be object", value=row, constraint={"type": "object"})
                        continue
                    rule_id = str(row.get("id", "")).strip()
                    if not rule_id:
                        add_error("security_risk_rules", "required", f"rule[{i}].id is required", value=row)
                    elif rule_id in seen_ids:
                        add_error("security_risk_rules", "duplicate", f"rule[{i}].id duplicated: {rule_id}", value=row)
                    else:
                        seen_ids.add(rule_id)
                    if not str(row.get("path_pattern", "")).strip():
                        add_error("security_risk_rules", "required", f"rule[{i}].path_pattern is required", value=row)
                    method = str(row.get("method", "*") or "*").strip().upper() or "*"
                    if method != "*" and method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
                        add_error("security_risk_rules", "enum", f"rule[{i}].method invalid: {method}", value=row)
                    decision = str(row.get("decision", "ALERT") or "ALERT").strip().upper()
                    if decision not in {"ALLOW", "ALERT"}:
                        add_error("security_risk_rules", "enum", f"rule[{i}].decision invalid: {decision}", value=row)
                    lv = str(row.get("risk_level", "")).strip().upper()
                    if lv and lv not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}:
                        add_error("security_risk_rules", "enum", f"rule[{i}].risk_level invalid: {lv}", value=row)
                    priority = _to_int(row.get("priority", 0))
                    if priority is None:
                        add_error("security_risk_rules", "type", f"rule[{i}].priority must be integer", value=row.get("priority"))
                        priority = 0
                    if priority < 0 or priority > 100000:
                        add_error("security_risk_rules", "range", f"rule[{i}].priority out of range: {priority}", value=row.get("priority"))
                    route_key = (method, str(row.get("path_pattern", "")).strip().lower(), int(priority))
                    if route_key[1]:
                        first_idx = seen_route_priority.get(route_key)
                        if first_idx is not None:
                            add_error(
                                "security_risk_rules",
                                "conflict",
                                f"rule[{i}] conflicts with rule[{first_idx}] (same method/path_pattern/priority)",
                                value=row,
                            )
                        else:
                            seen_route_priority[route_key] = i
                        for prev_idx, prev_method, prev_pattern, prev_priority in prior_rules:
                            if int(prev_priority) != int(priority):
                                continue
                            if not _methods_overlap(method, prev_method):
                                continue
                            if _path_patterns_may_overlap(route_key[1], prev_pattern):
                                add_error(
                                    "security_risk_rules",
                                    "conflict",
                                    f"rule[{i}] overlaps with rule[{prev_idx}] (same priority + overlapping method/path_pattern)",
                                    value=row,
                                )
                        prior_rules.append((i, method, route_key[1], int(priority)))
        if str(key) == "security_alert_thresholds":
            if value is not None and not isinstance(value, dict):
                add_error("security_alert_thresholds", "type", "security_alert_thresholds must be object", value=value, constraint={"type": "object"})

    # Cross-field rules (range/dependency/conflict)
    smin = _to_float(merged.get("summary_min_chars"))
    smax = _to_float(merged.get("summary_max_chars"))
    if smin is not None and smax is not None and smin > smax:
        add_error("summary_min_chars", "range", "summary_min_chars cannot be greater than summary_max_chars", value=merged.get("summary_min_chars"))
        add_error("summary_max_chars", "range", "summary_max_chars must be >= summary_min_chars", value=merged.get("summary_max_chars"))

    seg_chars = _to_float(merged.get("summary_segment_chars"))
    seg_overlap = _to_float(merged.get("summary_segment_overlap"))
    seg_unit_max = _to_float(merged.get("summary_segment_unit_max_chars"))
    if seg_chars is not None and seg_overlap is not None and seg_overlap >= seg_chars:
        add_error(
            "summary_segment_overlap",
            "range",
            "summary_segment_overlap must be less than summary_segment_chars",
            value=merged.get("summary_segment_overlap"),
        )
    if seg_chars is not None and seg_unit_max is not None and seg_unit_max > seg_chars:
        add_error(
            "summary_segment_unit_max_chars",
            "range",
            "summary_segment_unit_max_chars cannot exceed summary_segment_chars",
            value=merged.get("summary_segment_unit_max_chars"),
        )

    w_keyword = _to_float(merged.get("memory_session_fusion_weight_keyword"))
    w_vector = _to_float(merged.get("memory_session_fusion_weight_vector"))
    w_recency = _to_float(merged.get("memory_session_fusion_weight_recency"))
    if w_keyword is not None and w_vector is not None and w_recency is not None:
        total = w_keyword + w_vector + w_recency
        if total > 1.0 + 1e-6:
            add_error(
                "memory_session_fusion_weight_keyword",
                "sum",
                "fusion weights (keyword/vector/recency) sum must be <= 1.0",
                value={"keyword": w_keyword, "vector": w_vector, "recency": w_recency},
                constraint={"max_sum": 1.0},
            )

    slot_wait = _to_float(merged.get("api_slot_wait_seconds"))
    slot_ttl = _to_float(merged.get("api_slot_ttl_seconds"))
    if slot_wait is not None and slot_ttl is not None and slot_ttl < slot_wait:
        add_error(
            "api_slot_ttl_seconds",
            "range",
            "api_slot_ttl_seconds must be >= api_slot_wait_seconds",
            value=merged.get("api_slot_ttl_seconds"),
        )

    hot_days = _to_float(merged.get("system_log_hot_retention_days"))
    archive_days = _to_float(merged.get("system_log_archive_retention_days"))
    if hot_days is not None and archive_days is not None and archive_days < hot_days:
        add_error(
            "system_log_archive_retention_days",
            "range",
            "system_log_archive_retention_days must be >= system_log_hot_retention_days",
            value=merged.get("system_log_archive_retention_days"),
        )

    rewrite_enabled = merged.get("memory_query_rewrite_enabled")
    rewrite_use_llm = merged.get("memory_query_rewrite_use_llm")
    if bool(rewrite_use_llm) and not bool(rewrite_enabled):
        add_error(
            "memory_query_rewrite_use_llm",
            "dependency",
            "memory_query_rewrite_use_llm requires memory_query_rewrite_enabled=true",
            value=rewrite_use_llm,
        )

    archive_enabled = merged.get("system_log_archive_enabled")
    if archive_enabled is False and "system_log_archive_period" in (updates or {}):
        add_warning(
            "system_log_archive_period",
            "inactive",
            "archive period is set but archive is disabled",
            value=merged.get("system_log_archive_period"),
        )

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def _estimate_config_impact(*, updates: dict) -> dict:
    keys = [str(k) for k in (updates or {}).keys()]
    modules: set[str] = set()
    services: set[str] = set()
    restart_required_keys: list[str] = []
    hot_reload_keys: list[str] = []
    non_versioned_keys: list[str] = []

    for key in keys:
        if key in NON_VERSIONED_CONFIG_KEYS:
            non_versioned_keys.append(key)
        hint = CONFIG_SCHEMA_HINTS.get(key, {})
        hot_reload = bool(hint.get("hot_reload", _config_hot_reload_default(key)))
        if hot_reload:
            hot_reload_keys.append(key)
        else:
            restart_required_keys.append(key)

        if key.startswith("chat_") or key.startswith("memory_"):
            modules.add("chat")
            services.add("chat-plane")
        elif key.startswith("api_"):
            modules.add("api_governance")
            services.add("execution-api")
            services.add("execution-worker")
        elif key.startswith("summary_") or key.startswith("embedding_") or key in {"global_kb_id"}:
            modules.add("kb")
            services.add("execution-api")
            services.add("execution-worker")
        elif key.startswith("system_log_") or key.startswith("execution_"):
            modules.add("system")
            services.add("execution-api")
            if key == "execution_cors_origins":
                services.add("web-admin")
                services.add("debug-web")
        else:
            modules.add("general")
            services.add("execution-api")

    risk_level = "LOW"
    reasons: list[str] = []
    if restart_required_keys:
        risk_level = "MEDIUM"
        reasons.append(f"包含需重启配置: {', '.join(restart_required_keys[:8])}")
    if any(_is_secret_config_key(k) for k in keys):
        risk_level = "HIGH"
        reasons.append("包含认证/密钥类配置，影响鉴权与外部调用稳定性")
    if "execution_jwt_secret" in keys:
        risk_level = "HIGH"
        reasons.append("JWT 密钥变更将导致已签发令牌失效")
    if "execution_cors_origins" in keys:
        reasons.append("CORS 变更可能影响控制台与前端访问")

    return {
        "risk_level": risk_level,
        "impact_modules": sorted(modules),
        "impact_services": sorted(services),
        "restart_required_keys": sorted(restart_required_keys),
        "hot_reload_keys": sorted(hot_reload_keys),
        "non_versioned_keys": sorted(non_versioned_keys),
        "reasons": reasons,
    }


def _is_secret_config_key(key: str) -> bool:
    k = (key or "").strip().lower()
    if not k:
        return False
    for token in ("api_key", "secret", "password", "private_key"):
        if token in k:
            return True
    return k.endswith("_token")


def _mask_secret_value(value):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if len(text) <= 6:
            return "***"
        return f"{text[:3]}***{text[-2:]}"
    return "***"


def _ensure_config_schema_seeded() -> dict:
    global _CONFIG_SCHEMA_SEEDED
    defaults = _load_json_dict(_runtime_config_default_path()) or _load_json_dict(_runtime_config_legacy_path())
    result = kb_store.seed_config_schema(defaults, RUNTIME_CONFIG_ALLOWED_KEYS)
    _CONFIG_SCHEMA_SEEDED = True
    return result


def _runtime_config_safe_items(item: dict) -> dict:
    safe: dict = {}
    for key in sorted(RUNTIME_CONFIG_ALLOWED_KEYS):
        if key not in item:
            continue
        val = item.get(key)
        safe[key] = _mask_secret_value(val) if _is_secret_config_key(key) else val
    return safe


def _sync_runtime_config_user_from_config_center() -> None:
    """
    Keep legacy runtime-config.user.json mirror in sync so existing components
    still reading file-based config observe latest PLATFORM effective values.
    """
    effective = kb_store.get_config_effective(scope_type="PLATFORM", scope_id="platform")
    item = (effective or {}).get("item") or {}
    mirrored = {k: item.get(k) for k in sorted(RUNTIME_CONFIG_ALLOWED_KEYS) if k in item}
    try:
        current_file = _load_runtime_config_json()
        for key in NON_VERSIONED_CONFIG_KEYS:
            if key in current_file:
                mirrored[key] = current_file.get(key)
    except Exception:
        pass
    if mirrored:
        _save_runtime_config_json(mirrored)


def _runtime_log_root(config: dict | None = None) -> str:
    conf = config or _load_runtime_config_json()
    root = str(conf.get("system_log_storage_path") or "").strip()
    if root:
        return root
    return os.path.abspath(AUDIT_BASE_DIR)


def _runtime_log_hot_days(config: dict | None = None) -> int:
    conf = config or _load_runtime_config_json()
    try:
        value = int(conf.get("system_log_hot_retention_days", 30))
    except Exception:
        value = 30
    return max(1, min(value, 3650))


def _runtime_log_archive_days(config: dict | None = None) -> int:
    conf = config or _load_runtime_config_json()
    try:
        value = int(conf.get("system_log_archive_retention_days", 60))
    except Exception:
        value = 60
    return max(1, min(value, 3650))


def _runtime_log_archive_enabled(config: dict | None = None) -> bool:
    conf = config or _load_runtime_config_json()
    raw = conf.get("system_log_archive_enabled", True)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _runtime_log_expired_action(config: dict | None = None) -> str:
    conf = config or _load_runtime_config_json()
    action = str(conf.get("system_log_expired_action", "delete")).strip().lower()
    return action if action in {"delete", "keep"} else "delete"


def _risk_level_rank(level: str) -> int:
    v = str(level or "").strip().upper()
    if v == "CRITICAL":
        return 4
    if v == "HIGH":
        return 3
    if v == "MEDIUM":
        return 2
    return 1


def _path_match(path: str, pattern: str) -> bool:
    p = str(path or "").strip().lower()
    pat = str(pattern or "").strip().lower()
    if not pat:
        return False
    if "*" not in pat:
        return p == pat
    if pat == "*":
        return True
    parts = [x for x in pat.split("*") if x]
    if not parts:
        return True
    idx = 0
    for part in parts:
        pos = p.find(part, idx)
        if pos < 0:
            return False
        idx = pos + len(part)
    if not pat.startswith("*") and not p.startswith(parts[0]):
        return False
    if not pat.endswith("*") and not p.endswith(parts[-1]):
        return False
    return True


def _methods_overlap(method_a: str, method_b: str) -> bool:
    a = str(method_a or "*").strip().upper() or "*"
    b = str(method_b or "*").strip().upper() or "*"
    return a == "*" or b == "*" or a == b


def _path_patterns_may_overlap(pattern_a: str, pattern_b: str) -> bool:
    a = str(pattern_a or "").strip().lower()
    b = str(pattern_b or "").strip().lower()
    if not a or not b:
        return False
    if a == "*" or b == "*":
        return True
    if "*" not in a and "*" not in b:
        return a == b
    if "*" not in a:
        return _path_match(a, b)
    if "*" not in b:
        return _path_match(b, a)
    # Both wildcard patterns: quickly reject obviously incompatible prefix/suffix.
    a_prefix = a.split("*", 1)[0]
    b_prefix = b.split("*", 1)[0]
    if a_prefix and b_prefix and not (a_prefix.startswith(b_prefix) or b_prefix.startswith(a_prefix)):
        return False
    a_suffix = a.rsplit("*", 1)[-1]
    b_suffix = b.rsplit("*", 1)[-1]
    if a_suffix and b_suffix and not (a_suffix.endswith(b_suffix) or b_suffix.endswith(a_suffix)):
        return False
    return True


def _load_security_risk_rules(config: dict | None = None) -> list[dict]:
    conf = config or _load_runtime_config_json()
    raw = conf.get("security_risk_rules")
    rows: list[dict] = []
    if isinstance(raw, list) and raw:
        for item in raw:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row["priority"] = _to_int(row.get("priority", 0)) or 0
            rows.append(row)
    if not rows:
        rows = [dict(item) for item in DEFAULT_SECURITY_RISK_RULES]
        for row in rows:
            row["priority"] = _to_int(row.get("priority", 0)) or 0
    rows.sort(key=lambda x: (int(x.get("priority", 0) or 0), str(x.get("id", ""))), reverse=True)
    return rows


def _classify_log_risk(rec: dict) -> dict:
    path = str(rec.get("path", "") or "")
    method = str(rec.get("method", "") or "*").strip().upper()
    category = str(rec.get("resource_type", "") or rec.get("log_type", "") or "system").lower()
    event = str(rec.get("event", "") or "").lower()
    auth_result = str(rec.get("auth_result", "") or "").upper()
    outcome = str(rec.get("outcome", "") or "").upper()
    http_status_raw = str(rec.get("http_status", "") or "").strip()
    try:
        http_status = int(http_status_raw)
    except Exception:
        http_status = 0

    tags: set[str] = set()
    reason_code = "GENERIC_EVENT"
    decision = "ALLOW"
    risk_level = "LOW"
    trigger_rule = "default"

    path_only = path.split("?", 1)[0].lower()
    if "/auth/" in path_only or category == "auth":
        tags.add("auth")
        trigger_rule = "auth"
        risk_level = "MEDIUM"
        reason_code = "AUTH_EVENT"
    if "/config/" in path_only:
        tags.add("config_change")
        trigger_rule = "config"
        risk_level = "MEDIUM"
        reason_code = "CONFIG_EVENT"
    if "/tenants" in path_only or "/users" in path_only or "/bindings" in path_only:
        tags.add("tenant_boundary")
        if risk_level in {"LOW", "MEDIUM"}:
            risk_level = "HIGH"
        trigger_rule = "tenant_boundary"
        reason_code = "TENANT_BOUNDARY_OPERATION"
    if "/usage/" in path_only or "budget" in path_only:
        tags.add("billing")
    if "/api-pool" in path_only:
        tags.add("api_governance")

    if auth_result == "DENY" or outcome in {"DENY", "FAIL"} or http_status in {401, 403}:
        decision = "ALERT"
        if "/auth/login" in path_only:
            reason_code = "AUTH_LOGIN_FAIL"
        elif auth_result == "DENY":
            reason_code = "ACCESS_DENIED"
        else:
            reason_code = "AUTHZ_OR_AUTHN_FAIL"
        risk_level = "HIGH"

    if http_status >= 500:
        reason_code = "SERVER_ERROR"
        decision = "ALERT"
        if risk_level == "HIGH":
            risk_level = "CRITICAL"
        elif risk_level in {"LOW", "MEDIUM"}:
            risk_level = "HIGH"

    if "rollback" in path_only or "switch-default" in path_only or "delete" in event:
        tags.add("destructive")
        if risk_level in {"LOW", "MEDIUM"}:
            risk_level = "HIGH"

    # Config-driven rules override/upgrade defaults.
    for rule in _load_security_risk_rules():
        if not isinstance(rule, dict):
            continue
        if not bool(rule.get("enabled", True)):
            continue
        r_method = str(rule.get("method", "*") or "*").strip().upper()
        if r_method not in {"*", method}:
            continue
        if not _path_match(path_only, str(rule.get("path_pattern", "") or "")):
            continue
        r_level = str(rule.get("risk_level", "") or "").strip().upper() or "LOW"
        if _risk_level_rank(r_level) >= _risk_level_rank(risk_level):
            risk_level = r_level
        for tag in (rule.get("risk_tags") or []):
            t = str(tag or "").strip().lower()
            if t:
                tags.add(t)
        decision = str(rule.get("decision", decision) or decision).strip().upper() or decision
        reason_code = str(rule.get("reason_code", reason_code) or reason_code).strip().upper() or reason_code
        trigger_rule = str(rule.get("id", trigger_rule) or trigger_rule).strip() or trigger_rule
        break

    return {
        "risk_level": risk_level,
        "risk_tags": sorted(tags),
        "decision": decision,
        "reason_code": reason_code,
        "trigger_rule": trigger_rule,
    }


def _emit_risk_event_audit(
    *,
    request: Request,
    event_id: str,
    action: str,
    status: str,
    note: str,
) -> None:
    record = build_base_record(
        request=request,
        path_template=f"/api/v1/admin/system/risk/events/{{event_id}}/{action}",
        body_summary=f"kind=json;event_id={event_id};status={status};note_len={len(str(note or ''))}",
        response_status=200,
        duration_ms=0,
        error_class="",
        error_reason="",
    )
    record["event"] = f"kb.risk.event.{action}"
    record["resource_type"] = "risk_event"
    record["resource_id"] = str(event_id or "")
    record["action"] = action
    record["outcome"] = "SUCCESS"
    record["biz_code"] = "OK"
    record["error_reason"] = ""
    audit_sink.emit("access", record)
    audit_sink.emit("security", record)
    audit_sink.emit("system", record)


def _parse_ltsv_line(line: str) -> dict:
    rec: dict[str, str] = {}
    for part in (line or "").strip().split("\t"):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        rec[key.strip()] = value.strip()
    if not rec:
        return {}
    level = str(rec.get("level", "INFO")).strip().lower()
    risk = _classify_log_risk(rec)
    return {
        "id": f"{rec.get('ts', '')}-{rec.get('request_id', '')}-{hash((rec.get('path', ''), rec.get('event', '')))}",
        "ts": rec.get("ts", ""),
        "level": level,
        "category": rec.get("resource_type", "") or rec.get("log_type", "") or "system",
        "message": rec.get("error_reason", "") or rec.get("event", "") or "-",
        "source": rec.get("source_service", "") or rec.get("service", "") or "-",
        "role": rec.get("actor_role", "") or "-",
        "status": rec.get("outcome", "") or rec.get("auth_result", "") or "-",
        "path": rec.get("path", "") or "-",
        "event": rec.get("event", ""),
        "resource_type": rec.get("resource_type", ""),
        "http_status": rec.get("http_status", ""),
        "auth_result": rec.get("auth_result", ""),
        "trace_id": rec.get("trace_id", "") or rec.get("request_id", ""),
        "request_id": rec.get("request_id", ""),
        "risk_level": risk.get("risk_level", "LOW"),
        "risk_tags": risk.get("risk_tags", []),
        "decision": risk.get("decision", "ALLOW"),
        "reason_code": risk.get("reason_code", "GENERIC_EVENT"),
        "trigger_rule": risk.get("trigger_rule", "default"),
    }


def _list_log_day_dirs(log_root: str) -> list[tuple[datetime, str]]:
    if not os.path.isdir(log_root):
        return []
    out: list[tuple[datetime, str]] = []
    for name in os.listdir(log_root):
        full = os.path.join(log_root, name)
        if not os.path.isdir(full):
            continue
        try:
            dt = datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            continue
        out.append((dt, full))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _dir_size_bytes(path: str) -> int:
    total = 0
    if not path or not os.path.exists(path):
        return 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += int(entry.stat(follow_symlinks=False).st_size or 0)
                    except Exception:
                        continue
        except Exception:
            continue
    return max(0, total)


def _iter_system_logs(
    *,
    log_root: str,
    days: int = 30,
    level: str = "",
    category: str = "",
    q: str = "",
    from_ts: str = "",
    to_ts: str = "",
    include_noise: bool = False,
    max_rows: int = 1000,
    max_scan_lines: int = 10000,
    file_names: list[str] | None = None,
) -> list[dict]:
    local_tz = datetime.now().astimezone().tzinfo

    def _parse_time_bound(raw: str, *, is_end: bool) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        # Date-only input should represent full-day bounds.
        try:
            if len(text) == 10 and text.count("-") == 2:
                if is_end:
                    dt = datetime.fromisoformat(f"{text}T23:59:59.999999")
                else:
                    dt = datetime.fromisoformat(f"{text}T00:00:00")
            else:
                dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=local_tz)
            return dt
        except Exception:
            return None

    now = datetime.now()
    level_filter = str(level or "").strip().lower()
    category_filter = str(category or "").strip().lower()
    keyword = str(q or "").strip().lower()
    from_filter = _parse_time_bound(from_ts, is_end=False)
    to_filter = _parse_time_bound(to_ts, is_end=True)
    cutoff = now - timedelta(days=max(1, int(days or 30)))
    rows: list[dict] = []
    scanned_lines = 0
    allowed_file_set = {str(x or "").strip().lower() for x in (file_names or []) if str(x or "").strip()}
    noise_exact_paths = {
        "/api/v1/system/health",
        "/api/v1/admin/system/logs",
        "/api/v1/admin/system/logs/stats",
        "/api/v1/admin/system/alerts/preview",
        "/api/v1/admin/system/permission-warnings",
        "/api/v1/admin/system/risk/overview",
        "/api/v1/admin/system/risk/events",
        "/api/v1/admin/system/risk/events/stats",
        "/api/v1/admin/system/runtime-stats/daily",
        "/api/v1/admin/system/runtime-config",
        "/api/v1/admin/system/storage/usage",
        "/api/v1/admin/usage/api-tokens/summary",
        "/api/v1/admin/usage/api-tokens/budgets",
    }

    for day_dt, day_dir in _list_log_day_dirs(log_root):
        if max_rows > 0 and len(rows) >= max_rows:
            break
        if max_scan_lines > 0 and scanned_lines >= max_scan_lines:
            break
        if not from_filter and not to_filter and day_dt < cutoff:
            continue
        for filename in sorted(os.listdir(day_dir), reverse=True):
            if max_rows > 0 and len(rows) >= max_rows:
                break
            if max_scan_lines > 0 and scanned_lines >= max_scan_lines:
                break
            if not filename.endswith(".log"):
                continue
            if allowed_file_set:
                lower_name = filename.lower()
                if lower_name not in allowed_file_set:
                    continue
            path = os.path.join(day_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        scanned_lines += 1
                        if max_scan_lines > 0 and scanned_lines > max_scan_lines:
                            break
                        item = _parse_ltsv_line(line)
                        if not item:
                            continue
                        ts_raw = str(item.get("ts") or "")
                        try:
                            ts_dt = datetime.fromisoformat(ts_raw)
                        except Exception:
                            ts_dt = day_dt
                        if ts_dt.tzinfo is None:
                            ts_dt = ts_dt.replace(tzinfo=local_tz)
                        ts_epoch = ts_dt.timestamp()
                        if from_filter and ts_epoch < from_filter.timestamp():
                            continue
                        if to_filter and ts_epoch > to_filter.timestamp():
                            continue
                        if level_filter and str(item.get("level", "")).lower() != level_filter:
                            continue
                        if category_filter and category_filter not in str(item.get("category", "")).lower():
                            continue
                        if not include_noise:
                            path_val = str(item.get("path", "")).split("?", 1)[0]
                            if path_val in noise_exact_paths:
                                continue
                        if keyword:
                            bag = " ".join([
                                str(item.get("message", "")),
                                str(item.get("path", "")),
                                str(item.get("source", "")),
                                str(item.get("role", "")),
                                str(item.get("status", "")),
                                str(item.get("event", "")),
                            ]).lower()
                            if keyword not in bag:
                                continue
                        rows.append(item)
                        if max_rows > 0 and len(rows) >= max_rows:
                            break
            except Exception:
                continue
    rows.sort(key=lambda x: str(x.get("ts", "")), reverse=True)
    return rows


_SYSTEM_LOG_SYNC_LAST_TS = 0.0
_SYSTEM_LOG_SYNC_TASK: asyncio.Task | None = None
_SYSTEM_LOG_SYNC_HEALTH: dict[str, object] = {
    "last_started_at": "",
    "last_finished_at": "",
    "last_success_at": "",
    "last_error_at": "",
    "last_error": "",
    "last_duration_ms": 0,
    "last_scanned_files": 0,
    "last_scanned_lines": 0,
    "last_parsed_rows": 0,
    "last_insert_candidates": 0,
    "last_insert_rowcount": 0,
    "total_runs": 0,
    "total_failures": 0,
}


def _to_int_clamped(value: object, default_value: int, *, min_value: int, max_value: int) -> int:
    try:
        n = int(value)
    except Exception:
        n = int(default_value)
    return max(min_value, min(max_value, n))


def _to_float_clamped(value: object, default_value: float, *, min_value: float, max_value: float) -> float:
    try:
        n = float(value)
    except Exception:
        n = float(default_value)
    return max(min_value, min(max_value, n))


def _log_index_sync_settings() -> dict[str, object]:
    conf = _load_runtime_config_json()
    return {
        "interval_seconds": _to_float_clamped(
            conf.get("system_log_index_sync_interval_seconds", 8),
            8.0,
            min_value=3.0,
            max_value=60.0,
        ),
        "days": _to_int_clamped(
            conf.get("system_log_index_sync_days", 7),
            7,
            min_value=1,
            max_value=30,
        ),
        "max_files": _to_int_clamped(
            conf.get("system_log_index_sync_max_files", 60),
            60,
            min_value=10,
            max_value=300,
        ),
        "max_lines_per_file": _to_int_clamped(
            conf.get("system_log_index_sync_max_lines_per_file", 3000),
            3000,
            min_value=200,
            max_value=20000,
        ),
        "min_interval_seconds": _to_float_clamped(
            conf.get("system_log_index_sync_min_interval_seconds", 0.5),
            0.5,
            min_value=0.1,
            max_value=10.0,
        ),
    }


def _tail_lines(path: str, max_lines: int = 2000) -> list[str]:
    from collections import deque

    cap = max(1, int(max_lines or 2000))
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return list(deque(f, maxlen=cap))
    except Exception:
        return []


def _sync_system_log_index(
    *,
    days: int = 7,
    file_names: list[str] | None = None,
    max_files: int = 40,
    max_lines_per_file: int = 2000,
    min_interval_seconds: float = 2.0,
) -> None:
    global _SYSTEM_LOG_SYNC_LAST_TS
    started_at_dt = datetime.now()
    started_perf = time.perf_counter()
    _SYSTEM_LOG_SYNC_HEALTH["total_runs"] = int(_SYSTEM_LOG_SYNC_HEALTH.get("total_runs", 0) or 0) + 1
    _SYSTEM_LOG_SYNC_HEALTH["last_started_at"] = started_at_dt.isoformat()
    now = time.time()
    if now - float(_SYSTEM_LOG_SYNC_LAST_TS or 0.0) < float(min_interval_seconds):
        return
    _SYSTEM_LOG_SYNC_LAST_TS = now

    log_root = _runtime_log_root()
    cap_days = max(1, min(int(days or 7), 30))
    cutoff = datetime.now() - timedelta(days=cap_days)
    allowed_files = {str(x or "").strip().lower() for x in (file_names or []) if str(x or "").strip()}
    rows_to_insert: list[tuple] = []
    scanned_files = 0
    scanned_lines = 0

    for day_dt, day_dir in _list_log_day_dirs(log_root):
        if day_dt < cutoff:
            continue
        for filename in sorted(os.listdir(day_dir), reverse=True):
            if scanned_files >= max_files:
                break
            if not filename.endswith(".log"):
                continue
            if allowed_files and filename.lower() not in allowed_files:
                continue
            scanned_files += 1
            full = os.path.join(day_dir, filename)
            lines = _tail_lines(full, max_lines=max_lines_per_file)
            scanned_lines += len(lines)
            for line in lines:
                item = _parse_ltsv_line(line)
                if not item:
                    continue
                raw_uid = f"{day_dir}|{filename}|{line.strip()}"
                log_uid = hashlib.sha1(raw_uid.encode("utf-8", errors="ignore")).hexdigest()
                event_id = str(item.get("event_id") or item.get("id") or "").strip() or log_uid
                ts_text = str(item.get("ts") or "").strip()
                try:
                    ts_val = datetime.fromisoformat(ts_text) if ts_text else datetime.now()
                except Exception:
                    ts_val = datetime.now()
                risk_tags = item.get("risk_tags", [])
                if not isinstance(risk_tags, list):
                    risk_tags = []
                rows_to_insert.append(
                    (
                        log_uid,
                        event_id,
                        ts_val,
                        str(item.get("level", "info") or "info").lower(),
                        str(item.get("category", "system") or "system"),
                        str(item.get("message", "-") or "-"),
                        str(item.get("source", "-") or "-"),
                        str(item.get("role", "-") or "-"),
                        str(item.get("status", "-") or "-"),
                        str(item.get("path", "-") or "-"),
                        str(item.get("event", "") or ""),
                        str(item.get("resource_type", "") or ""),
                        str(item.get("http_status", "") or ""),
                        str(item.get("auth_result", "") or ""),
                        str(item.get("trace_id", "") or ""),
                        str(item.get("request_id", "") or ""),
                        str(item.get("risk_level", "LOW") or "LOW").upper(),
                        json.dumps(risk_tags, ensure_ascii=False),
                        str(item.get("decision", "ALLOW") or "ALLOW").upper(),
                        str(item.get("reason_code", "GENERIC_EVENT") or "GENERIC_EVENT").upper(),
                        str(item.get("trigger_rule", "default") or "default"),
                        str(item.get("category", "access") or "access"),
                        filename,
                    )
                )
        if scanned_files >= max_files:
            break

    if not rows_to_insert:
        _SYSTEM_LOG_SYNC_HEALTH["last_finished_at"] = datetime.now().isoformat()
        _SYSTEM_LOG_SYNC_HEALTH["last_success_at"] = _SYSTEM_LOG_SYNC_HEALTH["last_finished_at"]
        _SYSTEM_LOG_SYNC_HEALTH["last_duration_ms"] = int((time.perf_counter() - started_perf) * 1000)
        _SYSTEM_LOG_SYNC_HEALTH["last_scanned_files"] = int(scanned_files)
        _SYSTEM_LOG_SYNC_HEALTH["last_scanned_lines"] = int(scanned_lines)
        _SYSTEM_LOG_SYNC_HEALTH["last_parsed_rows"] = 0
        _SYSTEM_LOG_SYNC_HEALTH["last_insert_candidates"] = 0
        _SYSTEM_LOG_SYNC_HEALTH["last_insert_rowcount"] = 0
        _SYSTEM_LOG_SYNC_HEALTH["last_error"] = ""
        return

    insert_sql = """
        INSERT INTO system_log_entries
        (log_uid,event_id,ts,level,category,message,source,role,status,path,event,resource_type,http_status,auth_result,trace_id,request_id,risk_level,risk_tags,decision,reason_code,trigger_rule,log_type,file_name)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)
        ON CONFLICT (log_uid) DO NOTHING
    """
    try:
        with kb_store._conn() as conn, conn.cursor() as cur:
            cur.executemany(insert_sql, rows_to_insert)
            insert_rowcount = int(cur.rowcount or 0)
    except Exception as exc:
        _SYSTEM_LOG_SYNC_HEALTH["total_failures"] = int(_SYSTEM_LOG_SYNC_HEALTH.get("total_failures", 0) or 0) + 1
        _SYSTEM_LOG_SYNC_HEALTH["last_error_at"] = datetime.now().isoformat()
        _SYSTEM_LOG_SYNC_HEALTH["last_error"] = str(exc)
        raise
    _SYSTEM_LOG_SYNC_HEALTH["last_finished_at"] = datetime.now().isoformat()
    _SYSTEM_LOG_SYNC_HEALTH["last_success_at"] = _SYSTEM_LOG_SYNC_HEALTH["last_finished_at"]
    _SYSTEM_LOG_SYNC_HEALTH["last_duration_ms"] = int((time.perf_counter() - started_perf) * 1000)
    _SYSTEM_LOG_SYNC_HEALTH["last_scanned_files"] = int(scanned_files)
    _SYSTEM_LOG_SYNC_HEALTH["last_scanned_lines"] = int(scanned_lines)
    _SYSTEM_LOG_SYNC_HEALTH["last_parsed_rows"] = int(len(rows_to_insert))
    _SYSTEM_LOG_SYNC_HEALTH["last_insert_candidates"] = int(len(rows_to_insert))
    _SYSTEM_LOG_SYNC_HEALTH["last_insert_rowcount"] = int(insert_rowcount)
    _SYSTEM_LOG_SYNC_HEALTH["last_error"] = ""


def _insert_system_log_entry_manual(
    *,
    category: str,
    message: str,
    source: str,
    role: str,
    status: str,
    path: str,
    event: str,
    resource_type: str,
    trace_id: str = "",
    request_id: str = "",
    risk_level: str = "LOW",
    decision: str = "ALLOW",
    reason_code: str = "GENERIC_EVENT",
    trigger_rule: str = "manual",
    level: str = "info",
) -> None:
    ts_val = datetime.now(timezone.utc)
    raw_uid = f"manual|{ts_val.isoformat()}|{source}|{event}|{message}|{trace_id}|{request_id}"
    log_uid = hashlib.sha1(raw_uid.encode("utf-8", errors="ignore")).hexdigest()
    event_id = f"manual-{log_uid[:16]}"
    insert_sql = """
        INSERT INTO system_log_entries
        (log_uid,event_id,ts,level,category,message,source,role,status,path,event,resource_type,http_status,auth_result,trace_id,request_id,risk_level,risk_tags,decision,reason_code,trigger_rule,log_type,file_name)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'','',%s,%s,%s,%s::jsonb,%s,%s,%s,'system','')
        ON CONFLICT (log_uid) DO NOTHING
    """
    with kb_store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            insert_sql,
            (
                log_uid,
                event_id,
                ts_val,
                str(level or "info").lower(),
                str(category or "system"),
                str(message or "-"),
                str(source or "execution-api"),
                str(role or "-"),
                str(status or "-"),
                str(path or "-"),
                str(event or ""),
                str(resource_type or ""),
                str(trace_id or ""),
                str(request_id or ""),
                str(risk_level or "LOW").upper(),
                "[]",
                str(decision or "ALLOW").upper(),
                str(reason_code or "GENERIC_EVENT").upper(),
                str(trigger_rule or "manual"),
            ),
        )


async def _ensure_system_log_index_ready(
    *,
    days: int = 7,
    file_names: list[str] | None = None,
    max_files: int = 40,
    max_lines_per_file: int = 2000,
    max_staleness_seconds: float = 20.0,
) -> None:
    now = time.time()
    if now - float(_SYSTEM_LOG_SYNC_LAST_TS or 0.0) <= float(max_staleness_seconds):
        return
    settings = _log_index_sync_settings()
    await asyncio.to_thread(
        _sync_system_log_index,
        days=max(1, min(int(days or int(settings["days"])), int(settings["days"]))),
        file_names=file_names,
        max_files=max_files if max_files > 0 else int(settings["max_files"]),
        max_lines_per_file=max_lines_per_file if max_lines_per_file > 0 else int(settings["max_lines_per_file"]),
        min_interval_seconds=float(settings["min_interval_seconds"]),
    )


async def _system_log_sync_loop() -> None:
    file_names = ["security.log", "auth.log", "error.log", "system.log"]
    while True:
        try:
            settings = _log_index_sync_settings()
            await asyncio.to_thread(
                _sync_system_log_index,
                days=int(settings["days"]),
                file_names=file_names,
                max_files=int(settings["max_files"]),
                max_lines_per_file=int(settings["max_lines_per_file"]),
                min_interval_seconds=float(settings["min_interval_seconds"]),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Keep background loop resilient; request path still has stale-check fallback.
            pass
        interval = float(_log_index_sync_settings()["interval_seconds"])
        await asyncio.sleep(interval)


def _archive_month_name(day_dt: datetime) -> str:
    return day_dt.strftime("%Y-%m")


def _run_log_retention_policy(config: dict | None = None) -> dict:
    conf = config or _load_runtime_config_json()
    log_root = _runtime_log_root(conf)
    hot_days = _runtime_log_hot_days(conf)
    archive_days = _runtime_log_archive_days(conf)
    archive_enabled = _runtime_log_archive_enabled(conf)
    expired_action = _runtime_log_expired_action(conf)
    now = datetime.now()
    archive_root = os.path.join(log_root, "_archive")
    os.makedirs(archive_root, exist_ok=True)

    archived_days = 0
    deleted_days = 0
    kept_days = 0

    for day_dt, day_dir in _list_log_day_dirs(log_root):
        age_days = (now.date() - day_dt.date()).days
        if age_days <= hot_days:
            kept_days += 1
            continue
        if age_days <= archive_days and archive_enabled:
            archive_name = f"{_archive_month_name(day_dt)}.zip"
            archive_path = os.path.join(archive_root, archive_name)
            try:
                with zipfile.ZipFile(archive_path, mode="a", compression=zipfile.ZIP_DEFLATED) as zf:
                    for root, _, files in os.walk(day_dir):
                        for fn in files:
                            full = os.path.join(root, fn)
                            rel = os.path.relpath(full, log_root)
                            zf.write(full, rel)
                shutil.rmtree(day_dir, ignore_errors=True)
                archived_days += 1
            except Exception:
                kept_days += 1
            continue
        if expired_action == "delete":
            shutil.rmtree(day_dir, ignore_errors=True)
            deleted_days += 1
        else:
            kept_days += 1

    return {
        "log_root": log_root,
        "hot_retention_days": hot_days,
        "archive_retention_days": archive_days,
        "archive_enabled": archive_enabled,
        "expired_action": expired_action,
        "archived_days": archived_days,
        "deleted_days": deleted_days,
        "kept_days": kept_days,
    }


def _list_log_archives(log_root: str) -> list[dict]:
    archive_root = os.path.join(log_root, "_archive")
    if not os.path.isdir(archive_root):
        return []
    items: list[dict] = []
    for name in sorted(os.listdir(archive_root), reverse=True):
        if not name.endswith(".zip"):
            continue
        full = os.path.join(archive_root, name)
        try:
            stat = os.stat(full)
            items.append(
                {
                    "name": name,
                    "size_bytes": int(stat.st_size),
                    "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                }
            )
        except Exception:
            continue
    return items


def _validate_chunk_settings(chunk_method: str, chunk_size: int, chunk_overlap: int, chunk_delimiter: str | None) -> tuple[int, int, str | None]:
    method = _validate_chunk_method(chunk_method)
    if method == "delimiter":
        delim = (chunk_delimiter or "").strip()
        if not delim:
            raise HTTPException(status_code=400, detail="chunk_delimiter is required when chunk_method=delimiter")
        return 0, 0, delim
    if chunk_size < 100 or chunk_size > 2000:
        raise HTTPException(status_code=400, detail="chunk_size must be between 100 and 2000")
    if chunk_overlap < 0 or chunk_overlap > 500:
        raise HTTPException(status_code=400, detail="chunk_overlap must be between 0 and 500")
    return chunk_size, chunk_overlap, None


def _extract_text_from_file(filename: str, raw: bytes) -> str:
    ext = os.path.splitext((filename or "").lower())[1]
    if ext in {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".xml"}:
        return _strip_nul(_decode_text_bytes(raw))
    if ext in {".html", ".htm"}:
        parser = _HtmlTextExtractor()
        parser.feed(_decode_text_bytes(raw))
        return _strip_nul(parser.get_text())
    if ext == ".pdf":
        reader = PdfReader(io.BytesIO(raw))
        parts: list[str] = []
        for page in reader.pages:
            parts.append(page.extract_text() or "")
        return _strip_nul("\n".join(parts))
    if ext == ".docx":
        doc = Document(io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        return _strip_nul("\n".join(parts))
    raise HTTPException(status_code=400, detail=f"Unsupported file type: {ext or 'unknown'}")


async def _read_upload_bounded(file: UploadFile, max_bytes: int) -> tuple[bytes, bool]:
    """Read at most max_bytes + 1 so rejection never requires an unbounded allocation."""
    limit = max(1, int(max_bytes))
    content = bytearray()
    chunk_bytes = 1024 * 1024
    while len(content) <= limit:
        remaining = limit + 1 - len(content)
        chunk = await file.read(min(chunk_bytes, remaining))
        if not chunk:
            break
        content.extend(chunk)
    return bytes(content), len(content) > limit


async def _persist_document_artifacts(
    *,
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    filename: str,
    source_bytes: bytes,
    extracted_text: str,
    source_content_type: str,
    chunk_method: str,
    chunk_size: int,
    chunk_overlap: int,
    chunk_delimiter: str | None,
) -> dict:
    await _enforce_minio_physical_capacity_for_write()
    source_key, extracted_key = object_storage.document_keys(tenant_id, kb_id, doc_id, filename)
    extracted_bytes = extracted_text.encode("utf-8")
    quota_bytes = _bounded_env_int(
        "OBJECT_STORAGE_TENANT_QUOTA_BYTES",
        0,
        0,
        10 * 1024 * 1024 * 1024 * 1024,
    )
    try:
        reservation = await asyncio.to_thread(
            kb_store.reserve_storage_quota,
            tenant_id=tenant_id,
            requested_bytes=len(source_bytes) + len(extracted_bytes),
            quota_bytes=quota_bytes,
            ttl_seconds=_bounded_env_int("OBJECT_STORAGE_QUOTA_RESERVATION_TTL_SECONDS", 300, 30, 3600),
            kb_id=kb_id,
            doc_id=doc_id,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "OBJECT_STORAGE_CAPACITY_UNAVAILABLE",
                "message": "object storage capacity check unavailable",
            },
        ) from exc
    if not reservation.get("allowed"):
        raise HTTPException(
            status_code=507,
            detail={
                "code": "OBJECT_STORAGE_QUOTA_EXCEEDED",
                "message": "tenant object storage quota exceeded",
                "used_bytes": int(reservation.get("used_bytes") or 0),
                "reserved_bytes": int(reservation.get("reserved_bytes") or 0),
                "requested_bytes": int(reservation.get("requested_bytes") or 0),
                "projected_bytes": int(reservation.get("projected_bytes") or 0),
                "quota_bytes": int(reservation.get("quota_bytes") or 0),
                "existing_bytes": int(reservation.get("existing_bytes") or 0),
                "capacity_delta_bytes": int(reservation.get("capacity_delta_bytes") or 0),
            },
        )
    reservation_id = str(reservation.get("reservation_id") or "")
    stored: list[tuple[str, str]] = []
    try:
        source = await asyncio.to_thread(
            object_storage.put_bytes,
            source_key,
            source_bytes,
            source_content_type or "application/octet-stream",
            {"tenant-scope": object_storage._scope_hash(tenant_id), "kb-scope": object_storage._scope_hash(kb_id)},
        )
        stored.append((source.bucket, source.key))
        extracted = await asyncio.to_thread(
            object_storage.put_bytes,
            extracted_key,
            extracted_bytes,
            "text/plain; charset=utf-8",
            {"source-sha256": source.sha256},
        )
        stored.append((extracted.bucket, extracted.key))
        kb_store.save_document(
            tenant_id,
            kb_id,
            doc_id,
            filename,
            extracted_text,
            chunk_method=chunk_method,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunk_delimiter=chunk_delimiter,
        )
        kb_store.set_document_object_metadata(
            tenant_id,
            kb_id,
            doc_id,
            source_bucket=source.bucket,
            source_key=source.key,
            source_sha256=source.sha256,
            source_size_bytes=source.size_bytes,
            source_content_type=source.content_type,
            extracted_key=extracted.key,
            extracted_sha256=extracted.sha256,
            extracted_size_bytes=extracted.size_bytes,
        )
        return {
            "object_bucket": extracted.bucket,
            "content_object_key": extracted.key,
            "content_object_sha256": extracted.sha256,
            "source_object_key": source.key,
            "source_object_sha256": source.sha256,
            "source_object_size_bytes": source.size_bytes,
            "extracted_object_size_bytes": extracted.size_bytes,
        }
    except Exception as exc:
        for bucket, key in reversed(stored):
            try:
                await asyncio.to_thread(object_storage.delete_object, bucket, key)
            except Exception as cleanup_exc:
                kb_store.enqueue_cross_store_repair(
                    store_name="MINIO",
                    operation="DELETE_OBJECT",
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    error_message=str(cleanup_exc),
                    payload={"bucket": bucket, "key": key},
                )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=503, detail="document object storage unavailable") from exc
    finally:
        if reservation_id:
            try:
                await asyncio.to_thread(kb_store.release_storage_quota, reservation_id)
            except Exception:
                # Reservations expire automatically. A release failure must not
                # turn an already committed document into a client-visible error.
                pass


def _build_parse_payload(
    *,
    request: Request,
    task_id: str,
    task_type: str,
    kb_id: str,
    doc_id: str,
    filename: str,
    content: str,
    chunk_method: str,
    chunk_size: int,
    chunk_overlap: int,
    chunk_delimiter: str | None,
    auto_summary: bool = False,
    batch_id: str | None = None,
    batch_name: str | None = None,
) -> dict:
    return {
        "task_id": task_id,
        "task_type": task_type,
        "tenant_id": request.state.tenant_id,
        "actor_id": request.state.actor_id,
        "trace_id": request.state.trace_id,
        "source_service": request.state.source_service,
        "kb_id": kb_id,
        "doc_id": doc_id,
        "filename": filename,
        "content": content,
        "chunk_method": chunk_method,
        "chunk_delimiter": chunk_delimiter,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "auto_summary": bool(auto_summary),
        "batch_id": batch_id or "",
        "batch_name": batch_name or "",
    }


def _try_acquire_submission_lock(
    *,
    request: Request,
    kb_id: str,
    doc_id: str,
    task_id: str,
    task_type: str,
    lock_scope: str,
) -> Dict | None:
    lock = kb_store.acquire_task_dedup_lock(
        tenant_id=request.state.tenant_id,
        kb_id=kb_id,
        doc_id=doc_id or "",
        lock_scope=lock_scope,
        task_id=task_id,
    )
    if bool(lock.get("acquired")):
        return None
    existing_task_id = str(lock.get("task_id") or "")
    if not existing_task_id:
        raise HTTPException(status_code=409, detail="task lock exists but no active task id")
    return {
        "status": "accepted",
        "task_id": existing_task_id,
        "task_type": task_type,
        "deduplicated": True,
        "lock_scope": lock_scope,
        "trace_id": request.state.trace_id,
    }


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    return request.url.path


@app.middleware("http")
async def context_middleware(request: Request, call_next):
    started_at = time.perf_counter()
    request.state.request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    request.state.tenant_id = request.headers.get("X-Tenant-Id", "platform")
    request.state.actor_id = request.headers.get("X-Actor-Id", "system")
    request.state.trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
    request.state.source_service = request.headers.get("X-Source-Service", "execution-api")
    path_template = _route_template(request)
    body_summary = "-"
    try:
        body_summary = await summarize_body(request, path_template)
    except Exception:
        body_summary = "kind=inspect_failed"

    response = None
    error_class = ""
    error_reason = ""
    try:
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response
    except Exception as exc:
        error_class = exc.__class__.__name__
        error_reason = str(exc)
        raise
    finally:
        status_code = int(getattr(response, "status_code", 500) or 500)
        if response is not None and status_code >= 400:
            content_type = response.headers.get("content-type", "")
            body = getattr(response, "body", b"") or b""
            cls, reason = summarize_error_response_body(status_code, content_type, body)
            if cls and not error_class:
                error_class = cls
            if reason and not error_reason:
                error_reason = reason
        if not error_reason:
            error_reason = str(getattr(request.state, "audit_error_reason", "")) or ""
        if not error_class:
            error_class = str(getattr(request.state, "audit_error_class", "")) or ""

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        path_used = _route_template(request) or path_template
        record = build_base_record(
            request=request,
            path_template=path_used,
            body_summary=str(getattr(request.state, "audit_body_summary", body_summary)),
            response_status=status_code,
            duration_ms=duration_ms,
            error_class=error_class,
            error_reason=error_reason,
        )
        audit_sink.emit("access", record)
        for log_type in extra_log_types(record.get("event", ""), record.get("outcome", ""), record.get("auth_result", "")):
            audit_sink.emit(log_type, record)


@app.on_event("startup")
async def load_api_pool() -> None:
    global _SYSTEM_LOG_SYNC_TASK
    require_database_current()
    api_pool_manager.bootstrap()
    aux_api_pool_manager.bootstrap()
    audit_sink.start()
    if _SYSTEM_LOG_SYNC_TASK is None or _SYSTEM_LOG_SYNC_TASK.done():
        _SYSTEM_LOG_SYNC_TASK = asyncio.create_task(_system_log_sync_loop())


@app.on_event("shutdown")
async def stop_audit_sink() -> None:
    global _SYSTEM_LOG_SYNC_TASK
    if _SYSTEM_LOG_SYNC_TASK is not None:
        _SYSTEM_LOG_SYNC_TASK.cancel()
        try:
            await _SYSTEM_LOG_SYNC_TASK
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        _SYSTEM_LOG_SYNC_TASK = None
    audit_sink.stop()


def require_admin(request: Request) -> None:
    _ = _require_roles(request, {"PLATFORM_ADMIN", "KB_MANAGER"}, "kb:write")


def require_internal_token(request: Request) -> None:
    supplied = request.headers.get("X-Internal-Token", "")
    if not CONTROL_PLANE_INTERNAL_TOKEN or not hmac.compare_digest(supplied, CONTROL_PLANE_INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid internal token")


def require_alertmanager_webhook(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    supplied = auth.split(" ", 1)[1].strip() if auth.startswith("Bearer ") else ""
    if (
        not ALERTMANAGER_WEBHOOK_TOKEN
        or not supplied
        or not hmac.compare_digest(supplied, ALERTMANAGER_WEBHOOK_TOKEN)
    ):
        raise HTTPException(status_code=401, detail="Invalid Alertmanager webhook token")


def _sign_token(payload: dict) -> str:
    return JWT_KEY_RING.sign(payload)


def _verify_token_details(token: str) -> JwtVerification:
    try:
        return JWT_KEY_RING.verify(token)
    except JwtTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _verify_token(token: str) -> dict:
    return _verify_token_details(token).payload


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth.split(" ", 1)[1].strip()
        if token:
            return token
    query_token = request.query_params.get("token", "").strip()
    if query_token:
        return query_token
    raise HTTPException(status_code=401, detail="Missing bearer token")


def _require_roles(request: Request, allowed_roles: set[str], permission: str) -> dict:
    request.state.audit_permission = permission
    try:
        token = _extract_bearer_token(request)
        verification = _verify_token_details(token)
        payload = verification.payload
    except HTTPException as exc:
        request.state.audit_auth_result = "DENY"
        request.state.audit_error_class = "AuthError"
        request.state.audit_error_reason = str(exc.detail)
        raise

    role = str(payload.get("role", ""))
    request.state.auth_claims = payload
    request.state.auth_key_slot = verification.key_slot
    request.state.auth_legacy_token = verification.legacy_format
    request.state.audit_actor_role = role
    if role not in allowed_roles:
        request.state.audit_auth_result = "DENY"
        request.state.audit_error_class = "PermissionDenied"
        request.state.audit_error_reason = "Insufficient role permission"
        raise HTTPException(status_code=403, detail="Insufficient role permission")
    request.state.audit_auth_result = "ALLOW"
    return payload


def require_kb_read(request: Request) -> dict:
    return _require_roles(request, {"PLATFORM_ADMIN", "KB_MANAGER", "AUDITOR"}, "kb:read")


def require_kb_write(request: Request) -> dict:
    claims = _require_roles(request, {"PLATFORM_ADMIN", "KB_MANAGER"}, "kb:write")
    enabled = os.getenv(LEGACY_KB_WRITE_ENV, "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        request.state.audit_error_class = "LegacyWriteRetired"
        request.state.audit_error_reason = "write through Control Plane"
        raise HTTPException(
            status_code=410,
            detail={
                "code": "LEGACY_KB_WRITE_RETIRED",
                "message": "Execution KB write endpoints are retired; use the Control Plane API",
            },
        )
    return claims


def require_platform_admin(request: Request) -> dict:
    return _require_roles(request, {"PLATFORM_ADMIN"}, "platform:admin")


@app.get("/api/v1/system/health")
async def health() -> Dict[str, str]:
    return {
        "status": "ok",
        "service": "execution-api",
        "embedding_mode": (os.getenv("EMBEDDING_MODE", "api_strict") or "api_strict").strip().lower(),
    }


@app.get("/api/v1/system/context")
async def context(request: Request) -> Dict[str, str]:
    return {
        "tenant_id": request.state.tenant_id,
        "actor_id": request.state.actor_id,
        "trace_id": request.state.trace_id,
        "source_service": request.state.source_service,
    }


@app.post("/api/v1/admin/auth/login", response_model=ApiAdminLoginResponse)
async def admin_login(body: ApiAdminLoginRequest, request: Request) -> ApiAdminLoginResponse:
    request.state.audit_permission = "kb:auth:login"
    request.state.audit_actor_role = ADMIN_ROLE
    request.state.audit_auth_result = "ALLOW"
    request.state.actor_id = body.username
    if not hmac.compare_digest(body.username, ADMIN_USERNAME) or not hmac.compare_digest(body.password, ADMIN_PASSWORD):
        request.state.audit_auth_result = "DENY"
        request.state.audit_error_class = "AuthError"
        request.state.audit_error_reason = "Invalid admin credentials"
        raise HTTPException(status_code=401, detail="Invalid admin credentials")
    now = int(time.time())
    payload = {
        "sub": body.username,
        "role": ADMIN_ROLE,
        "iat": now,
        "exp": now + JWT_TTL_SECONDS,
    }
    return ApiAdminLoginResponse(access_token=_sign_token(payload), role=ADMIN_ROLE)


@app.post("/api/v1/admin/auth/logout")
async def admin_logout(request: Request, _: dict = Depends(require_kb_read)) -> Dict:
    return {"status": "ok", "message": "logged out", "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/workflow-secrets")
async def list_workflow_secrets(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    return {
        "status": "ok",
        "vault_available": workflow_secret_store.available,
        "items": workflow_secret_store.list_metadata(request.state.tenant_id),
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/workflow-secrets")
async def create_workflow_secret(
    body: ApiWorkflowSecretCreateRequest,
    request: Request,
    claims: dict = Depends(require_platform_admin),
) -> Dict:
    try:
        item = workflow_secret_store.create(
            tenant_id=request.state.tenant_id,
            name=body.name,
            description=body.description,
            value=body.value,
            actor_id=str(claims.get("sub") or request.state.actor_id),
        )
    except SecretVaultUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409 if "already exists" in str(exc) else 400, detail=str(exc)) from exc
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.put("/api/v1/admin/workflow-secrets/{secret_id}")
async def rotate_workflow_secret(
    secret_id: str,
    body: ApiWorkflowSecretRotateRequest,
    request: Request,
    claims: dict = Depends(require_platform_admin),
) -> Dict:
    try:
        item = workflow_secret_store.rotate(
            tenant_id=request.state.tenant_id,
            secret_id=secret_id,
            value=body.value,
            description=body.description,
            actor_id=str(claims.get("sub") or request.state.actor_id),
        )
    except SecretVaultUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except WorkflowSecretNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/workflow-secrets/{secret_id}/disable")
async def disable_workflow_secret(
    secret_id: str,
    request: Request,
    claims: dict = Depends(require_platform_admin),
) -> Dict:
    try:
        item = workflow_secret_store.disable(
            tenant_id=request.state.tenant_id,
            secret_id=secret_id,
            actor_id=str(claims.get("sub") or request.state.actor_id),
        )
    except WorkflowSecretNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/workflow-secrets/{secret_id}")
async def delete_workflow_secret(
    secret_id: str,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    try:
        workflow_secret_store.delete(tenant_id=request.state.tenant_id, secret_id=secret_id)
    except WorkflowSecretNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "deleted": secret_id, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/kbs")
async def list_kbs(request: Request, _: dict = Depends(require_kb_read)) -> Dict:
    items = kb_store.list_accessible_kbs(request.state.tenant_id)
    return {"status": "ok", "items": items, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/kbs")
async def create_kb(body: ApiCreateKbRequest, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    kb_id = str(uuid.uuid4())
    item = kb_store.create_kb(request.state.tenant_id, kb_id, body.name, "")
    return {
        "status": "ok",
        "item": item,
        "name_adjusted": (item.get("name") != body.name.strip()),
        "trace_id": request.state.trace_id,
    }


@app.patch("/api/v1/admin/kbs/{kb_id}")
async def rename_kb(kb_id: str, body: ApiRenameKbRequest, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    item = kb_store.rename_kb(request.state.tenant_id, kb_id, body.name)
    if not item:
        raise HTTPException(status_code=404, detail="kb not found")
    return {
        "status": "ok",
        "item": item,
        "name_adjusted": (item.get("name") != body.name.strip()),
        "trace_id": request.state.trace_id,
    }


@app.delete("/api/v1/admin/kbs/{kb_id}")
async def delete_kb(kb_id: str, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    deleted = kb_store.delete_kb(request.state.tenant_id, kb_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="kb not found")
    _schedule_cross_store_repair()
    return {"status": "ok", "deleted": kb_id, "trace_id": request.state.trace_id}


@app.patch("/api/v1/admin/kbs/{kb_id}/freeze")
async def freeze_kb(kb_id: str, body: ApiFreezeRequest, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    item = kb_store.set_kb_frozen(request.state.tenant_id, kb_id, bool(body.frozen))
    if not item:
        raise HTTPException(status_code=404, detail="kb not found")
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.patch("/api/v1/admin/kbs/{kb_id}/visibility")
async def set_kb_visibility(kb_id: str, body: ApiKbVisibilityRequest, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    try:
        item = kb_store.set_kb_visibility(request.state.tenant_id, kb_id, body.visibility)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="kb not found")
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/kbs/{kb_id}/bindings")
async def list_kb_bindings(kb_id: str, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    item = kb_store.get_kb(request.state.tenant_id, kb_id)
    if not item:
        raise HTTPException(status_code=404, detail="kb not found")
    items = kb_store.list_kb_bindings(request.state.tenant_id, kb_id)
    return {"status": "ok", "kb_id": kb_id, "items": items, "trace_id": request.state.trace_id}


@app.put("/api/v1/admin/kbs/{kb_id}/bindings")
async def upsert_kb_binding(kb_id: str, body: ApiKbBindingRequest, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    try:
        item = kb_store.upsert_kb_binding(
            owner_tenant_id=request.state.tenant_id,
            kb_id=kb_id,
            target_tenant_id=body.target_tenant_id,
            actor_id=request.state.actor_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="kb not found")
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/kbs/{kb_id}/bindings/{target_tenant_id}")
async def delete_kb_binding(kb_id: str, target_tenant_id: str, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    ok = kb_store.delete_kb_binding(
        owner_tenant_id=request.state.tenant_id,
        kb_id=kb_id,
        target_tenant_id=target_tenant_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="binding not found")
    return {"status": "ok", "kb_id": kb_id, "target_tenant_id": target_tenant_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/kbs/{kb_id}/summarize")
async def summarize_kb(kb_id: str, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    kb_item = kb_store.get_kb(request.state.tenant_id, kb_id)
    if not kb_item:
        raise HTTPException(status_code=404, detail="kb not found")
    task_id = str(uuid.uuid4())
    dedup = _try_acquire_submission_lock(
        request=request,
        kb_id=kb_id,
        doc_id="",
        task_id=task_id,
        task_type="KB_SUMMARIZE",
        lock_scope="KB_SUMMARY",
    )
    if dedup:
        dedup["kb_id"] = kb_id
        return dedup
    payload = {
        "task_id": task_id,
        "task_type": "KB_SUMMARIZE",
        "tenant_id": request.state.tenant_id,
        "actor_id": request.state.actor_id,
        "trace_id": request.state.trace_id,
        "source_service": request.state.source_service,
        "kb_id": kb_id,
        "kb_name": kb_item.get("name", kb_id),
    }
    kb_store.create_task_record(
        task_id,
        request.state.tenant_id,
        kb_id,
        "",
        "KB_SUMMARIZE",
        "SUMMARIZING",
        filename=kb_item.get("name", kb_id),
    )
    _send_persistent_task("app.tasks.summarize_kb", payload, "parse_queue", task_id)
    return {"status": "accepted", "task_id": task_id, "task_type": "KB_SUMMARIZE", "kb_id": kb_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/kbs/{kb_id}/documents/ingest")
async def ingest(kb_id: str, body: ApiIngestRequest, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    content_bytes = body.content.encode("utf-8")
    max_text_bytes = _bounded_env_int("INGEST_MAX_TEXT_BYTES", 10 * 1024 * 1024, 1024, 200 * 1024 * 1024)
    if len(content_bytes) > max_text_bytes:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "INGEST_TEXT_TOO_LARGE",
                "message": "ingest text exceeds configured byte limit",
                "size_bytes": len(content_bytes),
                "max_bytes": max_text_bytes,
            },
        )
    kb_store.ensure_kb(request.state.tenant_id, kb_id)
    doc_id = body.doc_id or str(uuid.uuid4())
    filename = body.filename or f"{doc_id}.txt"
    task_id = str(uuid.uuid4())
    dedup = _try_acquire_submission_lock(
        request=request,
        kb_id=kb_id,
        doc_id=doc_id,
        task_id=task_id,
        task_type="DOC_INGEST",
        lock_scope="DOC_PARSE",
    )
    if dedup:
        dedup["kb_id"] = kb_id
        dedup["doc_id"] = doc_id
        return dedup
    try:
        object_ref = await _persist_document_artifacts(
            tenant_id=request.state.tenant_id,
            kb_id=kb_id,
            doc_id=doc_id,
            filename=filename,
            source_bytes=content_bytes,
            extracted_text=body.content,
            source_content_type="text/plain; charset=utf-8",
            chunk_method="fixed",
            chunk_size=body.chunk_size,
            chunk_overlap=body.chunk_overlap,
            chunk_delimiter=None,
        )
    except Exception:
        kb_store.release_task_dedup_locks(task_id)
        raise
    kb_store.set_document_status(request.state.tenant_id, kb_id, doc_id, "UPLOADED_RAW")
    payload = {
        "task_id": task_id,
        "task_type": "DOC_INGEST",
        "tenant_id": request.state.tenant_id,
        "actor_id": request.state.actor_id,
        "trace_id": request.state.trace_id,
        "source_service": request.state.source_service,
        "kb_id": kb_id,
        "doc_id": doc_id,
        "filename": filename,
        "content": "",
        "chunk_method": "fixed",
        "chunk_size": body.chunk_size,
        "chunk_overlap": body.chunk_overlap,
        "auto_summary": body.auto_summary,
        **object_ref,
    }
    kb_store.create_task_record(task_id, request.state.tenant_id, kb_id, doc_id, "DOC_INGEST", "PARSING", filename=filename)
    _send_persistent_task("app.tasks.parse_document", payload, "parse_queue", task_id)
    return {"status": "accepted", "task_id": task_id, "kb_id": kb_id, "doc_id": doc_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/kbs/{kb_id}/documents/upload")
async def upload_documents(
    kb_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    process_mode: str = Form(default="UPLOAD_CLEAN_CHUNK_EMBED"),
    chunk_method: str = Form(default="sentence"),
    chunk_delimiter: str | None = Form(default=None),
    chunk_size: int = Form(default=500),
    chunk_overlap: int = Form(default=50),
    auto_summary: bool = Form(default=False),
    _: dict = Depends(require_kb_write),
) -> Dict:
    allowed_process_modes = {"UPLOAD_ONLY", "UPLOAD_AND_CLEAN", "UPLOAD_CLEAN_CHUNK_EMBED"}
    if process_mode not in allowed_process_modes:
        raise HTTPException(status_code=400, detail=f"invalid process_mode, choose from {sorted(allowed_process_modes)}")
    chunk_size, chunk_overlap, chunk_delimiter = _validate_chunk_settings(
        chunk_method=chunk_method,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_delimiter=chunk_delimiter,
    )
    chunk_method = _validate_chunk_method(chunk_method)
    kb_store.ensure_kb(request.state.tenant_id, kb_id)
    upload_settings = kb_store.get_upload_settings()
    max_file_size_mb = int(upload_settings.get("max_file_size_mb", 50))
    max_file_size_bytes = max_file_size_mb * 1024 * 1024
    max_files = _bounded_env_int("UPLOAD_MAX_FILES_PER_REQUEST", 20, 1, 200)
    max_batch_bytes = _bounded_env_int("UPLOAD_MAX_BATCH_BYTES", 200 * 1024 * 1024, 1024, 10 * 1024 * 1024 * 1024)
    accepted: list[dict] = []
    skipped: list[dict] = []
    total_bytes = 0
    ext_set: set[str] = set()
    for file_index, file in enumerate(files):
        if not file.filename:
            skipped.append({"filename": "", "reason": "missing filename"})
            continue
        if file_index >= max_files:
            skipped.append({"filename": file.filename, "reason": f"too many files (> {max_files})"})
            continue
        remaining_batch_bytes = max_batch_bytes - total_bytes
        if remaining_batch_bytes <= 0:
            skipped.append({"filename": file.filename, "reason": "upload batch byte limit reached"})
            continue
        read_limit = min(max_file_size_bytes, remaining_batch_bytes)
        raw, exceeded_limit = await _read_upload_bounded(file, read_limit)
        total_bytes += min(len(raw), read_limit)
        ext_set.add((os.path.splitext(file.filename.lower())[1] or "unknown").lstrip("."))
        if exceeded_limit:
            reason = (
                f"file too large (> {max_file_size_mb}MB)"
                if read_limit == max_file_size_bytes
                else f"upload batch too large (> {round(max_batch_bytes / (1024 * 1024), 3)}MB)"
            )
            skipped.append({"filename": file.filename, "reason": reason})
            continue
        if not raw:
            skipped.append({"filename": file.filename, "reason": "empty file"})
            continue
        try:
            text = _extract_text_from_file(file.filename, raw).strip()
        except HTTPException as exc:
            detail = exc.detail
            skipped_item = {
                "filename": file.filename,
                "status_code": int(exc.status_code),
                "reason": str(detail.get("message") or detail.get("code") or "upload rejected")
                if isinstance(detail, dict)
                else str(detail),
            }
            if isinstance(detail, dict):
                skipped_item["error_code"] = str(detail.get("code") or "")
                skipped_item["details"] = detail
            skipped.append(skipped_item)
            continue
        except Exception as exc:
            skipped.append({"filename": file.filename, "reason": f"parse failed: {exc}"})
            continue
        if not text:
            skipped.append({"filename": file.filename, "reason": "no extractable text"})
            continue
        text_to_store = preprocess_text_for_chunk(text) if process_mode == "UPLOAD_AND_CLEAN" else text
        doc_id = str(uuid.uuid4())
        task_id = None
        try:
            object_ref = await _persist_document_artifacts(
                tenant_id=request.state.tenant_id,
                kb_id=kb_id,
                doc_id=doc_id,
                filename=file.filename,
                source_bytes=raw,
                extracted_text=text_to_store,
                source_content_type=file.content_type or "application/octet-stream",
                chunk_method=chunk_method,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                chunk_delimiter=chunk_delimiter,
            )
        except HTTPException as exc:
            detail = exc.detail
            skipped_item = {
                "filename": file.filename,
                "status_code": int(exc.status_code),
                "reason": str(detail.get("message") or detail.get("code") or "upload rejected")
                if isinstance(detail, dict)
                else str(detail),
            }
            if isinstance(detail, dict):
                skipped_item["error_code"] = str(detail.get("code") or "")
                skipped_item["details"] = detail
            skipped.append(skipped_item)
            continue
        kb_store.set_document_status(request.state.tenant_id, kb_id, doc_id, "UPLOADED_RAW")
        if process_mode == "UPLOAD_ONLY":
            pass
        elif process_mode == "UPLOAD_AND_CLEAN":
            kb_store.set_document_status(request.state.tenant_id, kb_id, doc_id, "CLEANED")
        else:
            task_id = str(uuid.uuid4())
            dedup = _try_acquire_submission_lock(
                request=request,
                kb_id=kb_id,
                doc_id=doc_id,
                task_id=task_id,
                task_type="DOC_INGEST",
                lock_scope="DOC_PARSE",
            )
            if dedup:
                accepted.append({
                    "filename": file.filename,
                    "doc_id": doc_id,
                    "task_id": dedup.get("task_id"),
                    "process_mode": process_mode,
                    "deduplicated": True,
                })
                continue
            payload = {
                "task_id": task_id,
                "task_type": "DOC_INGEST",
                "tenant_id": request.state.tenant_id,
                "actor_id": request.state.actor_id,
                "trace_id": request.state.trace_id,
                "source_service": request.state.source_service,
                "kb_id": kb_id,
                "doc_id": doc_id,
                "filename": file.filename,
                "content": "",
                "chunk_method": chunk_method,
                "chunk_delimiter": chunk_delimiter,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "auto_summary": auto_summary,
                **object_ref,
            }
            kb_store.create_task_record(task_id, request.state.tenant_id, kb_id, doc_id, "DOC_INGEST", "PARSING", filename=file.filename)
            _send_persistent_task("app.tasks.parse_document", payload, "parse_queue", task_id)
        accepted.append({"filename": file.filename, "doc_id": doc_id, "task_id": task_id, "process_mode": process_mode})
    request.state.audit_body_summary = (
        f"kind=multipart;file_count={len(files)};total_size_mb={round(total_bytes / (1024 * 1024), 3)};"
        f"ext={','.join(sorted(ext_set)) if ext_set else '-'};process_mode={process_mode};chunk_method={chunk_method};"
        f"auto_summary={str(auto_summary).lower()};"
        f"chunk_delimiter={('set' if chunk_delimiter else '-')};"
        f"chunk_size={chunk_size};chunk_overlap={chunk_overlap}"
    )
    return {
        "status": "accepted" if accepted else "rejected",
        "kb_id": kb_id,
        "accepted_count": len(accepted),
        "skipped_count": len(skipped),
        "max_file_size_mb": max_file_size_mb,
        "max_files_per_request": max_files,
        "max_batch_size_bytes": max_batch_bytes,
        "accepted": accepted,
        "skipped": skipped,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/system/upload-settings")
async def get_upload_settings(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    settings = kb_store.get_upload_settings()
    return {"status": "ok", "item": settings, "trace_id": request.state.trace_id}


@app.put("/api/v1/admin/system/upload-settings")
async def update_upload_settings(
    body: ApiUploadSettingsRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    item = kb_store.set_upload_settings(body.max_file_size_mb, request.state.actor_id)
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/system/runtime-config")
async def get_runtime_config(
    request: Request,
    mask_secret: bool = False,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    default_data = _load_json_dict(_runtime_config_default_path()) or _load_json_dict(_runtime_config_legacy_path())
    effective = kb_store.get_config_effective(scope_type="PLATFORM", scope_id="platform")
    item = effective.get("item") or {}
    source = effective.get("source") or {}
    file_cfg = _load_runtime_config_json()
    for key in NON_VERSIONED_CONFIG_KEYS:
        if key in file_cfg:
            item[key] = file_cfg.get(key)
            source[key] = "file:non_versioned"
    safe_items = _runtime_config_safe_items(item) if mask_secret else {k: item.get(k) for k in sorted(RUNTIME_CONFIG_ALLOWED_KEYS) if k in item}
    safe_defaults = {k: default_data.get(k) for k in sorted(RUNTIME_CONFIG_ALLOWED_KEYS) if k in default_data}
    return {
        "status": "ok",
        "item": safe_items,
        "defaults": safe_defaults,
        "source": source,
        "allowed_keys": sorted(RUNTIME_CONFIG_ALLOWED_KEYS),
        "default_path": _runtime_config_default_path(),
        "user_path": _runtime_config_user_path(),
        "trace_id": request.state.trace_id,
    }


@app.patch("/api/v1/admin/system/runtime-config")
async def patch_runtime_config(
    body: ApiRuntimeConfigUpdateRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    updates = body.updates or {}
    if not updates:
        raise HTTPException(status_code=400, detail="updates cannot be empty")
    illegal = [k for k in updates.keys() if k not in RUNTIME_CONFIG_ALLOWED_KEYS]
    if illegal:
        raise HTTPException(status_code=400, detail=f"unsupported keys: {', '.join(sorted(illegal))}")
    validation = _validate_config_updates(updates=updates, base_item=_load_runtime_config_json())
    if not validation.get("valid", False):
        raise HTTPException(
            status_code=422,
            detail={"message": "config validation failed", "errors": validation.get("errors") or [], "warnings": validation.get("warnings") or []},
        )
    versioned_updates = {k: v for k, v in updates.items() if k in RUNTIME_CONFIG_VERSIONED_KEYS}
    non_versioned_updates = {k: v for k, v in updates.items() if k in NON_VERSIONED_CONFIG_KEYS}
    if body.versioned is False and versioned_updates:
        raise HTTPException(status_code=400, detail=f"versioned=false only allows: {', '.join(sorted(NON_VERSIONED_CONFIG_KEYS))}")
    if body.versioned and versioned_updates and body.expected_latest_release_id is not None:
        latest_id = kb_store.get_latest_config_release_id(scope_type="PLATFORM", scope_id="platform")
        expected = int(body.expected_latest_release_id or 0)
        if expected != latest_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "config release conflict, please refresh before saving",
                    "expected_latest_release_id": expected,
                    "actual_latest_release_id": latest_id,
                },
            )

    # Compatibility path: keep runtime-config.user.json as mirror.
    current = _load_runtime_config_json()
    for k, v in non_versioned_updates.items():
        current[k] = v
    if non_versioned_updates:
        _save_runtime_config_json(current)

    patched = {"changed": []}
    published = {"release_id": 0, "changed_keys": []}
    if versioned_updates and body.versioned:
        patched = kb_store.patch_config_draft(
            scope_type="PLATFORM",
            scope_id="platform",
            updates=versioned_updates,
            actor_id=request.state.actor_id,
            trace_id=request.state.trace_id,
        )
        published = kb_store.publish_config(
            scope_type="PLATFORM",
            scope_id="platform",
            changed_keys=sorted(list(versioned_updates.keys())),
            actor_id=request.state.actor_id,
            trace_id=request.state.trace_id,
            note="runtime-config compatibility patch",
        )
        try:
            _sync_runtime_config_user_from_config_center()
        except Exception:
            pass
    effective = kb_store.get_config_effective(scope_type="PLATFORM", scope_id="platform")
    item = effective.get("item") or {}
    if non_versioned_updates:
        file_now = _load_runtime_config_json()
        for key in NON_VERSIONED_CONFIG_KEYS:
            if key in file_now:
                item[key] = file_now.get(key)
    mismatch_keys: list[str] = []
    for key, val in updates.items():
        if json.dumps(item.get(key), ensure_ascii=False, sort_keys=True) != json.dumps(val, ensure_ascii=False, sort_keys=True):
            mismatch_keys.append(key)
    return {
        "status": "ok",
        "item": _runtime_config_safe_items(item),
        "updated_keys": sorted(list(updates.keys())),
        "verify": {"ok": len(mismatch_keys) == 0, "mismatch_keys": mismatch_keys},
        "patched": {"changed": len(patched.get("changed") or [])},
        "published": published,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/config/schema")
async def list_config_schema(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    seeded = _ensure_config_schema_seeded()
    rows = _enrich_config_schema_rows(kb_store.list_config_schema())
    return {"status": "ok", "items": rows, "seed": seeded, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/config/effective")
async def get_config_effective(
    request: Request,
    scope_type: str = "PLATFORM",
    scope_id: str = "platform",
    mask_secret: bool = True,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    result = kb_store.get_config_effective(scope_type=scope_type, scope_id=scope_id)
    item = result.get("item") or {}
    for key in NON_VERSIONED_CONFIG_KEYS:
        item.pop(key, None)
    if mask_secret:
        item = {k: (_mask_secret_value(v) if _is_secret_config_key(k) else v) for k, v in item.items()}
    result["item"] = item
    result["status"] = "ok"
    result["trace_id"] = request.state.trace_id
    return result


@app.post("/api/v1/admin/config/validate")
async def validate_config_updates(
    body: ApiConfigValidateRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    updates = body.updates or {}
    illegal = [k for k in updates.keys() if k not in RUNTIME_CONFIG_ALLOWED_KEYS]
    if illegal:
        return {
            "status": "ok",
            "valid": False,
            "errors": [{"key": k, "code": "unsupported_key", "message": f"unsupported key: {k}"} for k in sorted(illegal)],
            "warnings": [],
            "trace_id": request.state.trace_id,
        }
    base_item = (kb_store.get_config_effective(scope_type=body.scope_type, scope_id=body.scope_id) or {}).get("item") or {}
    file_cfg = _load_runtime_config_json()
    for key in NON_VERSIONED_CONFIG_KEYS:
        if key in file_cfg:
            base_item[key] = file_cfg.get(key)
    result = _validate_config_updates(updates=updates, base_item=base_item)
    return {"status": "ok", **result, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/config/impact")
async def get_config_impact(
    body: ApiConfigImpactRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    updates = body.updates or {}
    illegal = [k for k in updates.keys() if k not in RUNTIME_CONFIG_ALLOWED_KEYS]
    if illegal:
        raise HTTPException(status_code=400, detail=f"unsupported keys: {', '.join(sorted(illegal))}")
    base_item = (kb_store.get_config_effective(scope_type=body.scope_type, scope_id=body.scope_id) or {}).get("item") or {}
    file_cfg = _load_runtime_config_json()
    for key in NON_VERSIONED_CONFIG_KEYS:
        if key in file_cfg:
            base_item[key] = file_cfg.get(key)
    validation = _validate_config_updates(updates=updates, base_item=base_item)
    impact = _estimate_config_impact(updates=updates)
    return {"status": "ok", "valid": validation.get("valid", True), "errors": validation.get("errors", []), "warnings": validation.get("warnings", []), "impact": impact, "trace_id": request.state.trace_id}


@app.patch("/api/v1/admin/config/draft")
async def patch_config_draft(
    body: ApiConfigDraftPatchRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    updates = body.updates or {}
    if not updates:
        raise HTTPException(status_code=400, detail="updates cannot be empty")
    illegal = [k for k in updates.keys() if k not in RUNTIME_CONFIG_ALLOWED_KEYS]
    if illegal:
        raise HTTPException(status_code=400, detail=f"unsupported keys: {', '.join(sorted(illegal))}")
    validation = _validate_config_updates(
        updates=updates,
        base_item=(kb_store.get_config_effective(scope_type=body.scope_type, scope_id=body.scope_id) or {}).get("item") or {},
    )
    if not validation.get("valid", False):
        raise HTTPException(
            status_code=422,
            detail={"message": "config validation failed", "errors": validation.get("errors") or [], "warnings": validation.get("warnings") or []},
        )
    non_versioned = [k for k in updates.keys() if k in NON_VERSIONED_CONFIG_KEYS]
    if non_versioned:
        raise HTTPException(status_code=400, detail=f"non-versioned keys are not allowed in config center: {', '.join(sorted(non_versioned))}")
    if body.expected_latest_release_id is not None:
        latest_id = kb_store.get_latest_config_release_id(scope_type=body.scope_type, scope_id=body.scope_id)
        expected = int(body.expected_latest_release_id or 0)
        if expected != latest_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "config release conflict, please refresh before saving",
                    "expected_latest_release_id": expected,
                    "actual_latest_release_id": latest_id,
                },
            )
    result = kb_store.patch_config_draft(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        updates=updates,
        actor_id=request.state.actor_id,
        trace_id=request.state.trace_id,
    )
    return {
        "status": "ok",
        "scope_type": result.get("scope_type"),
        "scope_id": result.get("scope_id"),
        "changed_keys": [item.get("key") for item in (result.get("changed") or [])],
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/config/publish")
async def publish_config(
    body: ApiConfigPublishRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    changed_keys = sorted({k for k in (body.changed_keys or []) if str(k).strip()})
    if not changed_keys:
        raise HTTPException(status_code=400, detail="changed_keys cannot be empty")
    illegal = [k for k in changed_keys if k not in RUNTIME_CONFIG_ALLOWED_KEYS]
    if illegal:
        raise HTTPException(status_code=400, detail=f"unsupported keys: {', '.join(sorted(illegal))}")
    non_versioned = [k for k in changed_keys if k in NON_VERSIONED_CONFIG_KEYS]
    if non_versioned:
        raise HTTPException(status_code=400, detail=f"non-versioned keys are not allowed in config center: {', '.join(sorted(non_versioned))}")
    note = str(body.note or "").strip()
    if len(note) < 8:
        raise HTTPException(status_code=422, detail={"message": "publish note too short", "min_length": 8})
    if body.expected_latest_release_id is not None:
        latest_id = kb_store.get_latest_config_release_id(scope_type=body.scope_type, scope_id=body.scope_id)
        expected = int(body.expected_latest_release_id or 0)
        if expected != latest_id:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "config release conflict, please refresh before publishing",
                    "expected_latest_release_id": expected,
                    "actual_latest_release_id": latest_id,
                },
            )
    req_hash = hashlib.sha256(
        json.dumps(
            {
                "scope_type": body.scope_type,
                "scope_id": body.scope_id,
                "changed_keys": changed_keys,
                "note": note,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    idem_key = str(body.idempotency_key or "").strip()
    if idem_key:
        cached = kb_store.get_config_publish_idempotency(
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            idempotency_key=idem_key,
            ttl_seconds=600,
        )
        if cached:
            cached_hash = str(cached.get("request_hash") or "")
            if cached_hash and cached_hash != req_hash:
                raise HTTPException(status_code=409, detail={"message": "idempotency key reused with different payload"})
            return {
                "status": "ok",
                "item": cached.get("response_json") or {},
                "idempotent_replay": True,
                "trace_id": request.state.trace_id,
            }

    lock = kb_store.acquire_config_publish_lock(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        owner_id=request.state.trace_id,
        ttl_seconds=30,
    )
    if not bool(lock.get("acquired")):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "publish lock is held by another request",
                "lock_owner": lock.get("owner_id") or "",
                "lock_until": lock.get("lock_until") or "",
            },
        )

    result = None
    sync_error = ""
    try:
        result = kb_store.publish_config(
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            changed_keys=changed_keys,
            note=note,
            actor_id=request.state.actor_id,
            trace_id=request.state.trace_id,
            status="PUBLISHED",
            audit_meta={
                "source_page": str(body.source_page or "").strip(),
                "client_version": str(body.client_version or "").strip(),
                "request_id": str(body.request_id or "").strip(),
                "idempotency_key": idem_key,
                "expected_latest_release_id": int(body.expected_latest_release_id or 0) if body.expected_latest_release_id is not None else 0,
            },
        )
        if (body.scope_type or "").upper() == "PLATFORM":
            try:
                _sync_runtime_config_user_from_config_center()
            except Exception as exc:
                sync_error = str(exc)
        if result and int(result.get("release_id") or 0) > 0:
            if sync_error:
                kb_store.update_config_release_apply_status(
                    release_id=int(result.get("release_id") or 0),
                    apply_status="FAILED",
                    apply_error=sync_error,
                )
            else:
                kb_store.update_config_release_apply_status(
                    release_id=int(result.get("release_id") or 0),
                    apply_status="APPLIED",
                    apply_error="",
                )
            if idem_key:
                kb_store.save_config_publish_idempotency(
                    scope_type=body.scope_type,
                    scope_id=body.scope_id,
                    idempotency_key=idem_key,
                    request_hash=req_hash,
                    response_json=result or {},
                )
        if sync_error:
            raise HTTPException(status_code=500, detail={"message": "publish succeeded but apply failed", "apply_error": sync_error, "item": result})
        return {"status": "ok", "item": result, "idempotent_replay": False, "trace_id": request.state.trace_id}
    finally:
        kb_store.release_config_publish_lock(
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            lock_token=str(lock.get("lock_token") or ""),
        )


@app.post("/api/v1/admin/config/rollback")
async def rollback_config(
    body: ApiConfigRollbackRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    result = kb_store.rollback_config_release(
        release_id=body.release_id,
        actor_id=request.state.actor_id,
        trace_id=request.state.trace_id,
    )
    try:
        _sync_runtime_config_user_from_config_center()
    except Exception:
        pass
    return {"status": "ok", "item": result, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/config/switch-default")
async def switch_config_default(
    body: ApiConfigSwitchDefaultRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    result = kb_store.switch_config_to_default(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        actor_id=request.state.actor_id,
        trace_id=request.state.trace_id,
    )
    if (body.scope_type or "").upper() == "PLATFORM":
        try:
            _sync_runtime_config_user_from_config_center()
        except Exception:
            pass
    return {"status": "ok", "item": result, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/config/history")
async def list_config_history(
    request: Request,
    scope_type: str = "PLATFORM",
    scope_id: str = "platform",
    limit: int = 50,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    rows = kb_store.list_config_releases(scope_type=scope_type, scope_id=scope_id, limit=limit)
    return {"status": "ok", "items": rows, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/config/audit")
async def list_config_audit(
    request: Request,
    scope_type: str = "PLATFORM",
    scope_id: str = "platform",
    limit: int = 200,
    config_key: str = "",
    actor_id: str = "",
    action: str = "",
    release_id: int = 0,
    trace_id: str = "",
    from_ts: str = "",
    to_ts: str = "",
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    rows = kb_store.list_config_audit_logs(
        scope_type=scope_type,
        scope_id=scope_id,
        limit=limit,
        config_key=config_key,
        actor_id=actor_id,
        action=action,
        release_id=release_id,
        trace_id=trace_id,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    return {"status": "ok", "items": rows, "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/config/history/{release_id}")
async def delete_config_history_release(
    release_id: int,
    request: Request,
    scope_type: str = "PLATFORM",
    scope_id: str = "platform",
    _: dict = Depends(require_platform_admin),
) -> Dict:
    if int(release_id) <= 0:
        raise HTTPException(status_code=400, detail="default release cannot be deleted")
    ok = kb_store.delete_config_release(scope_type=scope_type, scope_id=scope_id, release_id=release_id)
    if not ok:
        raise HTTPException(status_code=404, detail="release not found")
    return {"status": "ok", "deleted": int(release_id), "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/config/diff")
async def get_config_diff(
    request: Request,
    release_from: int,
    release_to: int,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    _ensure_config_schema_seeded()
    diff = kb_store.get_config_release_diff(release_from=release_from, release_to=release_to)
    return {"status": "ok", "item": diff, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/system/storage/usage")
async def get_system_storage_usage(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    conf = _load_runtime_config_json()
    log_root = _runtime_log_root(conf)
    used_bytes = _dir_size_bytes(log_root)
    used_mb = round(float(used_bytes) / (1024 * 1024), 3)

    threshold_mb = None
    try:
        raw_mb = conf.get("system_log_storage_threshold_mb")
        if raw_mb is not None:
            n = float(raw_mb)
            if n > 0:
                threshold_mb = n
    except Exception:
        threshold_mb = None
    if threshold_mb is None:
        try:
            raw_gb = conf.get("system_log_storage_threshold_gb")
            if raw_gb is not None:
                n = float(raw_gb)
                if n > 0:
                    threshold_mb = n * 1024.0
        except Exception:
            threshold_mb = None
    if threshold_mb is None:
        try:
            raw_legacy = conf.get("system_log_storage_threshold")
            if raw_legacy is not None:
                n = float(raw_legacy)
                if n > 0:
                    threshold_mb = n
        except Exception:
            threshold_mb = None

    usage_ratio = None
    if threshold_mb and threshold_mb > 0:
        usage_ratio = round(min(1.0, max(0.0, used_mb / float(threshold_mb))), 6)

    return {
        "status": "ok",
        "item": {
            "path": log_root,
            "used_bytes": int(used_bytes),
            "used_mb": used_mb,
            "threshold_mb": round(float(threshold_mb), 3) if threshold_mb else None,
            "usage_ratio": usage_ratio,
        },
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/system/cross-store-repairs")
async def list_cross_store_repairs(
    request: Request,
    status: str = "",
    limit: int = 100,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    normalized_status = str(status or "").strip().upper()
    if normalized_status and normalized_status not in {"PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED"}:
        raise HTTPException(status_code=400, detail="invalid repair status")
    items = kb_store.list_cross_store_repairs(limit=limit, status=normalized_status or None)
    return {"status": "ok", "items": items, "trace_id": request.state.trace_id}


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(os.getenv(name, str(default))), maximum))
    except (TypeError, ValueError):
        return default


def _bounded_env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        return max(minimum, min(float(os.getenv(name, str(default))), maximum))
    except (TypeError, ValueError):
        return default


def _object_storage_capacity_snapshot() -> dict:
    stats = kb_store.storage_capacity_stats(
        tenant_limit=_bounded_env_int("OBJECT_STORAGE_CAPACITY_TENANT_LIMIT", 100, 1, 1000)
    )
    quota = _bounded_env_int(
        "OBJECT_STORAGE_TENANT_QUOTA_BYTES",
        0,
        0,
        10 * 1024 * 1024 * 1024 * 1024,
    )
    warning_ratio = _bounded_env_float("OBJECT_STORAGE_QUOTA_WARN_RATIO", 0.8, 0.1, 1.0)
    max_ratio = 0.0
    over_quota_count = 0
    at_quota_count = 0
    items = []
    for row in stats.get("items") or []:
        item = dict(row)
        projected = int(item.get("projected_bytes") or 0)
        ratio = (projected / quota) if quota > 0 else None
        item["quota_bytes"] = quota
        item["usage_ratio"] = ratio
        if ratio is not None:
            max_ratio = max(max_ratio, ratio)
            over_quota_count += int(projected > quota)
            at_quota_count += int(projected >= quota)
        items.append(item)

    reasons: list[str] = []
    app_env = str(os.getenv("APP_ENV", "development") or "development").strip().lower()
    if quota <= 0:
        health = "WARN" if app_env in {"production", "prod"} else "UNCONFIGURED"
        reasons.append("tenant object storage quota is not configured")
    elif over_quota_count:
        health = "CRITICAL"
        reasons.append(f"{over_quota_count} tenant(s) exceed the configured object storage quota")
    elif at_quota_count:
        health = "CRITICAL"
        reasons.append(f"{at_quota_count} tenant(s) reached the configured object storage quota")
    elif max_ratio >= warning_ratio:
        health = "WARN"
        reasons.append("tenant object storage usage reached the warning threshold")
    else:
        health = "OK"
    return {
        **stats,
        "items": items,
        "health": health,
        "health_reasons": reasons,
        "quota_bytes": quota,
        "warning_ratio": warning_ratio,
        "max_usage_ratio": max_ratio if quota > 0 else None,
        "over_quota_tenant_count": over_quota_count,
        "at_quota_tenant_count": at_quota_count,
        "quota_enabled": quota > 0,
    }


def _minio_physical_capacity_snapshot() -> dict:
    prometheus_url = str(os.getenv("PROMETHEUS_URL", "") or "").strip().rstrip("/")
    if not prometheus_url:
        return {
            "health": "UNCONFIGURED",
            "health_reasons": ["Prometheus physical capacity source is not configured"],
            "reason_codes": ["PROMETHEUS_UNCONFIGURED"],
            "configured": False,
            "source": "prometheus/minio-cluster",
        }
    parsed = urlparse(prometheus_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return {
            "health": "CRITICAL",
            "health_reasons": ["Prometheus URL is invalid"],
            "reason_codes": ["PROMETHEUS_URL_INVALID"],
            "configured": True,
            "source": "prometheus/minio-cluster",
        }

    warn_free_ratio = _bounded_env_float("MINIO_PHYSICAL_CAPACITY_WARN_FREE_RATIO", 0.20, 0.01, 0.99)
    critical_free_ratio = _bounded_env_float("MINIO_PHYSICAL_CAPACITY_CRITICAL_FREE_RATIO", 0.10, 0.001, 0.99)
    critical_free_ratio = min(critical_free_ratio, warn_free_ratio)
    selector = (
        '{job="minio-cluster",__name__=~"'
        'minio_cluster_capacity_(raw|usable)_(free|total)_bytes|'
        'minio_cluster_drive_(offline|online)_total|'
        'minio_cluster_nodes_(offline|online)_total|up"}'
    )
    try:
        response = httpx.get(
            f"{prometheus_url}/api/v1/query",
            params={"query": selector},
            timeout=_bounded_env_float("PROMETHEUS_QUERY_TIMEOUT_SECONDS", 3.0, 0.5, 15.0),
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") != "success":
            raise RuntimeError("Prometheus query did not succeed")
        rows = ((payload.get("data") or {}).get("result") or [])
        totals: dict[str, float] = {}
        latest_sample_epoch = 0.0
        for row in rows:
            metric_name = str((row.get("metric") or {}).get("__name__") or "")
            value = row.get("value") or []
            if not metric_name or len(value) < 2:
                continue
            latest_sample_epoch = max(latest_sample_epoch, float(value[0] or 0.0))
            totals[metric_name] = totals.get(metric_name, 0.0) + float(value[1] or 0.0)
    except Exception:
        return {
            "health": "CRITICAL",
            "health_reasons": ["Prometheus MinIO capacity query failed"],
            "reason_codes": ["PROMETHEUS_QUERY_FAILED"],
            "configured": True,
            "source": "prometheus/minio-cluster",
            "warn_free_ratio": warn_free_ratio,
            "critical_free_ratio": critical_free_ratio,
        }

    required = {
        "minio_cluster_capacity_usable_free_bytes",
        "minio_cluster_capacity_usable_total_bytes",
        "minio_cluster_drive_offline_total",
        "minio_cluster_nodes_offline_total",
        "up",
    }
    missing = sorted(required - set(totals))
    usable_free = int(totals.get("minio_cluster_capacity_usable_free_bytes") or 0)
    usable_total = int(totals.get("minio_cluster_capacity_usable_total_bytes") or 0)
    raw_free = int(totals.get("minio_cluster_capacity_raw_free_bytes") or 0)
    raw_total = int(totals.get("minio_cluster_capacity_raw_total_bytes") or 0)
    drive_offline = int(totals.get("minio_cluster_drive_offline_total") or 0)
    drive_online = int(totals.get("minio_cluster_drive_online_total") or 0)
    node_offline = int(totals.get("minio_cluster_nodes_offline_total") or 0)
    node_online = int(totals.get("minio_cluster_nodes_online_total") or 0)
    scrape_up = float(totals.get("up") or 0.0) > 0.0
    free_ratio = (usable_free / usable_total) if usable_total > 0 else None

    reasons: list[str] = []
    reason_codes: list[str] = []
    if missing:
        reasons.append(f"missing required MinIO metrics: {', '.join(missing)}")
        reason_codes.append("MINIO_METRICS_MISSING")
    if not scrape_up:
        reasons.append("Prometheus reports the MinIO scrape target as down")
        reason_codes.append("MINIO_SCRAPE_DOWN")
    if drive_offline:
        reasons.append(f"{drive_offline} MinIO drive(s) are offline")
        reason_codes.append("MINIO_DRIVE_OFFLINE")
    if node_offline:
        reasons.append(f"{node_offline} MinIO node(s) are offline")
        reason_codes.append("MINIO_NODE_OFFLINE")
    if free_ratio is None:
        reasons.append("MinIO usable capacity total is unavailable or zero")
        reason_codes.append("MINIO_CAPACITY_TOTAL_INVALID")

    if reasons:
        health = "CRITICAL"
    elif free_ratio <= critical_free_ratio:
        health = "CRITICAL"
        reasons.append("MinIO usable free capacity reached the critical threshold")
        reason_codes.append("MINIO_CAPACITY_CRITICAL")
    elif free_ratio <= warn_free_ratio:
        health = "WARN"
        reasons.append("MinIO usable free capacity reached the warning threshold")
        reason_codes.append("MINIO_CAPACITY_WARN")
    else:
        health = "OK"
    return {
        "health": health,
        "health_reasons": reasons,
        "reason_codes": reason_codes,
        "configured": True,
        "source": "prometheus/minio-cluster",
        "scrape_up": scrape_up,
        "usable_free_bytes": usable_free,
        "usable_total_bytes": usable_total,
        "usable_free_ratio": free_ratio,
        "raw_free_bytes": raw_free,
        "raw_total_bytes": raw_total,
        "drive_online": drive_online,
        "drive_offline": drive_offline,
        "node_online": node_online,
        "node_offline": node_offline,
        "sample_epoch": latest_sample_epoch,
        "warn_free_ratio": warn_free_ratio,
        "critical_free_ratio": critical_free_ratio,
    }


async def _enforce_minio_physical_capacity_for_write() -> None:
    if not _minio_physical_write_admission_enabled():
        return
    snapshot = await asyncio.to_thread(_minio_physical_capacity_snapshot)
    health = str(snapshot.get("health") or "UNCONFIGURED").upper()
    if health in {"OK", "WARN"}:
        return
    reason_codes = {str(item) for item in (snapshot.get("reason_codes") or [])}
    physical_failure_codes = {
        "MINIO_CAPACITY_CRITICAL",
        "MINIO_DRIVE_OFFLINE",
        "MINIO_NODE_OFFLINE",
    }
    physical_failure = bool(reason_codes & physical_failure_codes)
    raise HTTPException(
        status_code=507 if physical_failure else 503,
        detail={
            "code": (
                "OBJECT_STORAGE_PHYSICAL_CAPACITY_CRITICAL"
                if physical_failure
                else "OBJECT_STORAGE_PHYSICAL_CAPACITY_UNAVAILABLE"
            ),
            "message": (
                "MinIO physical capacity is not safe for writes"
                if physical_failure
                else "MinIO physical capacity admission is unavailable"
            ),
            "health": health,
            "reason_codes": sorted(reason_codes),
            "usable_free_bytes": snapshot.get("usable_free_bytes"),
            "usable_total_bytes": snapshot.get("usable_total_bytes"),
            "usable_free_ratio": snapshot.get("usable_free_ratio"),
        },
    )


def _minio_physical_write_admission_enabled() -> bool:
    value = str(os.getenv("MINIO_PHYSICAL_CAPACITY_ENFORCE_WRITES", "false") or "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _cross_store_repair_health_snapshot() -> dict:
    stats = kb_store.get_cross_store_repair_stats()
    thresholds = {
        "warn_open_count": _bounded_env_int("CROSS_STORE_REPAIR_WARN_OPEN_COUNT", 10, 1, 1000000),
        "critical_open_count": _bounded_env_int("CROSS_STORE_REPAIR_CRITICAL_OPEN_COUNT", 100, 1, 1000000),
        "warn_age_seconds": _bounded_env_int("CROSS_STORE_REPAIR_WARN_AGE_SECONDS", 300, 30, 604800),
        "critical_age_seconds": _bounded_env_int("CROSS_STORE_REPAIR_CRITICAL_AGE_SECONDS", 1800, 30, 2592000),
    }
    thresholds["critical_open_count"] = max(thresholds["warn_open_count"], thresholds["critical_open_count"])
    thresholds["critical_age_seconds"] = max(thresholds["warn_age_seconds"], thresholds["critical_age_seconds"])
    open_count = int(stats.get("open_count") or 0)
    failed = int((stats.get("status_counts") or {}).get("FAILED") or 0)
    age = float(stats.get("oldest_open_age_seconds") or 0.0)
    reasons: list[str] = []
    if failed:
        reasons.append(f"{failed} failed repair obligations")
    if open_count >= thresholds["critical_open_count"]:
        reasons.append("open repair count reached critical threshold")
    if age >= thresholds["critical_age_seconds"]:
        reasons.append("oldest open repair reached critical age")
    if reasons:
        health = "CRITICAL"
    elif open_count >= thresholds["warn_open_count"] or age >= thresholds["warn_age_seconds"]:
        health = "WARN"
        reasons.append("repair backlog reached warning threshold")
    elif open_count:
        health = "DEGRADED"
        reasons.append("repair obligations are in progress or awaiting execution")
    else:
        health = "OK"
    return {**stats, "health": health, "health_reasons": reasons, "thresholds": thresholds}


@app.get("/api/v1/admin/system/cross-store-repairs/stats")
async def get_cross_store_repair_stats(
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    return {"status": "ok", "item": _cross_store_repair_health_snapshot(), "trace_id": request.state.trace_id}


def _prometheus_label(value: object) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@app.get("/api/v1/admin/system/cross-store-repairs/metrics", response_class=PlainTextResponse)
async def get_cross_store_repair_metrics(
    _: dict = Depends(require_platform_admin),
) -> PlainTextResponse:
    snapshot = _cross_store_repair_health_snapshot()
    counts = snapshot.get("status_counts") or {}
    lines = [
        "# HELP agent_powflow_cross_store_repairs Repair ledger rows by status.",
        "# TYPE agent_powflow_cross_store_repairs gauge",
    ]
    for status in ("PENDING", "IN_PROGRESS", "SUCCEEDED", "FAILED"):
        lines.append(f'agent_powflow_cross_store_repairs{{status="{status.lower()}"}} {int(counts.get(status) or 0)}')
    lines.extend(
        [
            "# HELP agent_powflow_cross_store_repair_open Open repair obligations.",
            "# TYPE agent_powflow_cross_store_repair_open gauge",
            f"agent_powflow_cross_store_repair_open {int(snapshot.get('open_count') or 0)}",
            "# HELP agent_powflow_cross_store_repair_oldest_open_age_seconds Age of the oldest open repair obligation.",
            "# TYPE agent_powflow_cross_store_repair_oldest_open_age_seconds gauge",
            f"agent_powflow_cross_store_repair_oldest_open_age_seconds {float(snapshot.get('oldest_open_age_seconds') or 0.0):.3f}",
            "# HELP agent_powflow_cross_store_repair_failure_ratio Failed share of terminal repair obligations.",
            "# TYPE agent_powflow_cross_store_repair_failure_ratio gauge",
            f"agent_powflow_cross_store_repair_failure_ratio {float(snapshot.get('failure_ratio') or 0.0):.6f}",
            "# HELP agent_powflow_cross_store_repair_health Current repair health state (exactly one state is 1).",
            "# TYPE agent_powflow_cross_store_repair_health gauge",
        ]
    )
    current_health = str(snapshot.get("health") or "OK").upper()
    for state in ("OK", "DEGRADED", "WARN", "CRITICAL"):
        lines.append(f'agent_powflow_cross_store_repair_health{{state="{state.lower()}"}} {1 if state == current_health else 0}')
    lines.extend(
        [
            "# HELP agent_powflow_cross_store_repairs_by_store Repair ledger rows by store and status.",
            "# TYPE agent_powflow_cross_store_repairs_by_store gauge",
        ]
    )
    for row in snapshot.get("by_store_status") or []:
        store = _prometheus_label(str(row.get("store_name") or "unknown").lower())
        status = _prometheus_label(str(row.get("status") or "unknown").lower())
        lines.append(f'agent_powflow_cross_store_repairs_by_store{{store="{store}",status="{status}"}} {int(row.get("count") or 0)}')
    capacity = _object_storage_capacity_snapshot()
    lines.extend(
        [
            "# HELP agent_powflow_object_storage_bytes Logical document object bytes by kind.",
            "# TYPE agent_powflow_object_storage_bytes gauge",
            f'agent_powflow_object_storage_bytes{{kind="source"}} {int(capacity.get("source_bytes") or 0)}',
            f'agent_powflow_object_storage_bytes{{kind="extracted"}} {int(capacity.get("extracted_bytes") or 0)}',
            f'agent_powflow_object_storage_bytes{{kind="used"}} {int(capacity.get("used_bytes") or 0)}',
            f'agent_powflow_object_storage_bytes{{kind="reserved"}} {int(capacity.get("reserved_bytes") or 0)}',
            f'agent_powflow_object_storage_bytes{{kind="projected"}} {int(capacity.get("projected_bytes") or 0)}',
            "# HELP agent_powflow_object_storage_tenant_quota_bytes Configured logical quota per tenant; zero means disabled.",
            "# TYPE agent_powflow_object_storage_tenant_quota_bytes gauge",
            f'agent_powflow_object_storage_tenant_quota_bytes {int(capacity.get("quota_bytes") or 0)}',
            "# HELP agent_powflow_object_storage_max_tenant_usage_ratio Highest projected tenant usage ratio.",
            "# TYPE agent_powflow_object_storage_max_tenant_usage_ratio gauge",
            f'agent_powflow_object_storage_max_tenant_usage_ratio {float(capacity.get("max_usage_ratio") or 0.0):.6f}',
            "# HELP agent_powflow_object_storage_over_quota_tenants Tenants above the configured logical quota.",
            "# TYPE agent_powflow_object_storage_over_quota_tenants gauge",
            f'agent_powflow_object_storage_over_quota_tenants {int(capacity.get("over_quota_tenant_count") or 0)}',
            "# HELP agent_powflow_object_storage_capacity_health Current logical capacity health state (exactly one state is 1).",
            "# TYPE agent_powflow_object_storage_capacity_health gauge",
        ]
    )
    capacity_health = str(capacity.get("health") or "UNCONFIGURED").upper()
    for state in ("OK", "UNCONFIGURED", "WARN", "CRITICAL"):
        lines.append(
            f'agent_powflow_object_storage_capacity_health{{state="{state.lower()}"}} '
            f'{1 if state == capacity_health else 0}'
        )
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/v1/admin/system/object-storage/health")
async def get_object_storage_health(
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    try:
        item = await asyncio.to_thread(object_storage.governance_status)
    except Exception as exc:
        item = {
            "health": "CRITICAL",
            "bucket": object_storage.bucket,
            "bucket_exists": False,
            "secure_transport": object_storage.secure,
            "versioning_status": "UNAVAILABLE",
            "lifecycle_configured": False,
            "lifecycle_rule_count": 0,
            "reasons": [str(exc)],
        }
    try:
        capacity = await asyncio.to_thread(_object_storage_capacity_snapshot)
    except Exception as exc:
        capacity = {
            "health": "CRITICAL",
            "health_reasons": [f"object storage capacity inspection failed: {exc}"],
            "quota_enabled": False,
            "quota_bytes": 0,
            "items": [],
        }
    item["capacity"] = capacity
    capacity_health = str(capacity.get("health") or "UNCONFIGURED").upper()
    if capacity_health == "CRITICAL":
        item["health"] = "CRITICAL"
    elif capacity_health == "WARN" and str(item.get("health") or "OK").upper() == "OK":
        item["health"] = "WARN"
    if capacity_health in {"WARN", "CRITICAL"}:
        item.setdefault("reasons", []).extend(capacity.get("health_reasons") or [])
    physical_capacity = await asyncio.to_thread(_minio_physical_capacity_snapshot)
    physical_capacity["write_admission_enforced"] = _minio_physical_write_admission_enabled()
    item["physical_capacity"] = physical_capacity
    physical_health = str(physical_capacity.get("health") or "UNCONFIGURED").upper()
    if physical_health == "CRITICAL":
        item["health"] = "CRITICAL"
    elif physical_health == "WARN" and str(item.get("health") or "OK").upper() == "OK":
        item["health"] = "WARN"
    if physical_health in {"WARN", "CRITICAL"}:
        item.setdefault("reasons", []).extend(physical_capacity.get("health_reasons") or [])
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


def _parse_alertmanager_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.startswith("0001-01-01"):
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


@app.post("/api/v1/internal/alerts/alertmanager")
async def receive_alertmanager_webhook(
    body: dict,
    request: Request,
    _: None = Depends(require_alertmanager_webhook),
) -> Dict:
    alerts = body.get("alerts") if isinstance(body, dict) else None
    if not isinstance(alerts, list):
        raise HTTPException(status_code=400, detail="Alertmanager payload must contain an alerts array")
    max_alerts = _bounded_env_int("ALERTMANAGER_WEBHOOK_MAX_ALERTS", 100, 1, 1000)
    if len(alerts) > max_alerts:
        raise HTTPException(status_code=413, detail="Alertmanager payload contains too many alerts")
    accepted = 0
    for alert in alerts:
        if not isinstance(alert, dict):
            raise HTTPException(status_code=400, detail="Alertmanager alert item must be an object")
        labels = alert.get("labels") if isinstance(alert.get("labels"), dict) else {}
        annotations = alert.get("annotations") if isinstance(alert.get("annotations"), dict) else {}
        alert_name = str(labels.get("alertname") or "").strip()
        fingerprint = str(alert.get("fingerprint") or "").strip()
        if not fingerprint:
            canonical = json.dumps(
                {"labels": labels, "startsAt": alert.get("startsAt") or ""},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if not alert_name:
            raise HTTPException(status_code=400, detail="Alertmanager alertname label is required")
        raw_status = str(alert.get("status") or body.get("status") or "firing").strip().upper()
        status = "RESOLVED" if raw_status == "RESOLVED" else "FIRING"
        try:
            kb_store.upsert_alertmanager_alert(
                fingerprint=fingerprint,
                alert_name=alert_name,
                component=str(labels.get("component") or ""),
                severity=str(labels.get("severity") or "warning"),
                status=status,
                labels=labels,
                annotations=annotations,
                starts_at=_parse_alertmanager_datetime(alert.get("startsAt")),
                ends_at=_parse_alertmanager_datetime(alert.get("endsAt")),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        accepted += 1
    kb_store.cleanup_resolved_alertmanager_alerts(
        _bounded_env_int("ALERTMANAGER_ALERT_RETENTION_DAYS", 90, 1, 3650)
    )
    return {"status": "accepted", "accepted": accepted, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/system/cross-store-repairs/execute")
async def execute_cross_store_repairs(
    request: Request,
    limit: int = 50,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    stale_after = _bounded_env_int("CROSS_STORE_REPAIR_STALE_AFTER_SECONDS", 300, 30, 86400)
    result = kb_store.repair_cross_store_operations(limit=limit, stale_after_seconds=stale_after)
    return {"status": "ok", "item": result, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/system/cross-store-repairs/{repair_id}/execute")
async def execute_cross_store_repair(
    repair_id: int,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    if repair_id < 1:
        raise HTTPException(status_code=400, detail="invalid repair id")
    try:
        succeeded = kb_store.repair_cross_store_operation(repair_id)
    except RuntimeError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(status_code=404, detail="cross-store repair not found") from exc
        raise
    item = kb_store.get_cross_store_repair(repair_id)
    return {
        "status": "ok" if succeeded else "pending",
        "item": {"succeeded": succeeded, "record": item},
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/system/log-index/health")
async def get_system_log_index_health(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    settings = _log_index_sync_settings()
    item = dict(_SYSTEM_LOG_SYNC_HEALTH)
    item["last_sync_epoch"] = float(_SYSTEM_LOG_SYNC_LAST_TS or 0.0)
    item["staleness_seconds"] = (
        max(0.0, time.time() - float(_SYSTEM_LOG_SYNC_LAST_TS)) if float(_SYSTEM_LOG_SYNC_LAST_TS or 0.0) > 0 else None
    )
    item["settings"] = settings
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/system/logs")
async def list_system_logs(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    days: int = 30,
    level: str = "",
    category: str = "",
    field: str = "",
    q: str = "",
    from_ts: str = "",
    to_ts: str = "",
    include_noise: bool = False,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    current_page = max(1, int(page or 1))
    cap = max(1, min(int(page_size or 20), 200))
    offset = (current_page - 1) * cap
    selected_files = None
    field_key = str(field or "").strip().lower()
    q_key = str(q or "").strip()
    precise_lookup = bool(q_key) and field_key in {"id", "event_id", "trace_id", "request_id"}
    if not precise_lookup and not include_noise and not str(level or "").strip() and not str(category or "").strip():
        selected_files = ["security.log", "auth.log", "error.log", "system.log"]
    await _ensure_system_log_index_ready(
        days=max(7, min(int(days or 7), 30)),
        file_names=selected_files,
        max_files=0,
        max_lines_per_file=0,
        max_staleness_seconds=15.0,
    )
    where = ["ts >= (NOW() - (%s || ' days')::interval)"]
    params: list = [max(1, min(int(days or 30), 365))]
    if level:
        where.append("lower(level) = %s")
        params.append(str(level).strip().lower())
    if category:
        where.append("lower(category) LIKE %s")
        params.append(f"%{str(category).strip().lower()}%")
    if from_ts:
        where.append("ts >= %s::timestamptz")
        params.append(str(from_ts))
    if to_ts:
        where.append("ts <= %s::timestamptz")
        params.append(str(to_ts))
    if not include_noise:
        where.append(
            """split_part(path,'?',1) <> ALL(%s)"""
        )
        params.append([
            "/api/v1/system/health",
            "/api/v1/admin/system/logs",
            "/api/v1/admin/system/logs/stats",
            "/api/v1/admin/system/alerts/preview",
            "/api/v1/admin/system/permission-warnings",
            "/api/v1/admin/system/risk/overview",
            "/api/v1/admin/system/risk/events",
            "/api/v1/admin/system/risk/events/stats",
            "/api/v1/admin/system/runtime-stats/daily",
            "/api/v1/admin/system/runtime-config",
            "/api/v1/admin/system/storage/usage",
            "/api/v1/admin/system/log-index/health",
            "/api/v1/admin/usage/api-tokens/summary",
            "/api/v1/admin/usage/api-tokens/budgets",
        ])
    if q:
        q_raw = q_key
        q_lower = q_raw.lower()
        if field_key in {"id", "event_id"}:
            where.append("(event_id = %s OR lower(event_id) = %s)")
            params.extend([q_raw, q_lower])
        elif field_key == "trace_id":
            where.append(
                """(
                    trace_id = %s OR lower(trace_id) = %s OR
                    request_id = %s OR lower(request_id) = %s
                )"""
            )
            params.extend([q_raw, q_lower, q_raw, q_lower])
        elif field_key == "request_id":
            where.append(
                """(
                    request_id = %s OR lower(request_id) = %s OR
                    trace_id = %s OR lower(trace_id) = %s
                )"""
            )
            params.extend([q_raw, q_lower, q_raw, q_lower])
        elif field_key in {"message", "path", "source", "role", "status", "event", "category", "risk_level", "reason_code", "trigger_rule"}:
            where.append(f"lower({field_key}) LIKE %s")
            params.append(f"%{q_lower}%")
        else:
            where.append(
                """(
                    lower(message) LIKE %s OR lower(path) LIKE %s OR lower(source) LIKE %s OR
                    lower(role) LIKE %s OR lower(status) LIKE %s OR lower(event) LIKE %s OR
                    lower(event_id) LIKE %s OR lower(trace_id) LIKE %s OR lower(request_id) LIKE %s OR
                    lower(reason_code) LIKE %s OR lower(trigger_rule) LIKE %s
                )"""
            )
            kw = f"%{q_lower}%"
            params.extend([kw, kw, kw, kw, kw, kw, kw, kw, kw, kw, kw])
    sql_where = " AND ".join(where) if where else "1=1"
    with kb_store._conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*)::bigint AS c FROM system_log_entries WHERE {sql_where}", params)
        total = int((cur.fetchone() or {}).get("c", 0))
        cur.execute(
            f"""
            SELECT event_id AS id, event_id, ts, level, category, message, source, role, status, path,
                   event, resource_type, http_status, auth_result, trace_id, request_id,
                   risk_level, risk_tags, decision, reason_code, trigger_rule
            FROM system_log_entries
            WHERE {sql_where}
            ORDER BY ts DESC
            LIMIT %s OFFSET %s
            """,
            [*params, cap, offset],
        )
        paged = [dict(r) for r in cur.fetchall()]
    return {
        "status": "ok",
        "page": current_page,
        "page_size": cap,
        "total": total,
        "truncated": False,
        "items": paged,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/system/logs/stats")
async def system_logs_stats(
    request: Request,
    days: int = 3,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    await _ensure_system_log_index_ready(
        days=max(7, min(int(days or 3), 30)),
        file_names=["security.log", "auth.log", "error.log", "system.log"],
        max_files=0,
        max_lines_per_file=0,
        max_staleness_seconds=10.0,
    )
    out = {"warn": 0, "error": 0, "critical": 0}
    with kb_store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT lower(level) AS lv, COUNT(*)::bigint AS c
            FROM system_log_entries
            WHERE ts >= (NOW() - (%s || ' days')::interval)
            GROUP BY lower(level)
            """,
            (max(1, min(int(days or 3), 365)),),
        )
        for row in cur.fetchall():
            lv = str(row.get("lv", ""))
            c = int(row.get("c", 0) or 0)
            if lv in {"warn", "warning"}:
                out["warn"] += c
            elif lv == "error":
                out["error"] += c
            elif lv == "critical":
                out["critical"] += c
    return {"status": "ok", "days": days, **out, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/system/alerts/preview")
async def system_alerts_preview(
    request: Request,
    limit: int = 50,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    cap = max(1, min(int(limit or 50), 200))
    data = kb_store.list_api_usage_alerts(
        tenant_id=request.state.tenant_id,
        days=30,
        status="OPEN",
        limit=cap,
    )
    items = []
    repair_health = _cross_store_repair_health_snapshot()
    if repair_health.get("health") in {"WARN", "CRITICAL"}:
        items.append(
            {
                "alert_type": "cross_store_repair",
                "severity": "critical" if repair_health.get("health") == "CRITICAL" else "warning",
                "scope": "platform/cross-store",
                "status": "OPEN",
                "message": "；".join(repair_health.get("health_reasons") or []) or "跨存储修复积压超过阈值",
                "created_at": repair_health.get("oldest_open_at") or "",
                "raw": repair_health,
            }
        )
    capacity_health = _object_storage_capacity_snapshot()
    if capacity_health.get("health") in {"WARN", "CRITICAL"}:
        items.append(
            {
                "alert_type": "object_storage_capacity",
                "severity": "critical" if capacity_health.get("health") == "CRITICAL" else "warning",
                "scope": "platform/object-storage",
                "status": "OPEN",
                "message": "；".join(capacity_health.get("health_reasons") or []) or "对象存储容量达到阈值",
                "created_at": "",
                "raw": capacity_health,
            }
        )
    physical_capacity = _minio_physical_capacity_snapshot()
    if physical_capacity.get("health") in {"WARN", "CRITICAL"}:
        items.append(
            {
                "alert_type": "minio_physical_capacity",
                "severity": "critical" if physical_capacity.get("health") == "CRITICAL" else "warning",
                "scope": "platform/minio",
                "status": "OPEN",
                "message": "；".join(physical_capacity.get("health_reasons") or []) or "MinIO 物理容量达到阈值",
                "created_at": "",
                "raw": physical_capacity,
            }
        )
    for alert in kb_store.list_alertmanager_alerts(status="FIRING", limit=cap):
        annotations = alert.get("annotations_json") or {}
        labels = alert.get("labels_json") or {}
        items.append(
            {
                "alert_type": "alertmanager",
                "severity": str(alert.get("severity") or "warning"),
                "scope": f"{alert.get('component') or 'platform'}/{labels.get('instance') or '-'}",
                "status": "OPEN",
                "message": str(annotations.get("summary") or annotations.get("description") or alert.get("alert_name") or "Alertmanager alert"),
                "created_at": alert.get("starts_at") or alert.get("first_received_at") or "",
                "raw": alert,
            }
        )
    for row in data.get("items", []):
        items.append(
            {
                "alert_type": "usage_budget",
                "severity": row.get("severity", "warning"),
                "scope": f"{row.get('scope_type', '-')}/{row.get('scope_value', '-')}",
                "status": row.get("status", "OPEN"),
                "message": row.get("message", "") or row.get("reason", "") or "预算阈值触发",
                "created_at": row.get("created_at", ""),
                "raw": row,
            }
        )
    return {"status": "ok", "items": items[:cap], "total": len(items), "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/system/permission-warnings")
async def system_permission_warnings(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    days: int = 30,
    risk_level: str = "",
    source: str = "",
    role: str = "",
    q: str = "",
    limit: int = 50,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    cap = max(1, min(int(page_size or limit or 50), 500))
    current_page = max(1, int(page or 1))
    offset = (current_page - 1) * cap
    await _ensure_system_log_index_ready(
        days=max(7, min(int(days or 30), 30)),
        file_names=["security.log", "auth.log", "error.log"],
        max_files=0,
        max_lines_per_file=0,
        max_staleness_seconds=20.0,
    )
    level_filter = str(risk_level or "").strip().lower()
    source_filter = str(source or "").strip().lower()
    role_filter = str(role or "").strip().lower()
    q_filter = str(q or "").strip().lower()
    where = [
        "ts >= (NOW() - (%s || ' days')::interval)",
        "(auth_result='DENY' OR http_status IN ('401','403') OR risk_level IN ('HIGH','CRITICAL'))",
    ]
    params: list = [max(1, min(int(days or 30), 365))]
    if level_filter:
        where.append("lower(risk_level) = %s")
        params.append(level_filter)
    if source_filter:
        where.append("lower(source) LIKE %s")
        params.append(f"%{source_filter}%")
    if role_filter:
        where.append("lower(role) LIKE %s")
        params.append(f"%{role_filter}%")
    if q_filter:
        where.append(
            "(lower(message) LIKE %s OR lower(path) LIKE %s OR lower(reason_code) LIKE %s OR lower(trace_id) LIKE %s)"
        )
        kw = f"%{q_filter}%"
        params.extend([kw, kw, kw, kw])
    sql_where = " AND ".join(where)
    with kb_store._conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*)::bigint AS c FROM system_log_entries WHERE {sql_where}", params)
        total = int((cur.fetchone() or {}).get("c", 0))
        cur.execute(
            f"""
            SELECT ts,source,role,path,status,message,risk_level,risk_tags,decision,reason_code,trigger_rule,trace_id
            FROM system_log_entries
            WHERE {sql_where}
            ORDER BY
              CASE upper(risk_level) WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 ELSE 1 END DESC,
              ts DESC
            LIMIT %s OFFSET %s
            """,
            [*params, cap, offset],
        )
        paged = [dict(r) for r in cur.fetchall()]
    return {
        "status": "ok",
        "items": paged,
        "total": total,
        "page": current_page,
        "page_size": cap,
        "truncated": False,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/system/risk/overview")
async def system_risk_overview(
    request: Request,
    days: int = 30,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    await _ensure_system_log_index_ready(
        days=max(7, min(int(days or 30), 30)),
        file_names=["security.log", "auth.log", "error.log"],
        max_files=0,
        max_lines_per_file=0,
        max_staleness_seconds=20.0,
    )
    out = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    reason_items = []
    path_items = []
    with kb_store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT lower(risk_level) AS lv, COUNT(*)::bigint AS c
            FROM system_log_entries
            WHERE ts >= (NOW() - (%s || ' days')::interval)
            GROUP BY lower(risk_level)
            """,
            (max(1, min(int(days or 30), 365)),),
        )
        for row in cur.fetchall():
            lv = str(row.get("lv", "low"))
            if lv in out:
                out[lv] = int(row.get("c", 0) or 0)
        cur.execute(
            """
            SELECT reason_code, COUNT(*)::bigint AS c
            FROM system_log_entries
            WHERE ts >= (NOW() - (%s || ' days')::interval)
            GROUP BY reason_code
            ORDER BY c DESC
            LIMIT 10
            """,
            (max(1, min(int(days or 30), 365)),),
        )
        reason_items = [(str(r.get("reason_code", "GENERIC_EVENT")), int(r.get("c", 0) or 0)) for r in cur.fetchall()]
        cur.execute(
            """
            SELECT path, COUNT(*)::bigint AS c
            FROM system_log_entries
            WHERE ts >= (NOW() - (%s || ' days')::interval)
            GROUP BY path
            ORDER BY c DESC
            LIMIT 10
            """,
            (max(1, min(int(days or 30), 365)),),
        )
        path_items = [(str(r.get("path", "-")), int(r.get("c", 0) or 0)) for r in cur.fetchall()]
    return {
        "status": "ok",
        "days": days,
        "counts": out,
        "top_reasons": [{"reason_code": k, "count": v} for k, v in reason_items],
        "top_paths": [{"path": k, "count": v} for k, v in path_items],
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/system/risk/rules")
async def system_risk_rules(
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    conf = _load_runtime_config_json()
    rules = _load_security_risk_rules(conf)
    thresholds = conf.get("security_alert_thresholds")
    if not isinstance(thresholds, dict):
        thresholds = {}
    return {
        "status": "ok",
        "rules": rules,
        "thresholds": thresholds,
        "trace_id": request.state.trace_id,
    }


class ApiRiskRulesPatchRequest(BaseModel):
    security_risk_rules: list[dict] = Field(default_factory=list)
    security_alert_thresholds: dict[str, object] = Field(default_factory=dict)
    note: str = ""


@app.patch("/api/v1/admin/system/risk/rules")
async def patch_system_risk_rules(
    body: ApiRiskRulesPatchRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    updates = {
        "security_risk_rules": body.security_risk_rules or [],
        "security_alert_thresholds": body.security_alert_thresholds or {},
    }
    validation = _validate_config_updates(updates=updates, base_item=_load_runtime_config_json())
    if not validation.get("valid", False):
        raise HTTPException(
            status_code=422,
            detail={"message": "risk rules validation failed", "errors": validation.get("errors") or [], "warnings": validation.get("warnings") or []},
        )
    kb_store.patch_config_draft(
        scope_type="PLATFORM",
        scope_id="platform",
        updates=updates,
        actor_id=request.state.actor_id,
        trace_id=request.state.trace_id,
    )
    rel = kb_store.publish_config(
        scope_type="PLATFORM",
        scope_id="platform",
        changed_keys=sorted(list(updates.keys())),
        note=str(body.note or "update security risk rules"),
        actor_id=request.state.actor_id,
        trace_id=request.state.trace_id,
        status="PUBLISHED",
        audit_meta={"source_page": "risk-rules", "client_version": "execution-api"},
    )
    try:
        _sync_runtime_config_user_from_config_center()
    except Exception:
        pass
    kb_store.update_config_release_apply_status(release_id=int(rel.get("release_id") or 0), apply_status="APPLIED", apply_error="")
    return {"status": "ok", "item": rel, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/system/risk/events")
async def system_risk_events(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    days: int = 30,
    risk_level: str = "",
    risk_tag: str = "",
    decision: str = "",
    reason_code: str = "",
    event_status: str = "",
    q: str = "",
    _: dict = Depends(require_platform_admin),
) -> Dict:
    current_page = max(1, int(page or 1))
    cap = max(1, min(int(page_size or 50), 500))
    offset = (current_page - 1) * cap
    level_filter = str(risk_level or "").strip().lower()
    tag_filter = str(risk_tag or "").strip().lower()
    decision_filter = str(decision or "").strip().upper()
    reason_filter = str(reason_code or "").strip().upper()
    status_filter = str(event_status or "").strip().upper()
    q_filter = str(q or "").strip().lower()

    await _ensure_system_log_index_ready(
        days=max(7, min(int(days or 30), 30)),
        file_names=["security.log", "auth.log", "error.log"],
        max_files=0,
        max_lines_per_file=0,
        max_staleness_seconds=20.0,
    )
    where = ["ts >= (NOW() - (%s || ' days')::interval)"]
    params: list = [max(1, min(int(days or 30), 365))]
    if level_filter:
        where.append("lower(risk_level)=%s")
        params.append(level_filter)
    if tag_filter:
        where.append("lower(risk_tags::text) LIKE %s")
        params.append(f"%{tag_filter}%")
    if decision_filter:
        where.append("upper(decision)=%s")
        params.append(decision_filter)
    if reason_filter:
        where.append("upper(reason_code)=%s")
        params.append(reason_filter)
    if q_filter:
        where.append("(lower(message) LIKE %s OR lower(path) LIKE %s OR lower(source) LIKE %s OR lower(trace_id) LIKE %s OR lower(reason_code) LIKE %s)")
        kw = f"%{q_filter}%"
        params.extend([kw, kw, kw, kw, kw])
    sql_where = " AND ".join(where)
    fetch_cap = 5000
    with kb_store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT event_id, ts, level, category, message, source, role, status, path, event, resource_type,
                   http_status, auth_result, trace_id, request_id, risk_level, risk_tags, decision, reason_code, trigger_rule
            FROM system_log_entries
            WHERE {sql_where}
            ORDER BY
              CASE upper(risk_level) WHEN 'CRITICAL' THEN 4 WHEN 'HIGH' THEN 3 WHEN 'MEDIUM' THEN 2 ELSE 1 END DESC,
              ts DESC
            LIMIT %s
            """,
            [*params, fetch_cap + 1],
        )
        raw_items = [dict(r) for r in cur.fetchall()]
    raw_truncated = len(raw_items) > fetch_cap
    if raw_truncated:
        raw_items = raw_items[:fetch_cap]
    state_map = kb_store.get_risk_event_states([str(x.get("event_id", "")) for x in raw_items])
    items = []
    for item in raw_items:
        event_id = str(item.get("event_id", "")).strip()
        state = state_map.get(event_id) or {}
        current_status = str(state.get("status", "NEW") or "NEW").upper()
        if status_filter and current_status != status_filter:
            continue
        items.append({**item, "event_status": current_status, "event_state": state})
    paged = items[offset : offset + cap]
    return {
        "status": "ok",
        "items": paged,
        "total": len(items),
        "page": current_page,
        "page_size": cap,
        "truncated": raw_truncated,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/system/risk/events/stats")
async def system_risk_event_stats(
    request: Request,
    days: int = 30,
    risk_level: str = "",
    risk_tag: str = "",
    decision: str = "",
    reason_code: str = "",
    event_status: str = "",
    q: str = "",
    _: dict = Depends(require_platform_admin),
) -> Dict:
    await _ensure_system_log_index_ready(
        days=max(7, min(int(days or 30), 30)),
        file_names=["security.log", "auth.log", "error.log"],
        max_files=0,
        max_lines_per_file=0,
        max_staleness_seconds=20.0,
    )
    with kb_store._conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_id, ts, message, path, source, trace_id, risk_level, risk_tags, decision, reason_code, trigger_rule
            FROM system_log_entries
            WHERE ts >= (NOW() - (%s || ' days')::interval)
            ORDER BY ts DESC
            LIMIT 5000
            """,
            (max(1, min(int(days or 30), 365)),),
        )
        rows = [dict(r) for r in cur.fetchall()]
    state_map = kb_store.get_risk_event_states([str(x.get("event_id", "")) for x in rows])
    level_filter = str(risk_level or "").strip().lower()
    tag_filter = str(risk_tag or "").strip().lower()
    decision_filter = str(decision or "").strip().upper()
    reason_filter = str(reason_code or "").strip().upper()
    status_filter = str(event_status or "").strip().upper()
    q_filter = str(q or "").strip().lower()
    by_level: dict[str, int] = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    by_status: dict[str, int] = {"NEW": 0, "ACKED": 0, "RESOLVED": 0}
    by_reason: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    total = 0
    for item in rows:
        level = str(item.get("risk_level", "LOW") or "LOW").upper()
        status = str((state_map.get(str(item.get("event_id", ""))) or {}).get("status", "NEW") or "NEW").upper()
        tags = [str(x).lower() for x in (item.get("risk_tags", []) or [])]
        if level_filter and level.lower() != level_filter:
            continue
        if tag_filter and tag_filter not in tags:
            continue
        if decision_filter and str(item.get("decision", "")).upper() != decision_filter:
            continue
        reason = str(item.get("reason_code", "GENERIC_EVENT") or "GENERIC_EVENT").upper()
        if reason_filter and reason != reason_filter:
            continue
        if status_filter and status != status_filter:
            continue
        if q_filter:
            bag = " ".join(
                [
                    str(item.get("message", "")),
                    str(item.get("path", "")),
                    str(item.get("source", "")),
                    str(item.get("trace_id", "")),
                    ",".join(tags),
                    reason,
                    str(item.get("trigger_rule", "default") or "default"),
                ]
            ).lower()
            if q_filter not in bag:
                continue
        rule = str(item.get("trigger_rule", "default") or "default")
        if level not in by_level:
            level = "LOW"
        if status not in by_status:
            status = "NEW"
        total += 1
        by_level[level] = by_level.get(level, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
        by_rule[rule] = by_rule.get(rule, 0) + 1
    top_reasons = [{"reason_code": k, "count": v} for k, v in sorted(by_reason.items(), key=lambda x: x[1], reverse=True)[:20]]
    top_rules = [{"trigger_rule": k, "count": v} for k, v in sorted(by_rule.items(), key=lambda x: x[1], reverse=True)[:20]]
    return {
        "status": "ok",
        "days": days,
        "totals": {"events": total},
        "by_level": by_level,
        "by_status": by_status,
        "top_reasons": top_reasons,
        "top_trigger_rules": top_rules,
        "trace_id": request.state.trace_id,
    }


class ApiRiskEventStateRequest(BaseModel):
    note: str = ""


@app.post("/api/v1/admin/system/risk/events/{event_id}/ack")
async def ack_system_risk_event(
    event_id: str,
    body: ApiRiskEventStateRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    current = kb_store.get_risk_event_states([str(event_id or "")]).get(str(event_id or ""), {})
    current_status = str(current.get("status", "NEW") or "NEW").upper()
    if current_status == "ACKED":
        return {
            "status": "ok",
            "item": {"event_id": event_id, **current},
            "no_change": True,
            "trace_id": request.state.trace_id,
        }
    item = kb_store.set_risk_event_state(
        event_id=event_id,
        status="ACKED",
        actor_id=request.state.actor_id,
        note=body.note or "",
    )
    _emit_risk_event_audit(
        request=request,
        event_id=event_id,
        action="ack",
        status="ACKED",
        note=body.note or "",
    )
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/system/risk/events/{event_id}/resolve")
async def resolve_system_risk_event(
    event_id: str,
    body: ApiRiskEventStateRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    current = kb_store.get_risk_event_states([str(event_id or "")]).get(str(event_id or ""), {})
    current_status = str(current.get("status", "NEW") or "NEW").upper()
    if current_status == "RESOLVED":
        return {
            "status": "ok",
            "item": {"event_id": event_id, **current},
            "no_change": True,
            "trace_id": request.state.trace_id,
        }
    item = kb_store.set_risk_event_state(
        event_id=event_id,
        status="RESOLVED",
        actor_id=request.state.actor_id,
        note=body.note or "",
    )
    _emit_risk_event_audit(
        request=request,
        event_id=event_id,
        action="resolve",
        status="RESOLVED",
        note=body.note or "",
    )
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/system/runtime-stats/daily")
async def system_runtime_stats_daily(
    request: Request,
    days: int = 30,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    window_days = max(1, min(int(days or 30), 365))
    usage = kb_store.get_api_token_usage_stats(
        tenant_id=request.state.tenant_id,
        days=window_days,
        include_all_tenants=False,
        limit=500,
    )
    by_day_usage = usage.get("by_day", []) or []
    usage_map: dict[str, dict] = {}
    for row in by_day_usage:
        day = str(row.get("day", ""))
        usage_map[day] = {
            "day": day,
            "total_tokens": int(row.get("total_tokens", 0) or 0),
            "api_cost_total": float(row.get("estimated_cost_usd", 0.0) or 0.0),
            "new_chats": 0,
            "active_users": 0,
        }

    try:
        with kb_store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_char(created_at::date, 'YYYY-MM-DD') AS day, COUNT(*)::bigint AS c
                FROM chat_sessions
                WHERE created_at >= (now() - (%s || ' days')::interval)
                GROUP BY 1
                ORDER BY 1
                """,
                (window_days,),
            )
            for row in cur.fetchall():
                day = str(row.get("day", ""))
                usage_map.setdefault(day, {"day": day, "total_tokens": 0, "api_cost_total": 0.0, "new_chats": 0, "active_users": 0})
                usage_map[day]["new_chats"] = int(row.get("c", 0) or 0)

            cur.execute(
                """
                SELECT day, COUNT(DISTINCT actor_id)::bigint AS c FROM (
                  SELECT to_char(created_at::date, 'YYYY-MM-DD') AS day, actor_id
                  FROM chat_runs
                  WHERE created_at >= (now() - (%s || ' days')::interval)
                    AND coalesce(actor_id, '') <> ''
                ) t
                GROUP BY day
                ORDER BY day
                """,
                (window_days,),
            )
            for row in cur.fetchall():
                day = str(row.get("day", ""))
                usage_map.setdefault(day, {"day": day, "total_tokens": 0, "api_cost_total": 0.0, "new_chats": 0, "active_users": 0})
                usage_map[day]["active_users"] = int(row.get("c", 0) or 0)
    except Exception:
        pass

    items = [usage_map[k] for k in sorted(usage_map.keys())]
    return {"status": "ok", "days": window_days, "items": items, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/system/logs/retention/execute")
async def execute_log_retention_policy(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    data = _run_log_retention_policy()
    return {"status": "ok", "item": data, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/system/logs/archives")
async def list_system_log_archives(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    log_root = _runtime_log_root()
    items = _list_log_archives(log_root)
    return {"status": "ok", "items": items, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/system/logs/archives/{archive_name}")
async def download_system_log_archive(archive_name: str, _: dict = Depends(require_platform_admin)):
    if "/" in archive_name or "\\" in archive_name or not archive_name.endswith(".zip"):
        raise HTTPException(status_code=400, detail="invalid archive_name")
    log_root = _runtime_log_root()
    archive_path = os.path.join(log_root, "_archive", archive_name)
    if not os.path.exists(archive_path):
        raise HTTPException(status_code=404, detail="archive not found")
    return FileResponse(path=archive_path, filename=archive_name, media_type="application/zip")


@app.get("/api/v1/admin/kbs/{kb_id}/documents")
async def list_documents(kb_id: str, request: Request, limit: int = 100, _: dict = Depends(require_kb_read)) -> Dict:
    return {
        "status": "ok",
        "kb_id": kb_id,
        "items": kb_store.list_documents(request.state.tenant_id, kb_id, limit=limit),
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}")
async def get_document(kb_id: str, doc_id: str, request: Request, _: dict = Depends(require_kb_read)) -> Dict:
    data = kb_store.get_document(request.state.tenant_id, kb_id, doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="document not found")
    return {"status": "ok", "item": data, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/source")
async def download_document_source(
    kb_id: str,
    doc_id: str,
    request: Request,
    _: dict = Depends(require_kb_read),
):
    data = kb_store.get_document(request.state.tenant_id, kb_id, doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="document not found")
    bucket = str(data.get("source_object_bucket") or "")
    key = str(data.get("source_object_key") or "")
    checksum = str(data.get("source_object_sha256") or "")
    if not bucket or not key or not checksum:
        raise HTTPException(status_code=404, detail="document source object is unavailable")
    try:
        raw = await asyncio.to_thread(object_storage.get_bytes, bucket, key, checksum)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="document source object is unavailable") from exc
    filename = os.path.basename(str(data.get("filename") or "document.bin").replace("\\", "/")) or "document.bin"
    encoded_filename = quote(filename, safe="._-")
    return StreamingResponse(
        io.BytesIO(raw),
        media_type=str(data.get("source_content_type") or "application/octet-stream"),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "X-Content-SHA256": checksum,
        },
    )


@app.patch("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/freeze")
async def freeze_document(
    kb_id: str,
    doc_id: str,
    body: ApiFreezeRequest,
    request: Request,
    _: dict = Depends(require_kb_write),
) -> Dict:
    ok = kb_store.set_document_frozen(request.state.tenant_id, kb_id, doc_id, bool(body.frozen))
    if not ok:
        raise HTTPException(status_code=404, detail="document not found")
    data = kb_store.get_document(request.state.tenant_id, kb_id, doc_id)
    return {"status": "ok", "item": data, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks")
async def list_chunks(kb_id: str, doc_id: str, request: Request, limit: int = 300, _: dict = Depends(require_kb_read)) -> Dict:
    drafts = kb_store.list_chunk_drafts(request.state.tenant_id, kb_id, doc_id)
    draft_map = {item["chunk_id"]: item for item in drafts}
    chunks = kb_store.list_chunks(request.state.tenant_id, kb_id, doc_id, limit=limit)
    for chunk in chunks:
        if chunk.get("chunk_id") in draft_map:
            chunk["draft"] = draft_map[chunk["chunk_id"]]
    return {"status": "ok", "doc_id": doc_id, "items": chunks, "drafts": drafts, "trace_id": request.state.trace_id}


@app.patch("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/freeze")
async def freeze_chunk(
    kb_id: str,
    doc_id: str,
    chunk_id: str,
    body: ApiFreezeRequest,
    request: Request,
    _: dict = Depends(require_kb_write),
) -> Dict:
    ok = kb_store.set_chunk_frozen(request.state.tenant_id, kb_id, doc_id, chunk_id, bool(body.frozen))
    if not ok:
        raise HTTPException(status_code=404, detail="chunk not found")
    return {
        "status": "ok",
        "item": {
            "kb_id": kb_id,
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "frozen": bool(body.frozen),
        },
        "trace_id": request.state.trace_id,
    }


@app.put("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft")
async def upsert_chunk_draft(
    kb_id: str,
    doc_id: str,
    chunk_id: str,
    body: ApiChunkDraftRequest,
    request: Request,
    _: dict = Depends(require_kb_write),
) -> Dict:
    if body.action == "edit" and not (body.text or "").strip():
        raise HTTPException(status_code=400, detail="text is required for edit action")
    kb_store.set_chunk_draft(request.state.tenant_id, kb_id, doc_id, chunk_id, body.action, body.text)
    return {"status": "ok", "chunk_id": chunk_id, "action": body.action, "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft")
async def clear_chunk_draft(kb_id: str, doc_id: str, chunk_id: str, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    deleted = kb_store.clear_chunk_draft(request.state.tenant_id, kb_id, doc_id, chunk_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="draft not found")
    return {"status": "ok", "deleted": chunk_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks/commit")
async def commit_chunk_drafts(kb_id: str, doc_id: str, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    doc = kb_store.get_document(request.state.tenant_id, kb_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    task_id = str(uuid.uuid4())
    dedup = _try_acquire_submission_lock(
        request=request,
        kb_id=kb_id,
        doc_id=doc_id,
        task_id=task_id,
        task_type="CHUNK_COMMIT",
        lock_scope="DOC_COMMIT",
    )
    if dedup:
        dedup["doc_id"] = doc_id
        return dedup
    payload = {
        "task_id": task_id,
        "task_type": "DOC_REINDEX",
        "tenant_id": request.state.tenant_id,
        "actor_id": request.state.actor_id,
        "trace_id": request.state.trace_id,
        "source_service": request.state.source_service,
        "kb_id": kb_id,
        "doc_id": doc_id,
    }
    kb_store.set_document_status(request.state.tenant_id, kb_id, doc_id, "EMBEDDING")
    kb_store.create_task_record(
        task_id,
        request.state.tenant_id,
        kb_id,
        doc_id,
        "CHUNK_COMMIT",
        "EMBEDDING",
        filename=doc.get("filename", "unknown.txt"),
    )
    _send_persistent_task("app.tasks.commit_chunk_drafts", payload, "embed_queue", task_id)
    return {"status": "accepted", "task_id": task_id, "doc_id": doc_id, "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}")
async def delete_document(kb_id: str, doc_id: str, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    task_ids = kb_store.find_active_doc_task_ids(request.state.tenant_id, kb_id, doc_id)
    for task_id in task_ids:
        kb_store.request_cancel_task(task_id)
        try:
            celery_app.control.revoke(task_id, terminate=True)
        except Exception:
            pass
    deleted = kb_store.delete_document(request.state.tenant_id, kb_id, doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="document not found")
    _schedule_cross_store_repair()
    return {"status": "ok", "deleted": doc_id, "cancelled_task_ids": task_ids, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/reindex")
async def reindex_document(
    kb_id: str,
    doc_id: str,
    body: ApiReindexRequest,
    request: Request,
    _: dict = Depends(require_kb_write),
) -> Dict:
    chunk_size, chunk_overlap, chunk_delimiter = _validate_chunk_settings(
        chunk_method=body.chunk_method,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
        chunk_delimiter=body.chunk_delimiter,
    )
    method = _validate_chunk_method(body.chunk_method)
    data = kb_store.get_document(request.state.tenant_id, kb_id, doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="document not found")
    content = data.get("raw_text", "") or kb_store.build_document_text(request.state.tenant_id, kb_id, doc_id)
    if not content:
        raise HTTPException(status_code=400, detail="document content unavailable for reindex")
    task_id = str(uuid.uuid4())
    dedup = _try_acquire_submission_lock(
        request=request,
        kb_id=kb_id,
        doc_id=doc_id,
        task_id=task_id,
        task_type="DOC_REINDEX",
        lock_scope="DOC_PARSE",
    )
    if dedup:
        dedup["doc_id"] = doc_id
        return dedup
    payload = {
        "task_id": task_id,
        "tenant_id": request.state.tenant_id,
        "actor_id": request.state.actor_id,
        "trace_id": request.state.trace_id,
        "source_service": request.state.source_service,
        "kb_id": kb_id,
        "doc_id": doc_id,
        "filename": data.get("filename", "unknown.txt"),
        "content": content,
        "chunk_method": method,
        "chunk_delimiter": chunk_delimiter,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "auto_summary": False,
    }
    kb_store.set_document_chunk_config(
        request.state.tenant_id,
        kb_id,
        doc_id,
        chunk_method=method,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_delimiter=chunk_delimiter,
    )
    kb_store.create_task_record(
        task_id,
        request.state.tenant_id,
        kb_id,
        doc_id,
        "DOC_REINDEX",
        "PARSING",
        filename=data.get("filename", "unknown.txt"),
    )
    _send_persistent_task("app.tasks.parse_document", payload, "parse_queue", task_id)
    return {"status": "accepted", "task_id": task_id, "doc_id": doc_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunk")
async def chunk_document(
    kb_id: str,
    doc_id: str,
    body: ApiChunkRunRequest,
    request: Request,
    _: dict = Depends(require_kb_write),
) -> Dict:
    chunk_size, chunk_overlap, chunk_delimiter = _validate_chunk_settings(
        chunk_method=body.chunk_method,
        chunk_size=body.chunk_size,
        chunk_overlap=body.chunk_overlap,
        chunk_delimiter=body.chunk_delimiter,
    )
    method = _validate_chunk_method(body.chunk_method)
    data = kb_store.get_document(request.state.tenant_id, kb_id, doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="document not found")
    content = data.get("raw_text", "") or kb_store.build_document_text(request.state.tenant_id, kb_id, doc_id)
    if not content:
        raise HTTPException(status_code=400, detail="document content unavailable for chunking")
    task_id = str(uuid.uuid4())
    task_type = "DOC_RECHUNK" if int(data.get("chunk_count") or 0) > 0 else "DOC_CHUNK"
    dedup = _try_acquire_submission_lock(
        request=request,
        kb_id=kb_id,
        doc_id=doc_id,
        task_id=task_id,
        task_type=task_type,
        lock_scope="DOC_PARSE",
    )
    if dedup:
        dedup["doc_id"] = doc_id
        return dedup
    payload = {
        "task_id": task_id,
        "task_type": task_type,
        "tenant_id": request.state.tenant_id,
        "actor_id": request.state.actor_id,
        "trace_id": request.state.trace_id,
        "source_service": request.state.source_service,
        "kb_id": kb_id,
        "doc_id": doc_id,
        "filename": data.get("filename", "unknown.txt"),
        "content": content,
        "chunk_method": method,
        "chunk_delimiter": chunk_delimiter,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "auto_summary": False,
    }
    kb_store.set_document_chunk_config(
        request.state.tenant_id,
        kb_id,
        doc_id,
        chunk_method=method,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_delimiter=chunk_delimiter,
    )
    kb_store.create_task_record(
        task_id,
        request.state.tenant_id,
        kb_id,
        doc_id,
        task_type,
        "PARSING",
        filename=data.get("filename", "unknown.txt"),
    )
    _send_persistent_task("app.tasks.parse_document", payload, "parse_queue", task_id)
    return {
        "status": "accepted",
        "task_id": task_id,
        "task_type": task_type,
        "doc_id": doc_id,
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/summarize")
async def summarize_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    _: dict = Depends(require_kb_write),
) -> Dict:
    data = kb_store.get_document(request.state.tenant_id, kb_id, doc_id)
    if not data:
        raise HTTPException(status_code=404, detail="document not found")
    content = data.get("raw_text", "") or kb_store.build_document_text(request.state.tenant_id, kb_id, doc_id)
    if not content:
        raise HTTPException(status_code=400, detail="document content unavailable for summarize")
    task_id = str(uuid.uuid4())
    dedup = _try_acquire_submission_lock(
        request=request,
        kb_id=kb_id,
        doc_id=doc_id,
        task_id=task_id,
        task_type="DOC_SUMMARIZE",
        lock_scope="DOC_SUMMARY",
    )
    if dedup:
        dedup["doc_id"] = doc_id
        return dedup
    payload = {
        "task_id": task_id,
        "task_type": "DOC_SUMMARIZE",
        "tenant_id": request.state.tenant_id,
        "actor_id": request.state.actor_id,
        "trace_id": request.state.trace_id,
        "source_service": request.state.source_service,
        "kb_id": kb_id,
        "doc_id": doc_id,
        "filename": data.get("filename", "unknown.txt"),
        "content": content,
    }
    kb_store.create_task_record(
        task_id,
        request.state.tenant_id,
        kb_id,
        doc_id,
        "DOC_SUMMARIZE",
        "SUMMARIZING",
        filename=data.get("filename", "unknown.txt"),
    )
    _send_persistent_task("app.tasks.summarize_document", payload, "parse_queue", task_id)
    return {
        "status": "accepted",
        "task_id": task_id,
        "task_type": "DOC_SUMMARIZE",
        "doc_id": doc_id,
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/tags")
async def update_document_tags(
    kb_id: str,
    doc_id: str,
    body: ApiDocumentTagsRequest,
    request: Request,
    _: dict = Depends(require_kb_write),
) -> Dict:
    doc = kb_store.get_document(request.state.tenant_id, kb_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    tags = [str(x or "").strip() for x in (body.tags or []) if str(x or "").strip()]
    if not tags:
        raise HTTPException(status_code=400, detail="tags is required")
    mode = (body.mode or "add").strip().lower()
    if mode == "replace":
        ok = kb_store.set_document_tags(request.state.tenant_id, kb_id, doc_id, tags)
    else:
        ok = kb_store.add_document_tags(request.state.tenant_id, kb_id, doc_id, tags)
    if not ok:
        raise HTTPException(status_code=404, detail="document not found")
    item = kb_store.get_document(request.state.tenant_id, kb_id, doc_id) or {}
    return {
        "status": "ok",
        "doc_id": doc_id,
        "kb_id": kb_id,
        "mode": mode,
        "tags": item.get("tags_json") or [],
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/kbs/{kb_id}/documents/batch")
async def batch_documents(
    kb_id: str,
    body: ApiBatchDocumentsRequest,
    request: Request,
    _: dict = Depends(require_kb_write),
) -> Dict:
    operation = (body.operation or "").strip().lower()
    doc_ids = [str(x or "").strip() for x in (body.doc_ids or []) if str(x or "").strip()]
    if not doc_ids:
        raise HTTPException(status_code=400, detail="doc_ids is required")
    unique_doc_ids = list(dict.fromkeys(doc_ids))
    docs_map = {d["doc_id"]: d for d in kb_store.list_documents(request.state.tenant_id, kb_id, limit=1000)}
    missing_ids = [x for x in unique_doc_ids if x not in docs_map]
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"documents not found: {', '.join(missing_ids[:10])}")

    batch_id = str(uuid.uuid4())
    batch_name = (body.batch_name or f"批量{operation}-{datetime.now().strftime('%m%d-%H%M%S')}").strip()
    parent_task_id = str(uuid.uuid4())
    parent_task_type = f"BATCH_DOC_{operation.upper()}"
    total = len(unique_doc_ids)
    kb_store.create_task_record(
        parent_task_id,
        request.state.tenant_id,
        kb_id,
        "",
        parent_task_type,
        "BATCH_RUNNING",
        filename=batch_name,
        batch_id=batch_id,
        batch_name=batch_name,
        batch_is_parent=True,
    )
    kb_store.update_task_record(parent_task_id, "RUNNING", "BATCH_RUNNING")
    kb_store.update_task_progress(parent_task_id, "BATCH_RUNNING", 0, total, 0)
    kb_store.create_task_batch(
        batch_id=batch_id,
        tenant_id=request.state.tenant_id,
        kb_id=kb_id,
        operation=operation,
        batch_name=batch_name,
        params=body.model_dump(),
        total_items=total,
        created_by=request.state.actor_id,
        parent_task_id=parent_task_id,
    )

    accepted: list[dict] = []
    failed: list[dict] = []
    method = None
    chunk_size = None
    chunk_overlap = None
    chunk_delimiter = None
    if operation in {"chunk", "embed"}:
        chunk_size, chunk_overlap, chunk_delimiter = _validate_chunk_settings(
            chunk_method=body.chunk_method,
            chunk_size=body.chunk_size,
            chunk_overlap=body.chunk_overlap,
            chunk_delimiter=body.chunk_delimiter,
        )
        method = _validate_chunk_method(body.chunk_method)

    if operation == "move":
        target_kb_id = str(body.target_kb_id or "").strip()
        if not target_kb_id:
            raise HTTPException(status_code=400, detail="target_kb_id is required for move")
        kb_store.ensure_kb(request.state.tenant_id, target_kb_id)
    else:
        target_kb_id = ""

    if operation == "tag":
        tags = [str(x or "").strip() for x in (body.tags or []) if str(x or "").strip()]
        if not tags:
            raise HTTPException(status_code=400, detail="tags is required for tag")
    else:
        tags = []

    for idx, doc_id in enumerate(unique_doc_ids):
        doc = docs_map[doc_id]
        filename = doc.get("filename", "unknown.txt")
        active_task_ids = kb_store.find_active_doc_task_ids(request.state.tenant_id, kb_id, doc_id)
        if active_task_ids:
            failed.append(
                {
                    "doc_id": doc_id,
                    "filename": filename,
                    "reason": f"document has active task ({len(active_task_ids)})",
                }
            )
            continue
        if operation in {"chunk", "embed", "summarize"}:
            content = doc.get("raw_text", "") or kb_store.build_document_text(request.state.tenant_id, kb_id, doc_id)
            if not content:
                failed.append({"doc_id": doc_id, "filename": filename, "reason": "document content unavailable"})
                continue
            task_id = str(uuid.uuid4())
            if operation == "chunk":
                task_type = "DOC_RECHUNK" if int(doc.get("chunk_count") or 0) > 0 else "DOC_CHUNK"
                dedup = _try_acquire_submission_lock(
                    request=request,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    task_id=task_id,
                    task_type=task_type,
                    lock_scope="DOC_PARSE",
                )
                if dedup:
                    accepted.append({"doc_id": doc_id, "filename": filename, "task_id": dedup.get("task_id"), "index": idx + 1, "deduplicated": True})
                    continue
                kb_store.set_document_chunk_config(
                    request.state.tenant_id,
                    kb_id,
                    doc_id,
                    chunk_method=method,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    chunk_delimiter=chunk_delimiter,
                )
                payload = _build_parse_payload(
                    request=request,
                    task_id=task_id,
                    task_type=task_type,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    filename=filename,
                    content=content,
                    chunk_method=method,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    chunk_delimiter=chunk_delimiter,
                    batch_id=batch_id,
                    batch_name=batch_name,
                )
                kb_store.create_task_record(
                    task_id,
                    request.state.tenant_id,
                    kb_id,
                    doc_id,
                    task_type,
                    "PARSING",
                    filename=filename,
                    batch_id=batch_id,
                    batch_name=batch_name,
                )
                _send_persistent_task("app.tasks.parse_document", payload, "parse_queue", task_id)
            elif operation == "embed":
                # For docs without chunks, run prerequisite chunking before re-embedding.
                has_chunks = int(doc.get("chunk_count") or 0) > 0
                task_type = "DOC_REINDEX" if has_chunks else "DOC_CHUNK"
                dedup = _try_acquire_submission_lock(
                    request=request,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    task_id=task_id,
                    task_type=task_type,
                    lock_scope="DOC_PARSE",
                )
                if dedup:
                    accepted.append({"doc_id": doc_id, "filename": filename, "task_id": dedup.get("task_id"), "index": idx + 1, "deduplicated": True})
                    continue
                kb_store.set_document_chunk_config(
                    request.state.tenant_id,
                    kb_id,
                    doc_id,
                    chunk_method=method,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    chunk_delimiter=chunk_delimiter,
                )
                payload = _build_parse_payload(
                    request=request,
                    task_id=task_id,
                    task_type=task_type,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    filename=filename,
                    content=content,
                    chunk_method=method,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    chunk_delimiter=chunk_delimiter,
                    batch_id=batch_id,
                    batch_name=batch_name,
                )
                kb_store.create_task_record(
                    task_id,
                    request.state.tenant_id,
                    kb_id,
                    doc_id,
                    task_type,
                    "PARSING",
                    filename=filename,
                    batch_id=batch_id,
                    batch_name=batch_name,
                )
                _send_persistent_task("app.tasks.parse_document", payload, "parse_queue", task_id)
            else:
                task_type = "DOC_SUMMARIZE"
                dedup = _try_acquire_submission_lock(
                    request=request,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    task_id=task_id,
                    task_type=task_type,
                    lock_scope="DOC_SUMMARY",
                )
                if dedup:
                    accepted.append({"doc_id": doc_id, "filename": filename, "task_id": dedup.get("task_id"), "index": idx + 1, "deduplicated": True})
                    continue
                payload = {
                    "task_id": task_id,
                    "task_type": task_type,
                    "tenant_id": request.state.tenant_id,
                    "actor_id": request.state.actor_id,
                    "trace_id": request.state.trace_id,
                    "source_service": request.state.source_service,
                    "kb_id": kb_id,
                    "doc_id": doc_id,
                    "filename": filename,
                    "content": content,
                    "batch_id": batch_id,
                    "batch_name": batch_name,
                }
                kb_store.create_task_record(
                    task_id,
                    request.state.tenant_id,
                    kb_id,
                    doc_id,
                    task_type,
                    "SUMMARIZING_DOC",
                    filename=filename,
                    batch_id=batch_id,
                    batch_name=batch_name,
                )
                _send_persistent_task("app.tasks.summarize_document", payload, "parse_queue", task_id)
            accepted.append({"doc_id": doc_id, "filename": filename, "task_id": task_id, "index": idx + 1})
            continue

        task_id = str(uuid.uuid4())
        if operation == "move":
            task_type = "DOC_MOVE"
            stage = "MOVING"
            lock_scope = "DOC_MOVE"
        elif operation == "tag":
            task_type = "DOC_TAG"
            stage = "TAGGING"
            lock_scope = "DOC_TAG"
        else:
            task_type = "DOC_DELETE"
            stage = "DELETING"
            lock_scope = "DOC_DELETE"
        dedup = _try_acquire_submission_lock(
            request=request,
            kb_id=kb_id,
            doc_id=doc_id,
            task_id=task_id,
            task_type=task_type,
            lock_scope=lock_scope,
        )
        if dedup:
            accepted.append({"doc_id": doc_id, "filename": filename, "task_id": dedup.get("task_id"), "index": idx + 1, "deduplicated": True})
            continue
        kb_store.create_task_record(
            task_id,
            request.state.tenant_id,
            kb_id,
            doc_id,
            task_type,
            stage,
            filename=filename,
            batch_id=batch_id,
            batch_name=batch_name,
        )
        try:
            if operation == "move":
                payload = {
                    "task_id": task_id,
                    "task_type": task_type,
                    "tenant_id": request.state.tenant_id,
                    "actor_id": request.state.actor_id,
                    "trace_id": request.state.trace_id,
                    "source_service": request.state.source_service,
                    "kb_id": kb_id,
                    "doc_id": doc_id,
                    "filename": filename,
                    "target_kb_id": target_kb_id,
                    "batch_id": batch_id,
                    "batch_name": batch_name,
                }
                _send_persistent_task("app.tasks.move_document", payload, "parse_queue", task_id)
            elif operation == "tag":
                payload = {
                    "task_id": task_id,
                    "task_type": task_type,
                    "tenant_id": request.state.tenant_id,
                    "actor_id": request.state.actor_id,
                    "trace_id": request.state.trace_id,
                    "source_service": request.state.source_service,
                    "kb_id": kb_id,
                    "doc_id": doc_id,
                    "filename": filename,
                    "tags": tags,
                    "batch_id": batch_id,
                    "batch_name": batch_name,
                }
                _send_persistent_task("app.tasks.tag_document", payload, "parse_queue", task_id)
            else:
                payload = {
                    "task_id": task_id,
                    "task_type": task_type,
                    "tenant_id": request.state.tenant_id,
                    "actor_id": request.state.actor_id,
                    "trace_id": request.state.trace_id,
                    "source_service": request.state.source_service,
                    "kb_id": kb_id,
                    "doc_id": doc_id,
                    "filename": filename,
                    "batch_id": batch_id,
                    "batch_name": batch_name,
                }
                _send_persistent_task("app.tasks.delete_document", payload, "parse_queue", task_id)
            accepted.append({"doc_id": doc_id, "filename": filename, "task_id": task_id, "index": idx + 1})
        except Exception as exc:
            failed.append({"doc_id": doc_id, "filename": filename, "reason": str(exc)})
            kb_store.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
            kb_store.append_task_event(task_id, request.state.tenant_id, kb_id, doc_id, "TASK_FAILED", "FAILED", {"error_message": str(exc)})

    kb_store.refresh_task_batch(batch_id, tenant_id=request.state.tenant_id, kb_id=kb_id)
    return {
        "status": "accepted",
        "batch_id": batch_id,
        "batch_name": batch_name,
        "parent_task_id": parent_task_id,
        "operation": operation,
        "accepted_count": len(accepted),
        "failed_count": len(failed),
        "accepted": accepted,
        "failed": failed,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/tasks/batches/{batch_id}")
async def get_task_batch(batch_id: str, request: Request, limit: int = 300, _: dict = Depends(require_kb_read)) -> Dict:
    batch = kb_store.get_task_batch(batch_id, request.state.tenant_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch not found")
    items = kb_store.list_batch_tasks(batch_id, request.state.tenant_id, limit=limit)
    return {"status": "ok", "item": batch, "items": items, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/tasks/batches/{batch_id}/retry-failed")
async def retry_task_batch_failed(batch_id: str, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    batch = kb_store.get_task_batch(batch_id, request.state.tenant_id)
    if not batch:
        raise HTTPException(status_code=404, detail="batch not found")
    items = kb_store.list_batch_tasks(batch_id, request.state.tenant_id, limit=1000)
    failed_doc_ids = [str(x.get("doc_id") or "") for x in items if not x.get("batch_is_parent") and str(x.get("status") or "").upper() == "FAILED" and str(x.get("doc_id") or "").strip()]
    if not failed_doc_ids:
        return {"status": "ok", "batch_id": batch_id, "accepted_count": 0, "failed_count": 0, "accepted": [], "failed": [], "trace_id": request.state.trace_id}
    params = batch.get("params_json") or {}
    body = ApiBatchDocumentsRequest(
        operation=str(batch.get("operation") or ""),
        doc_ids=failed_doc_ids,
        chunk_method=str(params.get("chunk_method") or "sentence"),
        chunk_delimiter=params.get("chunk_delimiter"),
        chunk_size=int(params.get("chunk_size") or 500),
        chunk_overlap=int(50 if params.get("chunk_overlap") is None else params.get("chunk_overlap")),
        target_kb_id=params.get("target_kb_id"),
        tags=params.get("tags") or [],
        batch_name=f"{batch.get('batch_name') or '批次'}-重试",
    )
    return await batch_documents(str(batch.get("kb_id") or ""), body, request, _)


@app.get("/api/v1/admin/tasks")
async def list_admin_tasks(
    request: Request,
    kb_id: str | None = None,
    limit: int = 100,
    _: dict = Depends(require_kb_read),
) -> Dict:
    items = kb_store.list_tasks(request.state.tenant_id, kb_id=kb_id, limit=limit)
    return {"status": "ok", "items": items, "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/tasks")
async def clear_admin_tasks(
    request: Request,
    kb_id: str | None = None,
    _: dict = Depends(require_kb_write),
) -> Dict:
    result = kb_store.clear_finished_tasks(request.state.tenant_id, kb_id=kb_id)
    return {
        "status": "ok",
        "scope": {"tenant_id": request.state.tenant_id, "kb_id": kb_id},
        "result": result,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/tasks/stream")
async def stream_admin_tasks(
    request: Request,
    kb_id: str | None = None,
    _: dict = Depends(require_kb_read),
):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            items = kb_store.list_tasks(request.state.tenant_id, kb_id=kb_id, limit=100)
            payload = {
                "ts": int(time.time()),
                "items": items,
            }
            yield f"event: tasks\ndata: {json.dumps(payload, ensure_ascii=False, default=_json_default)}\n\n"
            await asyncio.sleep(2.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/api/v1/admin/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    record = kb_store.get_task(task_id, tenant_id=request.state.tenant_id)
    if not record:
        raise HTTPException(status_code=404, detail="task not found")
    kb_id_for_refresh = str(record.get("kb_id") or "")
    batch_id_for_refresh = str(record.get("batch_id") or "")
    cancelled_task_ids: list[str] = []
    if kb_store.request_cancel_task(task_id, tenant_id=request.state.tenant_id):
        cancelled_task_ids.append(task_id)
        try:
            celery_app.control.revoke(task_id, terminate=True)
        except Exception:
            pass
        kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.append_task_event(
            task_id,
            str(record.get("tenant_id") or request.state.tenant_id),
            kb_id_for_refresh,
            str(record.get("doc_id") or ""),
            "TASK_CANCELLED",
            "CANCELLED",
            {},
        )

    # Batch parent: cascade cancel to active children.
    if bool(record.get("batch_is_parent")) and batch_id_for_refresh:
        child_ids = kb_store.find_active_batch_task_ids(
            request.state.tenant_id,
            kb_id_for_refresh,
            batch_id_for_refresh,
        )
        for child_id in child_ids:
            if not kb_store.request_cancel_task(child_id, tenant_id=request.state.tenant_id):
                continue
            cancelled_task_ids.append(child_id)
            try:
                celery_app.control.revoke(child_id, terminate=True)
            except Exception:
                pass
            kb_store.update_task_record(child_id, "CANCELLED", "CANCELLED")
            child = kb_store.get_task(child_id, tenant_id=request.state.tenant_id) or {}
            kb_store.append_task_event(
                child_id,
                str(child.get("tenant_id") or request.state.tenant_id),
                str(child.get("kb_id") or kb_id_for_refresh),
                str(child.get("doc_id") or ""),
                "TASK_CANCELLED",
                "CANCELLED",
                {},
            )
        kb_store.refresh_task_batch(
            batch_id_for_refresh,
            tenant_id=request.state.tenant_id,
            kb_id=kb_id_for_refresh,
        )
    return {
        "status": "ok",
        "task_id": task_id,
        "state": "CANCELLED",
        "cancelled_task_ids": cancelled_task_ids,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/tasks/{task_id}/events")
async def list_task_events(task_id: str, request: Request, limit: int = 200, _: dict = Depends(require_kb_read)) -> Dict:
    if kb_store.get_task(task_id, tenant_id=request.state.tenant_id) is None:
        raise HTTPException(status_code=404, detail="task not found")
    events = kb_store.list_task_events(task_id, limit=limit, tenant_id=request.state.tenant_id)
    return {"status": "ok", "task_id": task_id, "items": events, "trace_id": request.state.trace_id}


@app.get("/api/v1/tasks/{task_id}")
async def get_task_status(task_id: str, request: Request) -> Dict:
    record = kb_store.get_task(task_id, tenant_id=request.state.tenant_id)
    body: Dict = {
        "status": "ok",
        "task_id": task_id,
        "state": str((record or {}).get("status") or "UNKNOWN"),
        "trace_id": request.state.trace_id,
    }
    if record:
        body["record"] = record
        if record.get("error_message"):
            body["error"] = str(record.get("error_message"))
    return body


@app.post("/api/v1/retrieval/search")
async def retrieval_search(body: ApiSearchRequest, request: Request) -> Dict:
    source_tenant_id = kb_store.resolve_kb_access_tenant(request.state.tenant_id, body.kb_id)
    if not source_tenant_id:
        return {
            "status": "ok",
            "kb_id": body.kb_id,
            "tenant_id": request.state.tenant_id,
            "source_tenant_id": None,
            "evidence": [],
            "retrieval": {
                "requested_rank_method": body.rank_method,
                "effective_rank_method": "access_denied",
                "returned_count": 0,
                "context_window": body.context_window,
            },
            "trace_id": request.state.trace_id,
        }
    try:
        result = search_with_meta(
            SearchRequest(
                tenant_id=source_tenant_id,
                kb_id=body.kb_id,
                query=body.query,
                top_k=body.top_k,
                doc_ids=body.doc_ids,
                rank_method=body.rank_method,
                candidate_k=body.candidate_k,
                final_k=body.final_k,
                max_chunks_per_doc=body.max_chunks_per_doc,
                context_window=body.context_window,
                rerank_enabled=body.rerank_enabled,
                rerank_k=body.rerank_k,
                rerank_required=body.rerank_required,
            )
        )
        evidence = list(result.get("evidence") or [])
    except Exception as exc:
        msg = str(exc)
        if "Missing API key" in msg:
            raise HTTPException(
                status_code=503,
                detail="retrieval unavailable: embedding API key is missing",
            ) from exc
        raise HTTPException(status_code=500, detail=f"retrieval failed: {msg}") from exc
    return {
        "status": "ok",
        "kb_id": body.kb_id,
        "tenant_id": request.state.tenant_id,
        "source_tenant_id": source_tenant_id,
        "evidence": evidence,
        "retrieval": result.get("meta") or {},
        "trace_id": request.state.trace_id,
    }


def _rag_index_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, IndexQualityGateFailed):
        return HTTPException(status_code=409, detail={"code": "RAG_INDEX_GATE_FAILED", "message": str(exc)})
    if isinstance(exc, (IndexLifecycleError, ValueError)):
        return HTTPException(status_code=409, detail={"code": "RAG_INDEX_CONFLICT", "message": str(exc)})
    return HTTPException(status_code=500, detail={"code": "RAG_INDEX_ERROR", "message": "RAG index operation failed"})


@app.get("/api/v1/internal/kbs/{tenant_id}/{kb_id}/rag/indexes")
async def list_rag_indexes_internal(tenant_id: str, kb_id: str, request: Request) -> Dict:
    require_internal_token(request)
    return {
        "status": "ok",
        "active_index_revision": kb_store.get_active_index_revision(tenant_id, kb_id),
        "items": index_lifecycle.list_manifests(tenant_id, kb_id),
    }


@app.post("/api/v1/internal/kbs/{tenant_id}/{kb_id}/rag/indexes/bootstrap")
async def bootstrap_rag_index_internal(tenant_id: str, kb_id: str, request: Request) -> Dict:
    require_internal_token(request)
    try:
        item = await asyncio.to_thread(index_lifecycle.bootstrap_active_snapshot, tenant_id, kb_id, "control-plane")
        return {"status": "ok", "item": item}
    except Exception as exc:
        raise _rag_index_http_error(exc) from exc


@app.post("/api/v1/internal/kbs/{tenant_id}/{kb_id}/rag/indexes/build")
async def build_rag_index_internal(
    tenant_id: str,
    kb_id: str,
    body: ApiRagIndexBuildRequest,
    request: Request,
) -> Dict:
    require_internal_token(request)
    revision = body.revision or f"rag2-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    try:
        item = await asyncio.to_thread(
            index_lifecycle.build_shadow,
            tenant_id,
            kb_id,
            revision,
            config={
                "require_evaluation": body.require_evaluation,
                "qdrant_batch_size": body.qdrant_batch_size,
            },
        )
        return {"status": "ok", "item": item}
    except Exception as exc:
        raise _rag_index_http_error(exc) from exc


@app.post("/api/v1/internal/kbs/{tenant_id}/{kb_id}/rag/indexes/{revision}/evaluate")
async def evaluate_rag_index_internal(
    tenant_id: str,
    kb_id: str,
    revision: str,
    body: ApiRagIndexEvaluateRequest,
    request: Request,
) -> Dict:
    require_internal_token(request)
    try:
        item = await asyncio.to_thread(
            index_lifecycle.evaluate,
            tenant_id,
            kb_id,
            revision,
            body.cases,
            top_k=body.top_k,
            min_recall=body.min_recall,
            min_mrr=body.min_mrr,
            min_ndcg=body.min_ndcg,
        )
        return {"status": "ok", "item": item}
    except Exception as exc:
        raise _rag_index_http_error(exc) from exc


@app.post("/api/v1/internal/kbs/{tenant_id}/{kb_id}/rag/indexes/activate")
async def activate_rag_index_internal(
    tenant_id: str,
    kb_id: str,
    body: ApiRagIndexSwitchRequest,
    request: Request,
) -> Dict:
    require_internal_token(request)
    try:
        item = await asyncio.to_thread(index_lifecycle.activate, tenant_id, kb_id, body.revision, body.actor_id)
        return {"status": "ok", "item": item}
    except Exception as exc:
        raise _rag_index_http_error(exc) from exc


@app.post("/api/v1/internal/kbs/{tenant_id}/{kb_id}/rag/indexes/rollback")
async def rollback_rag_index_internal(
    tenant_id: str,
    kb_id: str,
    body: ApiRagIndexSwitchRequest,
    request: Request,
) -> Dict:
    require_internal_token(request)
    try:
        item = await asyncio.to_thread(index_lifecycle.rollback, tenant_id, kb_id, body.revision, body.actor_id)
        return {"status": "ok", "item": item}
    except Exception as exc:
        raise _rag_index_http_error(exc) from exc


@app.delete("/api/v1/internal/kbs/{tenant_id}/{kb_id}/rag/indexes/{revision}")
async def cleanup_rag_index_internal(tenant_id: str, kb_id: str, revision: str, request: Request) -> Dict:
    require_internal_token(request)
    try:
        item = await asyncio.to_thread(index_lifecycle.cleanup_failed, tenant_id, kb_id, revision)
        return {"status": "ok", "item": item}
    except Exception as exc:
        raise _rag_index_http_error(exc) from exc


@app.delete("/api/v1/internal/kbs/{tenant_id}/{kb_id}")
async def delete_knowledge_base_internal(tenant_id: str, kb_id: str, request: Request) -> Dict:
    """Execute destructive KB cleanup only after Control Plane authorization.

    Control Plane owns the public metadata command. Execution remains responsible
    for deleting documents, tasks, vectors and objects, including durable repair
    obligations when a remote store is unavailable.
    """
    require_internal_token(request)
    target_tenant = str(tenant_id or "").strip()
    target_kb = str(kb_id or "").strip()
    if not target_tenant or not target_kb:
        raise HTTPException(status_code=400, detail="tenant_id and kb_id are required")
    if not kb_store.delete_kb(target_tenant, target_kb):
        raise HTTPException(status_code=404, detail="kb not found")
    return {"status": "ok", "deleted": target_kb, "tenant_id": target_tenant}


@app.post("/api/v1/internal/tasks/revoke")
async def revoke_tasks_internal(body: ApiInternalTaskRevokeRequest, request: Request) -> Dict:
    """Best-effort broker revocation after Control commits authoritative cancellation."""
    require_internal_token(request)
    task_ids: list[str] = []
    for raw in body.task_ids:
        task_id = str(raw or "").strip()
        if not task_id or task_id in task_ids:
            continue
        if len(task_id) > 256:
            raise HTTPException(status_code=400, detail="task id is too long")
        task_ids.append(task_id)
    if not task_ids:
        raise HTTPException(status_code=400, detail="task_ids must contain at least one task id")
    if len(task_ids) > 500:
        raise HTTPException(status_code=400, detail="at most 500 task ids can be revoked at once")
    for task_id in task_ids:
        try:
            celery_app.control.revoke(task_id, terminate=True)
        except Exception:
            # Control cancellation remains authoritative; workers also observe
            # the persisted cancel flag before and during pipeline stages.
            pass
    return {"status": "accepted", "revoked_task_ids": task_ids}


@app.post("/api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/describe")
async def describe_documents_internal(
    tenant_id: str,
    kb_id: str,
    body: ApiInternalDocumentDescribeRequest,
    request: Request,
) -> Dict:
    """Return bounded non-content metadata for Control batch-command preflight."""
    require_internal_token(request)
    target_tenant = str(tenant_id or "").strip()
    target_kb = str(kb_id or "").strip()
    if not target_tenant or not target_kb:
        raise HTTPException(status_code=400, detail="tenant_id and kb_id are required")
    if not kb_store.get_kb(target_tenant, target_kb):
        raise HTTPException(status_code=404, detail="knowledge base not found")
    doc_ids = list(dict.fromkeys(str(value or "").strip() for value in body.doc_ids if str(value or "").strip()))
    if not doc_ids:
        raise HTTPException(status_code=400, detail="doc_ids must contain at least one document id")
    if any(len(doc_id) > 128 for doc_id in doc_ids):
        raise HTTPException(status_code=400, detail="document id is too long")
    documents = {
        str(item.get("doc_id") or ""): item
        for item in kb_store.describe_documents(target_tenant, target_kb, doc_ids)
    }
    items = [
        {
            "doc_id": doc_id,
            "filename": str(documents[doc_id].get("filename") or ""),
            "chunk_count": int(documents[doc_id].get("chunk_count") or 0),
            "status": str(documents[doc_id].get("status") or ""),
        }
        for doc_id in doc_ids if doc_id in documents
    ]
    missing = [doc_id for doc_id in doc_ids if doc_id not in documents]
    return {
        "status": "ok",
        "tenant_id": target_tenant,
        "kb_id": target_kb,
        "items": items,
        "missing_doc_ids": missing,
    }


@app.get("/api/v1/internal/kbs/{tenant_id}/{kb_id}/documents")
async def list_documents_internal(
    tenant_id: str,
    kb_id: str,
    request: Request,
    limit: int = 100,
) -> Dict:
    """Return document metadata to the authoritative Control Plane."""
    require_internal_token(request)
    target_tenant = str(tenant_id or "").strip()
    target_kb = str(kb_id or "").strip()
    if not target_tenant or not target_kb:
        raise HTTPException(status_code=400, detail="tenant_id and kb_id are required")
    bounded_limit = min(max(int(limit), 1), 500)
    return {
        "status": "ok",
        "kb_id": target_kb,
        "items": kb_store.list_documents(target_tenant, target_kb, limit=bounded_limit),
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/{doc_id}/chunks")
async def list_document_chunks_internal(
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    request: Request,
    limit: int = 400,
) -> Dict:
    """Return chunk metadata and text only after Control Plane authorization."""
    require_internal_token(request)
    if not kb_store.get_document(tenant_id, kb_id, doc_id):
        raise HTTPException(status_code=404, detail="document not found")
    return {
        "status": "ok",
        "kb_id": kb_id,
        "doc_id": doc_id,
        "items": kb_store.list_chunks(tenant_id, kb_id, doc_id, limit=min(max(int(limit), 1), 500)),
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/stage")
async def stage_document_internal(
    tenant_id: str,
    kb_id: str,
    request: Request,
    doc_id: str = Form(...),
    task_id: str = Form(default=""),
    process_mode: str = Form(default="UPLOAD_CLEAN_CHUNK_EMBED"),
    chunk_method: str = Form(default="sentence"),
    chunk_delimiter: str | None = Form(default=None),
    chunk_size: int = Form(default=500),
    chunk_overlap: int = Form(default=50),
    file: UploadFile = File(...),
) -> Dict:
    """Persist one Control-authorized upload without publishing a worker task."""
    require_internal_token(request)
    target_tenant = str(tenant_id or "").strip()
    target_kb = str(kb_id or "").strip()
    target_doc = str(doc_id or "").strip()
    target_task = str(task_id or "").strip()
    filename = str(file.filename or "").strip()
    if not target_tenant or not target_kb or not target_doc or not filename:
        raise HTTPException(status_code=400, detail="tenant_id, kb_id, doc_id and filename are required")
    if len(target_doc) > 128 or len(target_task) > 256 or len(filename) > 512:
        raise HTTPException(status_code=400, detail="upload identity is too long")
    allowed_modes = {"UPLOAD_ONLY", "UPLOAD_AND_CLEAN", "UPLOAD_CLEAN_CHUNK_EMBED"}
    if process_mode not in allowed_modes:
        raise HTTPException(status_code=400, detail=f"invalid process_mode, choose from {sorted(allowed_modes)}")
    chunk_size, chunk_overlap, chunk_delimiter = _validate_chunk_settings(
        chunk_method=chunk_method,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_delimiter=chunk_delimiter,
    )
    chunk_method = _validate_chunk_method(chunk_method)
    kb_store.ensure_kb(target_tenant, target_kb)

    if not target_task:
        raise HTTPException(status_code=400, detail="task_id is required for Control-authorized upload")
    task = kb_store.get_task(target_task, tenant_id=target_tenant)
    if not task or str(task.get("state_owner") or "").strip().upper() != "CONTROL":
        raise HTTPException(status_code=409, detail="Control-owned upload task is missing")
    expected_task_type = "DOC_INGEST" if process_mode == "UPLOAD_CLEAN_CHUNK_EMBED" else "DOC_UPLOAD"
    identity = (
        str(task.get("kb_id") or ""),
        str(task.get("doc_id") or ""),
        str(task.get("task_type") or "").upper(),
    )
    if identity != (target_kb, target_doc, expected_task_type):
        raise HTTPException(status_code=409, detail="upload task immutable identity mismatch")
    if str(task.get("status") or "").upper() != "PENDING" or str(task.get("stage") or "").upper() != "UPLOAD_PENDING":
        raise HTTPException(status_code=409, detail="upload task is not waiting for staging")

    upload_settings = kb_store.get_upload_settings()
    max_file_size_mb = int(upload_settings.get("max_file_size_mb", 50))
    max_file_size_bytes = max_file_size_mb * 1024 * 1024
    raw, exceeded_limit = await _read_upload_bounded(file, max_file_size_bytes)
    if exceeded_limit:
        raise HTTPException(
            status_code=413,
            detail={"code": "UPLOAD_FILE_TOO_LARGE", "message": f"file too large (> {max_file_size_mb}MB)"},
        )
    if not raw:
        raise HTTPException(status_code=400, detail={"code": "UPLOAD_FILE_EMPTY", "message": "empty file"})
    text = _extract_text_from_file(filename, raw).strip()
    if not text:
        raise HTTPException(status_code=400, detail={"code": "UPLOAD_TEXT_EMPTY", "message": "no extractable text"})
    text_to_store = preprocess_text_for_chunk(text) if process_mode == "UPLOAD_AND_CLEAN" else text
    object_ref = await _persist_document_artifacts(
        tenant_id=target_tenant,
        kb_id=target_kb,
        doc_id=target_doc,
        filename=filename,
        source_bytes=raw,
        extracted_text=text_to_store,
        source_content_type=file.content_type or "application/octet-stream",
        chunk_method=chunk_method,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_delimiter=chunk_delimiter,
    )
    kb_store.set_document_status(
        target_tenant,
        target_kb,
        target_doc,
        "CLEANED" if process_mode == "UPLOAD_AND_CLEAN" else "UPLOADED_RAW",
    )
    return {
        "status": "staged",
        "tenant_id": target_tenant,
        "kb_id": target_kb,
        "doc_id": target_doc,
        "task_id": target_task,
        "filename": filename,
        "process_mode": process_mode,
        "chunk_method": chunk_method,
        "chunk_delimiter": chunk_delimiter,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        **object_ref,
    }


@app.patch("/api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/{doc_id}/freeze")
async def freeze_document_internal(
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    body: ApiFreezeRequest,
    request: Request,
) -> Dict:
    require_internal_token(request)
    ok = kb_store.set_document_frozen(tenant_id, kb_id, doc_id, bool(body.frozen))
    if not ok:
        raise HTTPException(status_code=404, detail="document not found")
    return {
        "status": "ok",
        "item": kb_store.get_document(tenant_id, kb_id, doc_id),
        "trace_id": request.state.trace_id,
    }


@app.patch("/api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/freeze")
async def freeze_chunk_internal(
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    chunk_id: str,
    body: ApiFreezeRequest,
    request: Request,
) -> Dict:
    require_internal_token(request)
    ok = kb_store.set_chunk_frozen(tenant_id, kb_id, doc_id, chunk_id, bool(body.frozen))
    if not ok:
        raise HTTPException(status_code=404, detail="chunk not found")
    return {
        "status": "ok",
        "item": {"kb_id": kb_id, "doc_id": doc_id, "chunk_id": chunk_id, "frozen": bool(body.frozen)},
        "trace_id": request.state.trace_id,
    }


@app.put("/api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft")
async def upsert_chunk_draft_internal(
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    chunk_id: str,
    body: ApiChunkDraftRequest,
    request: Request,
) -> Dict:
    require_internal_token(request)
    if body.action == "edit" and not (body.text or "").strip():
        raise HTTPException(status_code=400, detail="text is required for edit action")
    kb_store.set_chunk_draft(tenant_id, kb_id, doc_id, chunk_id, body.action, body.text)
    return {"status": "ok", "chunk_id": chunk_id, "action": body.action, "trace_id": request.state.trace_id}


@app.delete("/api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/{doc_id}/chunks/{chunk_id}/draft")
async def clear_chunk_draft_internal(
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    chunk_id: str,
    request: Request,
) -> Dict:
    require_internal_token(request)
    deleted = kb_store.clear_chunk_draft(tenant_id, kb_id, doc_id, chunk_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="draft not found")
    return {"status": "ok", "deleted": chunk_id, "trace_id": request.state.trace_id}


@app.delete("/api/v1/internal/kbs/{tenant_id}/{kb_id}/documents/{doc_id}/staging")
async def cleanup_staged_document_internal(
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    request: Request,
    task_id: str = "",
) -> Dict:
    """Compensate a failed or expired Control upload intent by exact identity."""
    require_internal_token(request)
    target_tenant = str(tenant_id or "").strip()
    target_kb = str(kb_id or "").strip()
    target_doc = str(doc_id or "").strip()
    target_task = str(task_id or "").strip()
    if not target_tenant or not target_kb or not target_doc:
        raise HTTPException(status_code=400, detail="tenant_id, kb_id and doc_id are required")
    if target_task:
        task = kb_store.get_task(target_task, tenant_id=target_tenant)
        if not task:
            raise HTTPException(status_code=404, detail="upload task not found")
        identity = (str(task.get("kb_id") or ""), str(task.get("doc_id") or ""))
        if identity != (target_kb, target_doc) or str(task.get("state_owner") or "").upper() != "CONTROL":
            raise HTTPException(status_code=409, detail="upload cleanup identity mismatch")
        if str(task.get("stage") or "").upper() not in {"UPLOAD_PENDING", "UPLOAD_FAILED", "UPLOAD_TIMEOUT"}:
            raise HTTPException(status_code=409, detail="upload is no longer eligible for staging cleanup")
    deleted = kb_store.delete_document(target_tenant, target_kb, target_doc)
    return {"status": "ok", "deleted": deleted, "tenant_id": target_tenant, "kb_id": target_kb, "doc_id": target_doc}


@app.post("/api/v1/internal/tasks/dispatch")
async def dispatch_task_internal(body: ApiInternalTaskDispatchRequest, request: Request) -> Dict:
    """Validate and publish a Control-owned document command."""
    require_internal_token(request)
    task = kb_store.get_task(body.task_id, tenant_id=body.tenant_id)
    if not task:
        raise HTTPException(status_code=409, detail="Control-owned task record is missing")
    if str(task.get("state_owner") or "").strip().upper() != "CONTROL":
        raise HTTPException(status_code=409, detail="task is not owned by Control")
    identity = (
        str(task.get("kb_id") or ""), str(task.get("doc_id") or ""), str(task.get("task_type") or "").upper()
    )
    if identity != (body.kb_id, body.doc_id, body.task_type):
        raise HTTPException(status_code=409, detail="task immutable identity mismatch")
    batch_identity = (
        str(task.get("batch_id") or ""), str(task.get("batch_name") or ""), bool(task.get("batch_is_parent"))
    )
    if batch_identity != (body.batch_id, body.batch_name, False):
        raise HTTPException(status_code=409, detail="task immutable batch identity mismatch")
    if str(task.get("status") or "").upper() in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        return {"status": "ignored", "task_id": body.task_id, "state": task.get("status")}

    if body.task_type == "DOC_INGEST":
        return await _dispatch_control_ingest(body)
    return _dispatch_control_document_command(body, task)


async def _dispatch_control_ingest(body: ApiInternalTaskDispatchRequest) -> Dict:
    if not body.doc_id or not body.filename:
        raise HTTPException(status_code=400, detail="ingest command requires doc_id and filename")
    if body.chunk_overlap >= body.chunk_size:
        raise HTTPException(status_code=400, detail="chunk_overlap must be smaller than chunk_size")
    content_bytes = body.content.encode("utf-8")
    has_object_ref = bool(body.object_bucket and body.content_object_key and body.content_object_sha256)
    if not content_bytes and not has_object_ref:
        raise HTTPException(status_code=400, detail="ingest command has neither text nor a complete object reference")
    max_text_bytes = _bounded_env_int("INGEST_MAX_TEXT_BYTES", 10 * 1024 * 1024, 1024, 200 * 1024 * 1024)
    if len(content_bytes) > max_text_bytes:
        raise HTTPException(status_code=413, detail="ingest text exceeds configured byte limit")
    kb_store.ensure_kb(body.tenant_id, body.kb_id)
    lock = kb_store.acquire_task_dedup_lock(
        tenant_id=body.tenant_id,
        kb_id=body.kb_id,
        doc_id=body.doc_id,
        lock_scope="DOC_PARSE",
        task_id=body.task_id,
    )
    if not bool(lock.get("acquired")) and str(lock.get("task_id") or "") != body.task_id:
        raise HTTPException(status_code=409, detail="another active ingest task owns the document lock")
    payload = body.model_dump()
    if has_object_ref:
        document = kb_store.get_document(body.tenant_id, body.kb_id, body.doc_id)
        if not document:
            kb_store.release_task_dedup_locks(body.task_id)
            raise HTTPException(status_code=409, detail="staged document is missing")
        staged_identity = (
            str(document.get("filename") or ""),
            str(document.get("source_object_bucket") or ""),
            str(document.get("source_object_key") or ""),
            str(document.get("source_object_sha256") or ""),
            str(document.get("extracted_object_key") or ""),
            str(document.get("extracted_text_sha256") or ""),
            str(document.get("chunk_method") or ""),
            int(document.get("chunk_size") or 0),
            int(document.get("chunk_overlap") or 0),
            str(document.get("chunk_delimiter") or ""),
        )
        command_identity = (
            body.filename,
            body.object_bucket,
            body.source_object_key,
            body.source_object_sha256,
            body.content_object_key,
            body.content_object_sha256,
            body.chunk_method,
            body.chunk_size,
            body.chunk_overlap,
            str(body.chunk_delimiter or ""),
        )
        if staged_identity != command_identity:
            kb_store.release_task_dedup_locks(body.task_id)
            raise HTTPException(status_code=409, detail="staged document object identity mismatch")
    else:
        try:
            object_ref = await _persist_document_artifacts(
                tenant_id=body.tenant_id,
                kb_id=body.kb_id,
                doc_id=body.doc_id,
                filename=body.filename,
                source_bytes=content_bytes,
                extracted_text=body.content,
                source_content_type="text/plain; charset=utf-8",
                chunk_method="fixed",
                chunk_size=body.chunk_size,
                chunk_overlap=body.chunk_overlap,
                chunk_delimiter=None,
            )
        except Exception:
            kb_store.release_task_dedup_locks(body.task_id)
            raise
        kb_store.set_document_status(body.tenant_id, body.kb_id, body.doc_id, "UPLOADED_RAW")
        payload.update(object_ref)
    payload["content"] = ""
    _send_persistent_task("app.tasks.parse_document", payload, "parse_queue", body.task_id)
    return {
        "status": "accepted",
        "task_id": body.task_id,
        "kb_id": body.kb_id,
        "doc_id": body.doc_id,
    }


def _dispatch_control_document_command(body: ApiInternalTaskDispatchRequest, task: dict) -> Dict:
    task_routes = {
        "KB_SUMMARIZE": ("app.tasks.summarize_kb", "parse_queue", "KB_SUMMARY"),
        "DOC_SUMMARIZE": ("app.tasks.summarize_document", "parse_queue", "DOC_SUMMARY"),
        "DOC_CHUNK": ("app.tasks.parse_document", "parse_queue", "DOC_PARSE"),
        "DOC_RECHUNK": ("app.tasks.parse_document", "parse_queue", "DOC_PARSE"),
        "DOC_REINDEX": ("app.tasks.parse_document", "parse_queue", "DOC_PARSE"),
        "CHUNK_COMMIT": ("app.tasks.commit_chunk_drafts", "embed_queue", "DOC_COMMIT"),
        "DOC_MOVE": ("app.tasks.move_document", "parse_queue", "DOC_MOVE"),
        "DOC_TAG": ("app.tasks.tag_document", "parse_queue", "DOC_TAG"),
        "DOC_DELETE": ("app.tasks.delete_document", "parse_queue", "DOC_DELETE"),
    }
    route = task_routes.get(body.task_type)
    if route is None:
        raise HTTPException(status_code=400, detail="unsupported Control document command")
    kb = kb_store.get_kb(body.tenant_id, body.kb_id)
    if not kb:
        raise HTTPException(status_code=409, detail="knowledge base is missing in Execution")

    payload = body.model_dump()
    pending_chunk_config = None
    if body.task_type == "KB_SUMMARIZE":
        if body.doc_id:
            raise HTTPException(status_code=409, detail="knowledge-base summary task must not carry doc_id")
        payload["kb_name"] = str(payload.get("kb_name") or task.get("filename") or kb.get("name") or body.kb_id)
        payload["filename"] = payload["kb_name"]
    else:
        if not body.doc_id:
            raise HTTPException(status_code=400, detail="document command requires doc_id")
        document = kb_store.get_document(body.tenant_id, body.kb_id, body.doc_id)
        if body.task_type == "DOC_MOVE":
            target_kb = str(body.target_kb_id or "").strip()
            if not target_kb or target_kb == body.kb_id:
                raise HTTPException(status_code=400, detail="move command requires a different target_kb_id")
            if not kb_store.get_kb(body.tenant_id, target_kb):
                raise HTTPException(status_code=409, detail="target knowledge base is missing in Execution")
            payload["target_kb_id"] = target_kb
            if not document:
                target_document = kb_store.get_document(body.tenant_id, target_kb, body.doc_id)
                repair = kb_store.find_document_move_repair(body.tenant_id, body.kb_id, target_kb, body.doc_id)
                if target_document and repair:
                    document = target_document
        if not document:
            raise HTTPException(status_code=409, detail="document is missing in Execution")
        payload["filename"] = str(document.get("filename") or body.filename or "unknown.txt")

        if body.task_type in {"DOC_SUMMARIZE", "DOC_CHUNK", "DOC_RECHUNK", "DOC_REINDEX"}:
            _attach_existing_document_content(payload, document, body)
        if body.task_type in {"DOC_CHUNK", "DOC_RECHUNK", "DOC_REINDEX"}:
            if body.chunk_overlap >= body.chunk_size:
                raise HTTPException(status_code=400, detail="chunk_overlap must be smaller than chunk_size")
            chunk_size, chunk_overlap, chunk_delimiter = _validate_chunk_settings(
                chunk_method=body.chunk_method,
                chunk_size=body.chunk_size,
                chunk_overlap=body.chunk_overlap,
                chunk_delimiter=body.chunk_delimiter,
            )
            method = _validate_chunk_method(body.chunk_method)
            payload.update({
                "chunk_method": method,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "chunk_delimiter": chunk_delimiter,
                "auto_summary": False,
            })
            pending_chunk_config = (method, chunk_size, chunk_overlap, chunk_delimiter)
        if body.task_type == "DOC_TAG":
            tags = list(dict.fromkeys(str(value or "").strip() for value in body.tags if str(value or "").strip()))
            if not tags:
                raise HTTPException(status_code=400, detail="tag command requires tags")
            payload["tags"] = tags
            payload["tag_mode"] = body.tag_mode

    task_name, queue, lock_scope = route
    lock = kb_store.acquire_task_dedup_lock(
        tenant_id=body.tenant_id,
        kb_id=body.kb_id,
        doc_id=body.doc_id,
        lock_scope=lock_scope,
        task_id=body.task_id,
    )
    if not bool(lock.get("acquired")) and str(lock.get("task_id") or "") != body.task_id:
        raise HTTPException(status_code=409, detail="another active task owns the command lock")
    if pending_chunk_config is not None:
        method, chunk_size, chunk_overlap, chunk_delimiter = pending_chunk_config
        kb_store.set_document_chunk_config(
            body.tenant_id, body.kb_id, body.doc_id,
            chunk_method=method,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            chunk_delimiter=chunk_delimiter,
        )
    _send_persistent_task(task_name, payload, queue, body.task_id)
    return {
        "status": "accepted",
        "task_id": body.task_id,
        "task_type": body.task_type,
        "kb_id": body.kb_id,
        "doc_id": body.doc_id,
    }


def _attach_existing_document_content(
    payload: dict, document: dict, body: ApiInternalTaskDispatchRequest
) -> None:
    bucket = str(document.get("source_object_bucket") or "")
    extracted_key = str(document.get("extracted_object_key") or "")
    extracted_sha = str(document.get("extracted_text_sha256") or "")
    if bucket and extracted_key and extracted_sha:
        payload.update({
            "content": "",
            "object_bucket": bucket,
            "content_object_key": extracted_key,
            "content_object_sha256": extracted_sha,
            "source_object_key": str(document.get("source_object_key") or ""),
            "source_object_sha256": str(document.get("source_object_sha256") or ""),
            "source_object_size_bytes": int(document.get("source_object_size_bytes") or 0),
            "extracted_object_size_bytes": int(document.get("extracted_object_size_bytes") or 0),
        })
        return
    content = str(document.get("raw_text") or "") or kb_store.build_document_text(
        body.tenant_id, body.kb_id, body.doc_id
    )
    if not content:
        raise HTTPException(status_code=409, detail="document content is unavailable")
    max_text_bytes = _bounded_env_int("INGEST_MAX_TEXT_BYTES", 10 * 1024 * 1024, 1024, 200 * 1024 * 1024)
    if len(content.encode("utf-8")) > max_text_bytes:
        raise HTTPException(status_code=413, detail="document content exceeds dispatch byte limit")
    payload["content"] = content


@app.post("/api/v1/internal/control-runs/dispatch")
async def dispatch_control_run(body: ApiControlRunDispatchRequest, request: Request) -> Dict:
    require_internal_token(request)
    approval_id = str((body.approval_resume or {}).get("approval_id") or "").strip()
    task_id = f"control-run-{body.run_id}" + (f"-approval-{approval_id[:12]}" if approval_id else "")
    celery_app.send_task(
        "app.tasks.execute_control_run",
        args=[body.model_dump()],
        queue="control_run_queue",
        exchange="control_run",
        routing_key="control_run.execute",
        task_id=task_id,
        ignore_result=True,
    )
    return {"status": "accepted", "run_id": body.run_id, "task_id": task_id}


@app.post("/api/v1/agent/run")
async def run_agent(body: ApiAgentRunRequest, request: Request) -> Dict:
    source_tenant_id = kb_store.resolve_kb_access_tenant(request.state.tenant_id, body.kb_id)
    if not source_tenant_id:
        raise HTTPException(status_code=404, detail="kb not found or not accessible")
    result = agent_runtime.run(
        AgentRunRequest(
            tenant_id=source_tenant_id,
            actor_id=request.state.actor_id,
            trace_id=request.state.trace_id,
            source_service=request.state.source_service,
            kb_id=body.kb_id,
            conversation_id=body.conversation_id,
            run_id=body.run_id,
            question=body.question,
            top_k=body.top_k,
            rank_method=body.rank_method,
            context_window=body.context_window,
            rerank_enabled=body.rerank_enabled,
            rerank_k=body.rerank_k,
            rerank_required=body.rerank_required,
            allow_generation_without_evidence=body.allow_generation_without_evidence,
        )
    )
    return result


@app.get("/api/v1/admin/api-pool")
async def list_api_pool(request: Request, task: str | None = None, _: dict = Depends(require_platform_admin)) -> Dict:
    endpoints = api_pool_manager.list_endpoints(request.state.tenant_id, task=task)
    return {
        "status": "ok",
        "tenant_id": request.state.tenant_id,
        "items": [e.model_dump(exclude={"api_key"}) for e in endpoints],
        "instances": api_pool_manager.list_instances(request.state.tenant_id, task=task),
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/internal/platform-readiness")
async def platform_readiness(request: Request) -> Dict:
    require_internal_token(request)
    endpoints = api_pool_manager.list_endpoints()
    enabled = [item for item in endpoints if item.enabled and not item.frozen and bool(item.api_key)]
    llm = [item for item in enabled if item.task == "llm"]
    embedding = [item for item in enabled if item.task == "embedding"]
    storage_detail: Dict[str, Any] = {}
    object_storage_ready = False
    qdrant_ready = False
    try:
        storage_detail = await asyncio.to_thread(object_storage.governance_status)
        object_storage_ready = bool(storage_detail.get("bucket_exists"))
    except Exception as exc:
        storage_detail = {"health": "CRITICAL", "reason": str(exc)[:300]}
    try:
        qdrant_base = str(os.getenv("QDRANT_URL", "http://qdrant:6333")).rstrip("/")
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            qdrant_response = await client.get(f"{qdrant_base}/readyz")
            qdrant_ready = qdrant_response.status_code < 400
    except Exception:
        qdrant_ready = False
    return {
        "status": "ok",
        "storage_ready": bool(object_storage_ready and qdrant_ready),
        "object_storage_ready": object_storage_ready,
        "qdrant_ready": qdrant_ready,
        "object_storage": storage_detail,
        "provider_ready": bool(llm and embedding),
        "enabled_endpoint_count": len(enabled),
        "llm_endpoint_count": len(llm),
        "embedding_endpoint_count": len(embedding),
        "missing_capabilities": [
            name for name, available in (("llm", bool(llm)), ("embedding", bool(embedding))) if not available
        ],
    }


@app.get("/api/v1/admin/api-pool/consumer-endpoints")
async def list_api_consumer_endpoints(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    return {
        "status": "ok",
        "items": API_CONSUMER_ENDPOINTS,
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/api-pool")
async def create_or_update_api_pool(
    body: ApiPoolUpsertRequest, request: Request, _: dict = Depends(require_platform_admin)
) -> Dict:
    try:
        endpoint = api_pool_manager.upsert_endpoint(ApiEndpoint(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "item": endpoint.model_dump(exclude={"api_key"}), "trace_id": request.state.trace_id}


@app.put("/api/v1/admin/api-pool/{endpoint_id}")
async def update_api_pool(
    endpoint_id: str,
    body: ApiPoolUpsertRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    if endpoint_id != body.endpoint_id:
        raise HTTPException(status_code=400, detail="endpoint_id mismatch")
    try:
        endpoint = api_pool_manager.upsert_endpoint(ApiEndpoint(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "item": endpoint.model_dump(exclude={"api_key"}), "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/api-pool/{endpoint_id}")
async def delete_api_pool(endpoint_id: str, request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    deleted = api_pool_manager.delete_endpoint(endpoint_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="endpoint not found")
    return {"status": "ok", "deleted": endpoint_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/api-pool/{endpoint_id}/set-default")
async def set_default_api_pool(endpoint_id: str, request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    try:
        default_id = api_pool_manager.set_default(endpoint_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "default_endpoint_id": default_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/api-pool/reload-default")
async def reload_default_api_pool(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    raise HTTPException(status_code=410, detail="API pool file defaults are disabled; use API management UI/database")


@app.get("/api/v1/admin/aux-api-pool")
async def list_aux_api_pool(request: Request, task: str | None = None, _: dict = Depends(require_platform_admin)) -> Dict:
    endpoints = aux_api_pool_manager.list_endpoints(request.state.tenant_id, task=task)
    return {
        "status": "ok",
        "tenant_id": request.state.tenant_id,
        "items": [e.model_dump(exclude={"api_key"}) for e in endpoints],
        "instances": aux_api_pool_manager.list_instances(request.state.tenant_id, task=task),
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/aux-api-pool")
async def create_or_update_aux_api_pool(
    body: ApiPoolUpsertRequest, request: Request, _: dict = Depends(require_platform_admin)
) -> Dict:
    try:
        endpoint = aux_api_pool_manager.upsert_endpoint(ApiEndpoint(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "item": endpoint.model_dump(exclude={"api_key"}), "trace_id": request.state.trace_id}


@app.put("/api/v1/admin/aux-api-pool/{endpoint_id}")
async def update_aux_api_pool(
    endpoint_id: str,
    body: ApiPoolUpsertRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    if endpoint_id != body.endpoint_id:
        raise HTTPException(status_code=400, detail="endpoint_id mismatch")
    try:
        endpoint = aux_api_pool_manager.upsert_endpoint(ApiEndpoint(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "item": endpoint.model_dump(exclude={"api_key"}), "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/aux-api-pool/{endpoint_id}")
async def delete_aux_api_pool(endpoint_id: str, request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    deleted = aux_api_pool_manager.delete_endpoint(endpoint_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="endpoint not found")
    return {"status": "ok", "deleted": endpoint_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/aux-api-pool/{endpoint_id}/set-default")
async def set_default_aux_api_pool(
    endpoint_id: str,
    request: Request,
    task: str | None = None,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    try:
        default_id = aux_api_pool_manager.set_default(endpoint_id, task=task)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "default_endpoint_id": default_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/aux-api-pool/reload-default")
async def reload_default_aux_api_pool(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    raise HTTPException(status_code=410, detail="Aux API pool file defaults are disabled; use API management UI/database")


def _api_pool_manager_from_name(pool: str):
    p = (pool or "main").strip().lower()
    if p == "main":
        return api_pool_manager
    if p == "aux":
        return aux_api_pool_manager
    raise HTTPException(status_code=400, detail="invalid pool, use main|aux")


def _key_lifecycle_status(expires_at: str | None, near_expiry_days: int) -> tuple[str, int | None]:
    if not expires_at:
        return ("NO_EXPIRY", None)
    try:
        exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        days_left = int((exp - now).total_seconds() // 86400)
        if days_left < 0:
            return ("EXPIRED", days_left)
        if days_left <= max(0, int(near_expiry_days)):
            return ("EXPIRING", days_left)
        return ("ACTIVE", days_left)
    except Exception:
        return ("INVALID_EXPIRY", None)


@app.get("/api/v1/admin/api-keys/lifecycle")
async def admin_api_key_lifecycle(
    request: Request,
    pool: str = "all",
    task: str | None = None,
    tenant_scope: str = "current",
    near_expiry_days: int = 7,
    expiring_only: bool = False,
    limit: int = 500,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    managers: list[tuple[str, object]] = []
    p = (pool or "all").strip().lower()
    if p == "all":
        managers = [("main", api_pool_manager), ("aux", aux_api_pool_manager)]
    elif p in {"main", "aux"}:
        managers = [(p, _api_pool_manager_from_name(p))]
    else:
        raise HTTPException(status_code=400, detail="invalid pool, use all|main|aux")

    rows: list[dict] = []
    for pool_name, manager in managers:
        endpoints = manager.list_endpoints(None if include_all_tenants else tenant_id, task=task)
        for ep in endpoints:
            status, days_left = _key_lifecycle_status(ep.expires_at, near_expiry_days)
            if expiring_only and status not in {"EXPIRING", "EXPIRED"}:
                continue
            rows.append(
                {
                    "pool": pool_name,
                    "endpoint_id": ep.endpoint_id,
                    "name": ep.name,
                    "task": ep.task,
                    "tenant_id": ep.tenant_id,
                    "enabled": bool(ep.enabled),
                    "key_fingerprint": ep.key_fingerprint,
                    "status": status,
                    "days_to_expiry": days_left,
                    "expires_at": ep.expires_at,
                    "rotation_state": ep.rotation_state,
                    "last_rotated_at": ep.last_rotated_at,
                    "updated_at": ep.updated_at,
                    "created_at": ep.created_at,
                    "rotation_note": ep.rotation_note,
                    "previous_fingerprint": ep.previous_fingerprint,
                }
            )

    rows.sort(
        key=lambda x: (
            {"EXPIRED": 0, "EXPIRING": 1, "ACTIVE": 2, "NO_EXPIRY": 3, "INVALID_EXPIRY": 4}.get(x.get("status"), 99),
            x.get("days_to_expiry") if x.get("days_to_expiry") is not None else 999999,
            str(x.get("updated_at") or ""),
        )
    )
    rows = rows[: max(1, min(int(limit), 5000))]
    summary = {
        "total": len(rows),
        "expired": sum(1 for r in rows if r.get("status") == "EXPIRED"),
        "expiring": sum(1 for r in rows if r.get("status") == "EXPIRING"),
        "active": sum(1 for r in rows if r.get("status") == "ACTIVE"),
        "no_expiry": sum(1 for r in rows if r.get("status") == "NO_EXPIRY"),
    }
    return {
        "status": "ok",
        "tenant_scope": "all" if include_all_tenants else "current",
        "tenant_id": tenant_id,
        "pool": p,
        "summary": summary,
        "items": rows,
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/api-keys/{endpoint_id}/rotate")
async def admin_rotate_api_key(
    endpoint_id: str,
    body: ApiKeyRotateRequest,
    request: Request,
    pool: str = "main",
    _: dict = Depends(require_platform_admin),
) -> Dict:
    manager = _api_pool_manager_from_name(pool)
    endpoint = next((e for e in manager.list_endpoints(None) if e.endpoint_id == endpoint_id), None)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="endpoint not found")
    endpoint.api_key = body.new_api_key
    endpoint.expires_at = body.expires_at
    endpoint.rotation_state = "ACTIVE"
    endpoint.rotation_note = (body.note or "").strip()
    updated = manager.upsert_endpoint(endpoint)
    return {
        "status": "ok",
        "pool": pool,
        "item": updated.model_dump(exclude={"api_key"}),
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/api-keys/lifecycle/alerts/evaluate")
async def admin_evaluate_api_key_lifecycle_alerts(
    body: ApiKeyLifecycleEvaluateRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    conf = _load_runtime_config_json()
    warn_days = _parse_warn_days(body.warn_days or conf.get("api_key_expiry_alert_days"))
    scan_limit = int(conf.get("api_key_expiry_alert_scan_limit", 500) or 500)
    suppress_seconds = int(conf.get("api_key_alert_suppress_seconds", 600) or 600)
    escalate_trigger_count = int(conf.get("api_key_alert_escalate_trigger_count", 3) or 3)
    notify_webhook = str(conf.get("api_key_alert_notify_webhook_url", "") or "").strip()
    notify_cooldown_seconds = int(conf.get("api_key_alert_notify_cooldown_seconds", 300) or 300)
    notify_timeout_seconds = int(conf.get("api_key_alert_notify_timeout_seconds", 5) or 5)
    notify_retry_max = int(conf.get("api_key_alert_notify_retry_max", 2) or 2)
    notify_retry_backoff_ms = int(conf.get("api_key_alert_notify_retry_backoff_ms", 250) or 250)
    limit = max(1, min(int(body.limit or scan_limit), max(1, scan_limit)))
    lifecycle = await admin_api_key_lifecycle(
        request=request,
        pool=body.pool,
        task=(body.task or None),
        tenant_scope=body.tenant_scope,
        near_expiry_days=body.near_expiry_days,
        expiring_only=body.expiring_only,
        limit=limit,
        _={},
    )
    data = kb_store.evaluate_api_key_lifecycle_alerts(
        tenant_id=request.state.tenant_id,
        items=lifecycle.get("items") or [],
        warn_days=warn_days,
        suppress_seconds=suppress_seconds,
        escalate_trigger_count=escalate_trigger_count,
    )
    try:
        for one in (data.get("opened") or [])[:100]:
            level = str(one.get("level") or "WARN").upper()
            risk_level = "MEDIUM"
            if level == "HIGH":
                risk_level = "HIGH"
            elif level == "CRITICAL":
                risk_level = "CRITICAL"
            _insert_system_log_entry_manual(
                category="api_key_lifecycle",
                message=f"api key lifecycle alert opened: {one.get('pool')}/{one.get('endpoint_id')} days={one.get('days_to_expiry')}",
                source="execution-api",
                role=request.state.role or "-",
                status="OPEN",
                path="/api/v1/admin/api-keys/lifecycle/alerts/evaluate",
                event="kb.api.key.lifecycle.alert.open",
                resource_type="api_key_lifecycle_alert",
                trace_id=request.state.trace_id,
                request_id=request.state.trace_id,
                risk_level=risk_level,
                decision="ALERT",
                reason_code="API_KEY_EXPIRY",
                trigger_rule="api_key_expiry_days",
                level=("error" if level in {"HIGH", "CRITICAL"} else "warn"),
            )
    except Exception:
        pass
    if notify_webhook and int(data.get("opened_count") or 0) > 0:
        try:
            opened_rows = [x for x in (data.get("opened") or []) if int(x.get("alert_id") or 0) > 0]
            due_alert_ids = kb_store.mark_api_key_lifecycle_alerts_notified(
                tenant_id=request.state.tenant_id,
                alert_ids=[int(x.get("alert_id")) for x in opened_rows],
                cooldown_seconds=notify_cooldown_seconds,
            )
            due_set = {int(x) for x in due_alert_ids}
            due_rows = [x for x in opened_rows if int(x.get("alert_id") or 0) in due_set]
            if not due_rows:
                return {
                    "status": "ok",
                    **data,
                    "notified_count": 0,
                    "trace_id": request.state.trace_id,
                }
            payload = {
                "type": "api_key_lifecycle_alert",
                "tenant_id": request.state.tenant_id,
                "opened_count": int(data.get("opened_count") or 0),
                "suppressed_count": int(data.get("suppressed_count") or 0),
                "notified_count": len(due_rows),
                "opened": due_rows[:20],
                "trace_id": request.state.trace_id,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            async with httpx.AsyncClient(timeout=float(max(1, notify_timeout_seconds))) as client:
                retry_max = max(0, min(notify_retry_max, 5))
                for attempt in range(retry_max + 1):
                    try:
                        resp = await client.post(notify_webhook, json=payload)
                        if 200 <= int(resp.status_code) < 300:
                            break
                    except Exception:
                        if attempt >= retry_max:
                            raise
                    if attempt < retry_max:
                        await asyncio.sleep(max(0, notify_retry_backoff_ms) / 1000.0 * (attempt + 1))
        except Exception:
            pass
    return {"status": "ok", **data, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/api-keys/lifecycle/alerts")
async def admin_list_api_key_lifecycle_alerts(
    request: Request,
    days: int = 30,
    status: str = "OPEN",
    pool: str = "",
    level: str = "",
    limit: int = 200,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    data = kb_store.list_api_key_lifecycle_alerts(
        tenant_id=request.state.tenant_id,
        days=days,
        status=status,
        pool=pool,
        level=level,
        limit=limit,
    )
    return {"status": "ok", **data, "trace_id": request.state.trace_id}


@app.patch("/api/v1/admin/api-keys/lifecycle/alerts/{alert_id}")
async def admin_update_api_key_lifecycle_alert(
    alert_id: int,
    body: ApiUsageAlertStatusRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    conf = _load_runtime_config_json()
    muted_seconds = int(conf.get("api_key_alert_muted_seconds", 1800) or 1800)
    ok = kb_store.update_api_key_lifecycle_alert_status(
        tenant_id=request.state.tenant_id,
        alert_id=alert_id,
        status=body.status,
        muted_seconds=muted_seconds,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="alert not found")
    try:
        _insert_system_log_entry_manual(
            category="api_key_lifecycle",
            message=f"api key lifecycle alert status updated: alert_id={alert_id} status={body.status}",
            source="execution-api",
            role=request.state.role or "-",
            status=body.status,
            path=f"/api/v1/admin/api-keys/lifecycle/alerts/{alert_id}",
            event="kb.api.key.lifecycle.alert.update",
            resource_type="api_key_lifecycle_alert",
            trace_id=request.state.trace_id,
            request_id=request.state.trace_id,
            risk_level="LOW",
            decision="ALLOW",
            reason_code="ALERT_STATUS_UPDATE",
            trigger_rule="manual",
            level="info",
        )
    except Exception:
        pass
    return {"status": "ok", "alert_id": int(alert_id), "next_status": body.status, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/api-keys/lifecycle/alerts/trend")
async def admin_list_api_key_lifecycle_alert_trend(
    request: Request,
    days: int = 30,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    data = kb_store.list_api_key_lifecycle_alert_trend(
        tenant_id=request.state.tenant_id,
        days=days,
    )
    return {"status": "ok", **data, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/api-keys/lifecycle/alerts/sla")
async def admin_get_api_key_lifecycle_alert_sla(
    request: Request,
    days: int = 30,
    limit: int = 20,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    data = kb_store.get_api_key_lifecycle_alert_sla(
        tenant_id=request.state.tenant_id,
        days=days,
        limit=limit,
    )
    return {"status": "ok", **data, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/usage/api-tokens/summary")
async def admin_api_token_usage_summary(
    request: Request,
    days: int = 30,
    tenant_scope: str = "current",
    limit: int = 200,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    data = kb_store.get_api_token_usage_stats(
        tenant_id=tenant_id,
        days=days,
        include_all_tenants=include_all_tenants,
        limit=limit,
    )
    return {
        "status": "ok",
        "tenant_scope": "all" if include_all_tenants else "current",
        "tenant_id": tenant_id,
        "window_days": data.get("window_days", 30),
        "totals": data.get("totals", {}),
        "by_api": data.get("by_api", []),
        "by_kb": data.get("by_kb", []),
        "by_model": data.get("by_model", []),
        "by_api_key": data.get("by_api_key", []),
        "by_task_category": data.get("by_task_category", []),
        "by_combination": data.get("by_combination", []),
        "by_day": data.get("by_day", []),
        "by_day_kb": data.get("by_day_kb", []),
        "by_day_model": data.get("by_day_model", []),
        "by_day_task_category": data.get("by_day_task_category", []),
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/usage/api-tokens/events")
async def admin_api_token_usage_events(
    request: Request,
    days: int = 30,
    page: int = 1,
    page_size: int = 50,
    tenant_scope: str = "current",
    api_path: str | None = None,
    model: str | None = None,
    api_key_fingerprint: str | None = None,
    task_category: str | None = None,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    data = kb_store.list_api_token_usage_events(
        tenant_id=tenant_id,
        days=days,
        include_all_tenants=include_all_tenants,
        page=page,
        page_size=page_size,
        api_path=api_path,
        model=model,
        api_key_fingerprint=api_key_fingerprint,
        task_category=task_category,
    )
    return {
        "status": "ok",
        "tenant_scope": "all" if include_all_tenants else "current",
        "tenant_id": tenant_id,
        "window_days": data.get("window_days", 30),
        "page": data.get("page", 1),
        "page_size": data.get("page_size", 50),
        "total": data.get("total", 0),
        "items": data.get("items", []),
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/usage/api-tokens/budgets")
async def admin_api_token_usage_budgets(
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    items = kb_store.list_api_usage_budgets(tenant_id=request.state.tenant_id)
    return {"status": "ok", "items": items, "trace_id": request.state.trace_id}


@app.put("/api/v1/admin/usage/api-tokens/budgets")
async def admin_upsert_api_token_usage_budget(
    body: ApiUsageBudgetUpsertRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    item = kb_store.upsert_api_usage_budget(
        tenant_id=request.state.tenant_id,
        scope_type=body.scope_type,
        scope_value=body.scope_value,
        daily_token_limit=body.daily_token_limit,
        daily_cost_limit=body.daily_cost_limit,
        enabled=body.enabled,
        actor_id=request.state.actor_id,
    )
    eval_data = kb_store.evaluate_api_usage_budgets(tenant_id=request.state.tenant_id)
    return {"status": "ok", "item": item, "evaluation": eval_data, "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/usage/api-tokens/budgets/{budget_id}")
async def admin_delete_api_token_usage_budget(
    budget_id: int,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    ok = kb_store.delete_api_usage_budget(tenant_id=request.state.tenant_id, budget_id=budget_id)
    if not ok:
        raise HTTPException(status_code=404, detail="budget not found")
    return {"status": "ok", "deleted": budget_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/usage/api-tokens/budgets/evaluate")
async def admin_evaluate_api_token_usage_budget(
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    data = kb_store.evaluate_api_usage_budgets(tenant_id=request.state.tenant_id)
    return {"status": "ok", **data, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/usage/api-tokens/alerts")
async def admin_api_token_usage_alerts(
    request: Request,
    days: int = 30,
    status: str = "OPEN",
    limit: int = 200,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    data = kb_store.list_api_usage_alerts(
        tenant_id=request.state.tenant_id,
        days=days,
        status=status,
        limit=limit,
    )
    return {"status": "ok", **data, "trace_id": request.state.trace_id}


@app.patch("/api/v1/admin/usage/api-tokens/alerts/{alert_id}")
async def admin_update_api_token_usage_alert_status(
    alert_id: int,
    body: ApiUsageAlertStatusRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    ok = kb_store.update_api_usage_alert_status(
        tenant_id=request.state.tenant_id,
        alert_id=alert_id,
        status=body.status,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="alert not found")
    return {"status": "ok", "alert_id": alert_id, "next_status": body.status, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/usage/api-pricing/versions")
async def admin_list_api_pricing_versions(
    request: Request,
    model: str = "",
    limit: int = 200,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    items = kb_store.list_api_model_pricing_versions(
        tenant_id=request.state.tenant_id,
        model=model,
        limit=limit,
    )
    return {"status": "ok", "items": items, "trace_id": request.state.trace_id}


@app.put("/api/v1/admin/usage/api-pricing/versions")
async def admin_upsert_api_pricing_version(
    body: ApiPricingVersionUpsertRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    try:
        item = kb_store.upsert_api_model_pricing_version(
            tenant_id=body.tenant_id or request.state.tenant_id,
            model=body.model,
            input_per_1k=body.input_per_1k,
            output_per_1k=body.output_per_1k,
            currency=body.currency,
            effective_from=body.effective_from,
            note=body.note,
            created_by=request.state.actor_id,
        )
    except ValueError as exc:
        msg = str(exc)
        if "conflict" in msg.lower():
            raise HTTPException(status_code=409, detail=msg) from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    return {"status": "ok", "item": item, "trace_id": request.state.trace_id}


@app.delete("/api/v1/admin/usage/api-pricing/versions/{version_id}")
async def admin_delete_api_pricing_version(
    version_id: int,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    ok = kb_store.delete_api_model_pricing_version(
        tenant_id=request.state.tenant_id,
        version_id=version_id,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="pricing version not found")
    return {"status": "ok", "deleted": version_id, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/usage/api-tokens/reconciliation/preview")
async def admin_api_tokens_reconciliation_preview(
    request: Request,
    days: int = 30,
    tenant_scope: str = "current",
    limit: int = 200,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    data = kb_store.get_api_usage_reconciliation_preview(
        tenant_id=tenant_id,
        days=days,
        include_all_tenants=include_all_tenants,
        limit=limit,
    )
    return {
        "status": "ok",
        "tenant_scope": "all" if include_all_tenants else "current",
        "tenant_id": tenant_id,
        **data,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/usage/api-tokens/reconciliation/breakdown")
async def admin_api_tokens_reconciliation_breakdown(
    request: Request,
    days: int = 30,
    tenant_scope: str = "current",
    dimension: str = "endpoint",
    limit: int = 200,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    data = kb_store.get_api_usage_reconciliation_breakdown(
        tenant_id=tenant_id,
        days=days,
        include_all_tenants=include_all_tenants,
        dimension=dimension,
        limit=limit,
    )
    return {
        "status": "ok",
        "tenant_scope": "all" if include_all_tenants else "current",
        "tenant_id": tenant_id,
        **data,
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/usage/api-tokens/reconciliation/alerts/evaluate")
async def admin_api_tokens_reconciliation_alerts_evaluate(
    body: ApiReconciliationEvaluateRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    conf = _load_runtime_config_json()
    ratio = float(conf.get("api_reconciliation_diff_ratio_threshold", 0.2) or 0.2)
    if body.threshold_ratio is not None:
        ratio = float(body.threshold_ratio)
    min_cost = float(conf.get("api_reconciliation_min_estimated_cost_usd", 1.0) or 1.0)
    if body.min_estimated_cost_usd is not None:
        min_cost = float(body.min_estimated_cost_usd)
    data = kb_store.evaluate_api_reconciliation_alerts(
        tenant_id=request.state.tenant_id,
        days=body.days,
        threshold_ratio=ratio,
        min_estimated_cost_usd=min_cost,
        dimension=body.dimension,
        limit=body.limit,
    )
    try:
        for one in (data.get("opened") or [])[:100]:
            level = str(one.get("level") or "WARN").upper()
            risk_level = "MEDIUM"
            if level == "HIGH":
                risk_level = "HIGH"
            elif level == "CRITICAL":
                risk_level = "CRITICAL"
            _insert_system_log_entry_manual(
                category="api_reconciliation",
                message=f"reconciliation alert opened: {one.get('dimension')}/{one.get('dim_value')} ratio={one.get('actual_ratio')}",
                source="execution-api",
                role=request.state.role or "-",
                status="OPEN",
                path="/api/v1/admin/usage/api-tokens/reconciliation/alerts/evaluate",
                event="kb.api.reconciliation.alert.open",
                resource_type="reconciliation_alert",
                trace_id=request.state.trace_id,
                request_id=request.state.trace_id,
                risk_level=risk_level,
                decision="ALERT",
                reason_code="API_RECONCILIATION_DIFF",
                trigger_rule="reconciliation_threshold",
                level=("error" if level in {"HIGH", "CRITICAL"} else "warn"),
            )
    except Exception:
        pass
    return {"status": "ok", **data, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/usage/api-tokens/reconciliation/alerts")
async def admin_api_tokens_reconciliation_alerts(
    request: Request,
    days: int = 30,
    status: str = "OPEN",
    dimension: str = "",
    limit: int = 200,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    data = kb_store.list_api_reconciliation_alerts(
        tenant_id=request.state.tenant_id,
        days=days,
        status=status,
        dimension=dimension,
        limit=limit,
    )
    return {"status": "ok", **data, "trace_id": request.state.trace_id}


@app.patch("/api/v1/admin/usage/api-tokens/reconciliation/alerts/{alert_id}")
async def admin_api_tokens_reconciliation_alerts_update_status(
    alert_id: int,
    body: ApiUsageAlertStatusRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    ok = kb_store.update_api_reconciliation_alert_status(
        tenant_id=request.state.tenant_id,
        alert_id=alert_id,
        status=body.status,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="alert not found")
    try:
        _insert_system_log_entry_manual(
            category="api_reconciliation",
            message=f"reconciliation alert status updated: alert_id={alert_id} status={body.status}",
            source="execution-api",
            role=request.state.role or "-",
            status=body.status,
            path=f"/api/v1/admin/usage/api-tokens/reconciliation/alerts/{alert_id}",
            event="kb.api.reconciliation.alert.status",
            resource_type="reconciliation_alert",
            trace_id=request.state.trace_id,
            request_id=request.state.trace_id,
            risk_level="LOW",
            decision="ALLOW",
            reason_code="API_RECONCILIATION_ALERT_STATUS",
            trigger_rule="manual",
            level="info",
        )
    except Exception:
        pass
    return {"status": "ok", "alert_id": alert_id, "next_status": body.status, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/dbviz/overview")
async def dbviz_overview(
    request: Request,
    tenant_scope: str = "current",
    _: dict = Depends(require_platform_admin),
) -> Dict:
    kb_store._ensure_indexes()
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    pg_counts: dict[str, int] = {}
    with kb_store._conn() as conn, conn.cursor() as cur:
        for table in SUPPORTED_DBVIZ_TABLES:
            try:
                if include_all_tenants:
                    cur.execute(sql.SQL("SELECT COUNT(*)::bigint AS c FROM {}").format(sql.Identifier(table)))
                else:
                    if table in DBVIZ_NON_TENANT_TABLES:
                        cur.execute(sql.SQL("SELECT COUNT(*)::bigint AS c FROM {}").format(sql.Identifier(table)))
                    else:
                        cur.execute(
                            sql.SQL("SELECT COUNT(*)::bigint AS c FROM {} WHERE tenant_id=%s").format(sql.Identifier(table)),
                            (tenant_id,),
                        )
                pg_counts[table] = int((cur.fetchone() or {}).get("c", 0))
            except Exception:
                pg_counts[table] = 0
    qdrant = kb_store.qdrant.get_collections().collections
    return {
        "status": "ok",
        "tenant_scope": "all" if include_all_tenants else "current",
        "tenant_id": tenant_id,
        "postgres": {"tables": pg_counts},
        "qdrant": {"collections": len(qdrant)},
        "mongo": {"enabled": bool(_mongo_uri()) and MongoClient is not None, "database": _mongo_db()},
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/dbviz/postgres/tables")
async def dbviz_postgres_tables(
    request: Request,
    tenant_scope: str = "current",
    _: dict = Depends(require_platform_admin),
) -> Dict:
    kb_store._ensure_indexes()
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    items: list[dict] = []
    with kb_store._conn() as conn, conn.cursor() as cur:
        for table in sorted(SUPPORTED_DBVIZ_TABLES):
            try:
                if include_all_tenants or table in DBVIZ_NON_TENANT_TABLES:
                    cur.execute(sql.SQL("SELECT COUNT(*)::bigint AS c FROM {}").format(sql.Identifier(table)))
                else:
                    cur.execute(
                        sql.SQL("SELECT COUNT(*)::bigint AS c FROM {} WHERE tenant_id=%s").format(sql.Identifier(table)),
                        (tenant_id,),
                    )
                row_count = int((cur.fetchone() or {}).get("c", 0))
            except Exception:
                row_count = 0
            items.append({"table": table, "row_count": row_count})
    return {"status": "ok", "items": items, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/dbviz/postgres/table/{table_name}")
async def dbviz_postgres_table_rows(
    table_name: str,
    request: Request,
    page: int = 1,
    page_size: int = 20,
    tenant_scope: str = "current",
    tenant_id_filter: str | None = None,
    kb_id_filter: str | None = None,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    kb_store._ensure_indexes()
    table = (table_name or "").strip().lower()
    if table not in SUPPORTED_DBVIZ_TABLES:
        raise HTTPException(status_code=400, detail=f"unsupported table: {table_name}")
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    cap = max(1, min(page_size, 200))
    current_page = max(1, page)
    offset = (current_page - 1) * cap

    where_parts: list[sql.Composed] = []
    params: list = []
    if not include_all_tenants and table not in DBVIZ_NON_TENANT_TABLES:
        where_parts.append(sql.SQL("tenant_id = %s"))
        params.append(tenant_id)
    if tenant_id_filter and table not in DBVIZ_NON_TENANT_TABLES:
        where_parts.append(sql.SQL("tenant_id = %s"))
        params.append(tenant_id_filter)
    if kb_id_filter and table in {"knowledge_bases", "documents", "chunks", "tasks", "task_events", "chunk_drafts"}:
        where_parts.append(sql.SQL("kb_id = %s"))
        params.append(kb_id_filter)

    where_sql = sql.SQL("")
    if where_parts:
        where_sql = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where_parts)

    with kb_store._conn() as conn, conn.cursor() as cur:
        try:
            count_stmt = sql.SQL("SELECT COUNT(*)::bigint AS c FROM {}").format(sql.Identifier(table)) + where_sql
            cur.execute(count_stmt, params)
            total = int((cur.fetchone() or {}).get("c", 0))

            select_stmt = (
                sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))
                + where_sql
                + sql.SQL(" ORDER BY 1 DESC LIMIT %s OFFSET %s")
            )
            cur.execute(select_stmt, [*params, cap, offset])
            rows = [dict(r) for r in cur.fetchall()]
        except Exception:
            total = 0
            rows = []

    return {
        "status": "ok",
        "table": table,
        "page": current_page,
        "page_size": cap,
        "total": total,
        "items": rows,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/dbviz/qdrant/collections")
async def dbviz_qdrant_collections(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    collections = kb_store.qdrant.get_collections().collections
    items: list[dict] = []
    for c in collections:
        name = getattr(c, "name", "")
        info = kb_store.qdrant.get_collection(name) if name else None
        vectors_count = getattr(getattr(info, "points_count", None), "__int__", lambda: info.points_count)() if info else 0
        if isinstance(vectors_count, bool):
            vectors_count = 0
        items.append(
            {
                "name": name,
                "points_count": int(getattr(info, "points_count", 0) or 0) if info else 0,
                "vectors_count": int(getattr(info, "vectors_count", 0) or 0) if info else 0,
                "status": str(getattr(getattr(info, "status", None), "value", getattr(info, "status", ""))) if info else "",
            }
        )
    items.sort(key=lambda x: x["name"])
    return {"status": "ok", "items": items, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/dbviz/qdrant/collections/{collection_name}/points")
async def dbviz_qdrant_points(
    collection_name: str,
    request: Request,
    page_size: int = 20,
    offset: str | None = None,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    cap = max(1, min(page_size, 200))
    offset_value = None
    if offset not in {None, "", "null"}:
        s = str(offset).strip()
        offset_value = int(s) if s.isdigit() else s
    points, next_offset = kb_store.qdrant.scroll(
        collection_name=collection_name,
        limit=cap,
        offset=offset_value,
        with_payload=True,
        with_vectors=False,
    )
    items = []
    for p in points:
        payload = p.payload or {}
        items.append(
            {
                "id": p.id,
                "tenant_id": payload.get("tenant_id"),
                "kb_id": payload.get("kb_id"),
                "doc_id": payload.get("doc_id"),
                "chunk_id": payload.get("chunk_id"),
                "chunk_index": payload.get("chunk_index"),
                "text_preview": str(payload.get("text", ""))[:280],
                "score": getattr(p, "score", None),
            }
        )
    return {
        "status": "ok",
        "collection": collection_name,
        "page_size": cap,
        "offset": offset_value,
        "next_offset": next_offset,
        "items": items,
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/dbviz/qdrant/collections/{collection_name}/points/{point_id}")
async def dbviz_qdrant_point_detail(
    collection_name: str,
    point_id: str,
    request: Request,
    vector_preview_dims: int = 24,
    full_vector: bool = False,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    cap = max(0, min(int(vector_preview_dims), 256))
    normalized_id: str | int = point_id
    if point_id.isdigit():
        normalized_id = int(point_id)
    points = kb_store.qdrant.retrieve(
        collection_name=collection_name,
        ids=[normalized_id],
        with_payload=True,
        with_vectors=True,
    )
    if not points:
        raise HTTPException(status_code=404, detail="point not found")
    p = points[0]
    vector = getattr(p, "vector", None)
    vector_values: list[float] = []
    if isinstance(vector, list):
        vector_values = [float(x) for x in vector]
    elif isinstance(vector, dict):
        for v in vector.values():
            if isinstance(v, list):
                vector_values = [float(x) for x in v]
                break
    vector_preview = vector_values if full_vector else vector_values[:cap]
    return {
        "status": "ok",
        "collection": collection_name,
        "item": {
            "id": p.id,
            "payload": p.payload or {},
            "vector_dim": len(vector_values),
            "vector_preview": vector_preview,
            "vector_is_full": len(vector_preview) == len(vector_values),
        },
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/dbviz/mongo/collections")
async def dbviz_mongo_collections(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    uri = _mongo_uri()
    if not uri or MongoClient is None:
        return {"status": "disabled", "reason": "MONGODB_URI not configured", "items": [], "trace_id": request.state.trace_id}
    db_name = _mongo_db()
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    db = client[db_name]
    names = db.list_collection_names()
    items = []
    for name in sorted(names):
        items.append({"name": name, "count": int(db[name].estimated_document_count())})
    return {"status": "ok", "database": db_name, "items": items, "trace_id": request.state.trace_id}


@app.get("/api/v1/admin/dbviz/mongo/collection/{collection_name}")
async def dbviz_mongo_collection_docs(
    collection_name: str,
    request: Request,
    page: int = 1,
    page_size: int = 20,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    uri = _mongo_uri()
    if not uri or MongoClient is None:
        return {"status": "disabled", "reason": "MONGODB_URI not configured", "items": [], "trace_id": request.state.trace_id}
    db_name = _mongo_db()
    cap = max(1, min(page_size, 200))
    current_page = max(1, page)
    skip = (current_page - 1) * cap
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    col = client[db_name][collection_name]
    total = int(col.count_documents({}))
    cursor = col.find({}).skip(skip).limit(cap)
    items = []
    for doc in cursor:
        flat = {}
        for k, v in doc.items():
            if k == "_id":
                flat[k] = str(v)
            elif isinstance(v, (dict, list)):
                flat[k] = f"<{type(v).__name__}>"
            else:
                flat[k] = v
        items.append(flat)
    return {
        "status": "ok",
        "database": db_name,
        "collection": collection_name,
        "page": current_page,
        "page_size": cap,
        "total": total,
        "items": items,
        "trace_id": request.state.trace_id,
    }
