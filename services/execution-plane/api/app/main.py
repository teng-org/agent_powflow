import asyncio
import base64
from datetime import date, datetime
import hashlib
import hmac
import io
import json
import os
import time
import uuid
from typing import Dict
from html.parser import HTMLParser

from docx import Document
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from psycopg import sql
from pypdf import PdfReader
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from api.app.audit_logging import (
    AuditSink,
    build_base_record,
    extra_log_types,
    summarize_body,
    summarize_error_response_body,
)
from models.api_pool import ApiEndpoint, api_pool_manager, aux_api_pool_manager
from retrieval.schemas import SearchRequest
from retrieval.service import preprocess_text_for_chunk, search
from retrieval.store import kb_store
from runtime.agent_runtime import AgentRunRequest, agent_runtime
from workers.app.celery_app import celery_app

try:
    from pymongo import MongoClient
except Exception:  # pragma: no cover
    MongoClient = None

app = FastAPI(title="execution-api", version="0.4.0")
SUPPORTED_CHUNK_METHODS = {"fixed", "sentence", "paragraph", "markdown", "delimiter"}
ADMIN_TOKEN = os.getenv("EXECUTION_ADMIN_TOKEN", "")
ADMIN_USERNAME = os.getenv("EXECUTION_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("EXECUTION_ADMIN_PASSWORD", "admin123")
ADMIN_ROLE = os.getenv("EXECUTION_ADMIN_ROLE", "PLATFORM_ADMIN")
JWT_SECRET = os.getenv("EXECUTION_JWT_SECRET", "change-me-in-production")
JWT_TTL_SECONDS = int(os.getenv("EXECUTION_JWT_TTL_SECONDS", "43200"))
AUDIT_ENABLED = os.getenv("EXECUTION_AUDIT_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
AUDIT_BASE_DIR = os.getenv("EXECUTION_AUDIT_BASE_DIR", os.path.join("logs", "kb-audit"))
EXECUTION_ENV = os.getenv("EXECUTION_ENV", "dev")
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
}

cors_origins = os.getenv("EXECUTION_CORS_ORIGINS", "http://localhost:5177,http://localhost:5180")
allowed_origins = [item.strip() for item in cors_origins.split(",") if item.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
audit_sink = AuditSink(enabled=AUDIT_ENABLED, base_dir=AUDIT_BASE_DIR, service="execution-api", env=EXECUTION_ENV)


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
    conversation_id: str | None = None
    run_id: str | None = None
    allow_generation_without_evidence: bool = True


class ApiSearchRequest(BaseModel):
    kb_id: str
    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    doc_ids: list[str] | None = None


class ApiIngestRequest(BaseModel):
    doc_id: str | None = None
    filename: str | None = None
    content: str
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=500)


class ApiReindexRequest(BaseModel):
    chunk_method: str = Field(default="fixed")
    chunk_delimiter: str | None = None
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=500)


class ApiChunkDraftRequest(BaseModel):
    action: str = Field(pattern="^(delete|edit)$")
    text: str | None = None


class ApiPoolUpsertRequest(BaseModel):
    endpoint_id: str
    name: str
    provider: str
    base_url: str
    model: str
    task: str = "llm"
    api_key: str = ""
    tenant_id: str = "*"
    enabled: bool = True
    input_per_1k: float = Field(default=0.0, ge=0.0)
    output_per_1k: float = Field(default=0.0, ge=0.0)


class ApiUploadSettingsRequest(BaseModel):
    max_file_size_mb: int = Field(default=20, ge=1, le=200)


class ApiRuntimeConfigUpdateRequest(BaseModel):
    updates: dict[str, object] = Field(default_factory=dict)


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
    status: str = Field(pattern="^(OPEN|ACK|RESOLVED)$")


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
    # PostgreSQL TEXT cannot contain NUL bytes.
    return text.replace("\x00", "")


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


def _mongo_uri() -> str:
    return (
        os.getenv("MONGODB_URI", "").strip()
        or os.getenv("MONGO_URI", "").strip()
        or os.getenv("MONGO_URL", "").strip()
    )


def _mongo_db() -> str:
    return os.getenv("MONGODB_DB", "").strip() or os.getenv("MONGO_DB", "").strip() or "agentpowflow"


def _runtime_config_path() -> str:
    env_path = os.getenv("RUNTIME_CONFIG_PATH", "").strip()
    if env_path:
        return env_path
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../config/runtime-config.json"))


def _load_runtime_config_json() -> dict:
    path = _runtime_config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_runtime_config_json(data: dict) -> None:
    path = _runtime_config_path()
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
    "global_kb_id",
    "siliconflow_api_key",
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
    "chat_llm_base_url",
    "chat_llm_model",
    "chat_llm_api_key",
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
    "api_model_pricing",
    "execution_cors_origins",
}


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
    config_path = os.getenv(
        "API_POOL_DEFAULT_CONFIG",
        os.path.join(os.path.dirname(__file__), "../../config/api_pool.default.json"),
    )
    aux_config_path = os.getenv(
        "AUX_API_POOL_DEFAULT_CONFIG",
        os.path.join(os.path.dirname(__file__), "../../config/aux_api_pool.default.json"),
    )
    api_pool_manager.bootstrap(os.path.abspath(config_path))
    aux_api_pool_manager.bootstrap(os.path.abspath(aux_config_path))
    audit_sink.start()


@app.on_event("shutdown")
async def stop_audit_sink() -> None:
    audit_sink.stop()


def require_admin(request: Request) -> None:
    _ = require_kb_write(request)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode((raw + padding).encode("utf-8"))


def _sign_token(payload: dict) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_part = _b64url_encode(payload_json)
    sig = hmac.new(JWT_SECRET.encode("utf-8"), payload_part.encode("utf-8"), hashlib.sha256).digest()
    return f"{payload_part}.{_b64url_encode(sig)}"


def _verify_token(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 2:
        raise HTTPException(status_code=401, detail="Invalid token format")
    payload_part, sig_part = parts
    expected_sig = hmac.new(JWT_SECRET.encode("utf-8"), payload_part.encode("utf-8"), hashlib.sha256).digest()
    if not hmac.compare_digest(_b64url_encode(expected_sig), sig_part):
        raise HTTPException(status_code=401, detail="Invalid token signature")
    try:
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid token payload") from exc
    if int(payload.get("exp", 0)) < int(time.time()):
        raise HTTPException(status_code=401, detail="Token expired")
    return payload


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
        payload = _verify_token(token)
    except HTTPException as exc:
        request.state.audit_auth_result = "DENY"
        request.state.audit_error_class = "AuthError"
        request.state.audit_error_reason = str(exc.detail)
        raise

    role = str(payload.get("role", ""))
    request.state.auth_claims = payload
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
    return _require_roles(request, {"PLATFORM_ADMIN", "KB_MANAGER"}, "kb:write")


def require_platform_admin(request: Request) -> dict:
    return _require_roles(request, {"PLATFORM_ADMIN"}, "platform:admin")


@app.get("/api/v1/system/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "execution-api"}


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
    celery_app.send_task("app.tasks.summarize_kb", args=[payload], queue="parse_queue", task_id=task_id)
    return {"status": "accepted", "task_id": task_id, "task_type": "KB_SUMMARIZE", "kb_id": kb_id, "trace_id": request.state.trace_id}


@app.post("/api/v1/admin/kbs/{kb_id}/documents/ingest")
async def ingest(kb_id: str, body: ApiIngestRequest, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
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
        "content": body.content,
        "chunk_method": "fixed",
        "chunk_size": body.chunk_size,
        "chunk_overlap": body.chunk_overlap,
        "auto_summary": True,
    }
    kb_store.create_task_record(task_id, request.state.tenant_id, kb_id, doc_id, "DOC_INGEST", "PARSING", filename=filename)
    celery_app.send_task("app.tasks.parse_document", args=[payload], queue="parse_queue", task_id=task_id)
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
    max_file_size_mb = int(upload_settings.get("max_file_size_mb", 20))
    max_file_size_bytes = max_file_size_mb * 1024 * 1024
    accepted: list[dict] = []
    skipped: list[dict] = []
    total_bytes = 0
    ext_set: set[str] = set()
    for file in files:
        if not file.filename:
            skipped.append({"filename": "", "reason": "missing filename"})
            continue
        raw = await file.read()
        total_bytes += len(raw or b"")
        ext_set.add((os.path.splitext(file.filename.lower())[1] or "unknown").lstrip("."))
        if not raw:
            skipped.append({"filename": file.filename, "reason": "empty file"})
            continue
        if len(raw) > max_file_size_bytes:
            skipped.append({"filename": file.filename, "reason": f"file too large (> {max_file_size_mb}MB)"})
            continue
        try:
            text = _extract_text_from_file(file.filename, raw).strip()
        except HTTPException as exc:
            skipped.append({"filename": file.filename, "reason": str(exc.detail)})
            continue
        except Exception as exc:
            skipped.append({"filename": file.filename, "reason": f"parse failed: {exc}"})
            continue
        if not text:
            skipped.append({"filename": file.filename, "reason": "no extractable text"})
            continue
        doc_id = str(uuid.uuid4())
        task_id = None
        if process_mode == "UPLOAD_ONLY":
            kb_store.save_document(
                request.state.tenant_id,
                kb_id,
                doc_id,
                file.filename,
                text,
                chunk_method=chunk_method,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                chunk_delimiter=chunk_delimiter,
            )
            kb_store.set_document_status(request.state.tenant_id, kb_id, doc_id, "UPLOADED_RAW")
        elif process_mode == "UPLOAD_AND_CLEAN":
            cleaned = preprocess_text_for_chunk(text)
            kb_store.save_document(
                request.state.tenant_id,
                kb_id,
                doc_id,
                file.filename,
                cleaned,
                chunk_method=chunk_method,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                chunk_delimiter=chunk_delimiter,
            )
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
                "content": text,
                "chunk_method": chunk_method,
                "chunk_delimiter": chunk_delimiter,
                "chunk_size": chunk_size,
                "chunk_overlap": chunk_overlap,
                "auto_summary": True,
            }
            kb_store.create_task_record(task_id, request.state.tenant_id, kb_id, doc_id, "DOC_INGEST", "PARSING", filename=file.filename)
            celery_app.send_task("app.tasks.parse_document", args=[payload], queue="parse_queue", task_id=task_id)
        accepted.append({"filename": file.filename, "doc_id": doc_id, "task_id": task_id, "process_mode": process_mode})
    request.state.audit_body_summary = (
        f"kind=multipart;file_count={len(files)};total_size_mb={round(total_bytes / (1024 * 1024), 3)};"
        f"ext={','.join(sorted(ext_set)) if ext_set else '-'};process_mode={process_mode};chunk_method={chunk_method};"
        f"chunk_delimiter={('set' if chunk_delimiter else '-')};"
        f"chunk_size={chunk_size};chunk_overlap={chunk_overlap}"
    )
    return {
        "status": "accepted" if accepted else "rejected",
        "kb_id": kb_id,
        "accepted_count": len(accepted),
        "skipped_count": len(skipped),
        "max_file_size_mb": max_file_size_mb,
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
async def get_runtime_config(request: Request, _: dict = Depends(require_platform_admin)) -> Dict:
    data = _load_runtime_config_json()
    safe_items = {k: data.get(k) for k in sorted(RUNTIME_CONFIG_ALLOWED_KEYS) if k in data}
    return {
        "status": "ok",
        "item": safe_items,
        "allowed_keys": sorted(RUNTIME_CONFIG_ALLOWED_KEYS),
        "path": _runtime_config_path(),
        "trace_id": request.state.trace_id,
    }


@app.patch("/api/v1/admin/system/runtime-config")
async def patch_runtime_config(
    body: ApiRuntimeConfigUpdateRequest,
    request: Request,
    _: dict = Depends(require_platform_admin),
) -> Dict:
    updates = body.updates or {}
    if not updates:
        raise HTTPException(status_code=400, detail="updates cannot be empty")
    illegal = [k for k in updates.keys() if k not in RUNTIME_CONFIG_ALLOWED_KEYS]
    if illegal:
        raise HTTPException(status_code=400, detail=f"unsupported keys: {', '.join(sorted(illegal))}")
    current = _load_runtime_config_json()
    for k, v in updates.items():
        current[k] = v
    _save_runtime_config_json(current)
    safe_items = {k: current.get(k) for k in sorted(RUNTIME_CONFIG_ALLOWED_KEYS) if k in current}
    return {"status": "ok", "item": safe_items, "updated_keys": sorted(list(updates.keys())), "trace_id": request.state.trace_id}


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
    celery_app.send_task("app.tasks.commit_chunk_drafts", args=[payload], queue="embed_queue", task_id=task_id)
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
    celery_app.send_task("app.tasks.parse_document", args=[payload], queue="parse_queue", task_id=task_id)
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
    celery_app.send_task("app.tasks.parse_document", args=[payload], queue="parse_queue", task_id=task_id)
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
    celery_app.send_task("app.tasks.summarize_document", args=[payload], queue="parse_queue", task_id=task_id)
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
                celery_app.send_task("app.tasks.parse_document", args=[payload], queue="parse_queue", task_id=task_id)
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
                celery_app.send_task("app.tasks.parse_document", args=[payload], queue="parse_queue", task_id=task_id)
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
                celery_app.send_task("app.tasks.summarize_document", args=[payload], queue="parse_queue", task_id=task_id)
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
                celery_app.send_task("app.tasks.move_document", args=[payload], queue="parse_queue", task_id=task_id)
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
                celery_app.send_task("app.tasks.tag_document", args=[payload], queue="parse_queue", task_id=task_id)
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
                celery_app.send_task("app.tasks.delete_document", args=[payload], queue="parse_queue", task_id=task_id)
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
        chunk_overlap=int(params.get("chunk_overlap") or 50),
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
    record = kb_store.get_task(task_id)
    if not record:
        raise HTTPException(status_code=404, detail="task not found")
    kb_id_for_refresh = str(record.get("kb_id") or "")
    batch_id_for_refresh = str(record.get("batch_id") or "")
    cancelled_task_ids: list[str] = []
    if kb_store.request_cancel_task(task_id):
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
            if not kb_store.request_cancel_task(child_id):
                continue
            cancelled_task_ids.append(child_id)
            try:
                celery_app.control.revoke(child_id, terminate=True)
            except Exception:
                pass
            kb_store.update_task_record(child_id, "CANCELLED", "CANCELLED")
            child = kb_store.get_task(child_id) or {}
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
    events = kb_store.list_task_events(task_id, limit=limit)
    return {"status": "ok", "task_id": task_id, "items": events, "trace_id": request.state.trace_id}


@app.get("/api/v1/tasks/{task_id}")
async def get_task_status(task_id: str, request: Request) -> Dict:
    result = celery_app.AsyncResult(task_id)
    body: Dict = {
        "status": "ok",
        "task_id": task_id,
        "state": result.state,
        "trace_id": request.state.trace_id,
    }
    record = kb_store.get_task(task_id)
    if record:
        body["record"] = record
    if result.successful():
        body["result"] = result.result
    elif result.failed():
        body["error"] = str(result.result)
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
            "trace_id": request.state.trace_id,
        }
    try:
        evidence = search(
            SearchRequest(
                tenant_id=source_tenant_id,
                kb_id=body.kb_id,
                query=body.query,
                top_k=body.top_k,
                doc_ids=body.doc_ids,
            )
        )
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
        "trace_id": request.state.trace_id,
    }


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
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/api-pool")
async def create_or_update_api_pool(
    body: ApiPoolUpsertRequest, request: Request, _: dict = Depends(require_platform_admin)
) -> Dict:
    endpoint = api_pool_manager.upsert_endpoint(ApiEndpoint(**body.model_dump()))
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
    endpoint = api_pool_manager.upsert_endpoint(ApiEndpoint(**body.model_dump()))
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
    config_path = os.getenv(
        "API_POOL_DEFAULT_CONFIG",
        os.path.join(os.path.dirname(__file__), "../../config/api_pool.default.json"),
    )
    snapshot = api_pool_manager.reset_from_file(os.path.abspath(config_path))
    return {
        "status": "ok",
        "default_endpoint_id": snapshot.default_endpoint_id,
        "default_endpoint_ids": snapshot.default_endpoint_ids,
        "count": len(snapshot.endpoints),
        "trace_id": request.state.trace_id,
    }


@app.get("/api/v1/admin/aux-api-pool")
async def list_aux_api_pool(request: Request, task: str | None = None, _: dict = Depends(require_platform_admin)) -> Dict:
    endpoints = aux_api_pool_manager.list_endpoints(request.state.tenant_id, task=task)
    return {
        "status": "ok",
        "tenant_id": request.state.tenant_id,
        "items": [e.model_dump(exclude={"api_key"}) for e in endpoints],
        "trace_id": request.state.trace_id,
    }


@app.post("/api/v1/admin/aux-api-pool")
async def create_or_update_aux_api_pool(
    body: ApiPoolUpsertRequest, request: Request, _: dict = Depends(require_platform_admin)
) -> Dict:
    endpoint = aux_api_pool_manager.upsert_endpoint(ApiEndpoint(**body.model_dump()))
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
    endpoint = aux_api_pool_manager.upsert_endpoint(ApiEndpoint(**body.model_dump()))
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
    config_path = os.getenv(
        "AUX_API_POOL_DEFAULT_CONFIG",
        os.path.join(os.path.dirname(__file__), "../../config/aux_api_pool.default.json"),
    )
    snapshot = aux_api_pool_manager.reset_from_file(os.path.abspath(config_path))
    return {
        "status": "ok",
        "default_endpoint_id": snapshot.default_endpoint_id,
        "default_endpoint_ids": snapshot.default_endpoint_ids,
        "count": len(snapshot.endpoints),
        "trace_id": request.state.trace_id,
    }


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


@app.get("/api/v1/admin/dbviz/overview")
async def dbviz_overview(
    request: Request,
    tenant_scope: str = "current",
    _: dict = Depends(require_platform_admin),
) -> Dict:
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    pg_counts: dict[str, int] = {}
    with kb_store._conn() as conn, conn.cursor() as cur:
        for table in SUPPORTED_DBVIZ_TABLES:
            if include_all_tenants:
                cur.execute(sql.SQL("SELECT COUNT(*)::bigint AS c FROM {}").format(sql.Identifier(table)))
                pg_counts[table] = int((cur.fetchone() or {}).get("c", 0))
            else:
                if table in DBVIZ_NON_TENANT_TABLES:
                    cur.execute(sql.SQL("SELECT COUNT(*)::bigint AS c FROM {}").format(sql.Identifier(table)))
                else:
                    cur.execute(
                        sql.SQL("SELECT COUNT(*)::bigint AS c FROM {} WHERE tenant_id=%s").format(sql.Identifier(table)),
                        (tenant_id,),
                    )
                pg_counts[table] = int((cur.fetchone() or {}).get("c", 0))
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
    include_all_tenants, tenant_id = _parse_tenant_scope(tenant_scope, request.state.tenant_id)
    items: list[dict] = []
    with kb_store._conn() as conn, conn.cursor() as cur:
        for table in sorted(SUPPORTED_DBVIZ_TABLES):
            if include_all_tenants or table in DBVIZ_NON_TENANT_TABLES:
                cur.execute(sql.SQL("SELECT COUNT(*)::bigint AS c FROM {}").format(sql.Identifier(table)))
            else:
                cur.execute(
                    sql.SQL("SELECT COUNT(*)::bigint AS c FROM {} WHERE tenant_id=%s").format(sql.Identifier(table)),
                    (tenant_id,),
                )
            items.append({"table": table, "row_count": int((cur.fetchone() or {}).get("c", 0))})
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
