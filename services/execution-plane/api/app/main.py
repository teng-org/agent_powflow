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
from models.api_pool import ApiEndpoint, api_pool_manager
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
ADMIN_TOKEN = os.getenv("EXECUTION_ADMIN_TOKEN", "dev-admin-token")
ADMIN_USERNAME = os.getenv("EXECUTION_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("EXECUTION_ADMIN_PASSWORD", "admin123")
ADMIN_ROLE = os.getenv("EXECUTION_ADMIN_ROLE", "PLATFORM_ADMIN")
JWT_SECRET = os.getenv("EXECUTION_JWT_SECRET", ADMIN_TOKEN)
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


class ApiUploadSettingsRequest(BaseModel):
    max_file_size_mb: int = Field(default=20, ge=1, le=200)


class ApiChunkRunRequest(BaseModel):
    chunk_method: str = Field(default="fixed")
    chunk_delimiter: str | None = None
    chunk_size: int = Field(default=500, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=500)


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
    api_pool_manager.bootstrap(os.path.abspath(config_path))
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
    items = kb_store.list_kbs(request.state.tenant_id)
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


@app.post("/api/v1/admin/kbs/{kb_id}/documents/ingest")
async def ingest(kb_id: str, body: ApiIngestRequest, request: Request, _: dict = Depends(require_kb_write)) -> Dict:
    kb_store.ensure_kb(request.state.tenant_id, kb_id)
    doc_id = body.doc_id or str(uuid.uuid4())
    filename = body.filename or f"{doc_id}.txt"
    task_id = str(uuid.uuid4())
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


@app.get("/api/v1/admin/kbs/{kb_id}/documents/{doc_id}/chunks")
async def list_chunks(kb_id: str, doc_id: str, request: Request, limit: int = 300, _: dict = Depends(require_kb_read)) -> Dict:
    drafts = kb_store.list_chunk_drafts(request.state.tenant_id, kb_id, doc_id)
    draft_map = {item["chunk_id"]: item for item in drafts}
    chunks = kb_store.list_chunks(request.state.tenant_id, kb_id, doc_id, limit=limit)
    for chunk in chunks:
        if chunk.get("chunk_id") in draft_map:
            chunk["draft"] = draft_map[chunk["chunk_id"]]
    return {"status": "ok", "doc_id": doc_id, "items": chunks, "drafts": drafts, "trace_id": request.state.trace_id}


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
    exists = kb_store.request_cancel_task(task_id)
    if not exists:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        celery_app.control.revoke(task_id, terminate=True)
    except Exception:
        pass
    kb_store.update_task_record(task_id, "CANCELLING", "CANCELLING")
    return {"status": "ok", "task_id": task_id, "state": "CANCELLING", "trace_id": request.state.trace_id}


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
    evidence = search(
        SearchRequest(
            tenant_id=request.state.tenant_id,
            kb_id=body.kb_id,
            query=body.query,
            top_k=body.top_k,
            doc_ids=body.doc_ids,
        )
    )
    return {"status": "ok", "kb_id": body.kb_id, "evidence": evidence, "trace_id": request.state.trace_id}


@app.post("/api/v1/agent/run")
async def run_agent(body: ApiAgentRunRequest, request: Request) -> Dict:
    result = agent_runtime.run(
        AgentRunRequest(
            tenant_id=request.state.tenant_id,
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
                if table in {"system_settings"}:
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
            if include_all_tenants or table == "system_settings":
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
    if not include_all_tenants and table != "system_settings":
        where_parts.append(sql.SQL("tenant_id = %s"))
        params.append(tenant_id)
    if tenant_id_filter and table != "system_settings":
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
    return {
        "status": "ok",
        "collection": collection_name,
        "item": {
            "id": p.id,
            "payload": p.payload or {},
            "vector_dim": len(vector_values),
            "vector_preview": vector_values[:cap],
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
