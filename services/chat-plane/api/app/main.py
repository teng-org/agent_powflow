from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from runtime.api_pool import ChatApiEndpoint, chat_api_pool_manager
from runtime.auth import AuthError, decode_access_token
from runtime.config import get_setting, set_overrides
from runtime.memory import (
    get_memory_config,
    memory_config_defaults,
    normalize_memory_config_updates,
)
from runtime.pipeline import canvas_schema, chat_pipeline
from runtime.models import ChatContext, ChatRequest
from runtime.store import chat_store

app = FastAPI(title="agent-powflow-chat-plane", version="0.1.0")

cors_origins = str(
    get_setting(
        "execution_cors_origins",
        "http://localhost:5173,http://localhost:5177,http://localhost:5180",
    )
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in cors_origins if x.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateSessionRequest(BaseModel):
    title: str = Field(default="New Chat", max_length=120)
    kb_id: Optional[str] = None


class RenameSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class ChatConfigPatchRequest(BaseModel):
    chat_system_prompt: Optional[str] = None
    chat_default_top_k: Optional[int] = None
    chat_default_rank_method: Optional[str] = None


class MemoryConfigPatchRequest(BaseModel):
    memory_auto_summary_enabled: Optional[bool] = None
    memory_hierarchical_enabled: Optional[bool] = None
    memory_user_profile_enabled: Optional[bool] = None
    memory_eviction_enabled: Optional[bool] = None
    memory_recent_messages_limit: Optional[int] = None
    memory_context_char_budget: Optional[int] = None
    memory_compress_trigger_messages: Optional[int] = None
    memory_short_summary_max_chars: Optional[int] = None
    memory_long_summary_max_chars: Optional[int] = None
    memory_profile_max_chars: Optional[int] = None
    memory_item_max: Optional[int] = None


class ChatApiPoolUpsertRequest(BaseModel):
    endpoint_id: str
    name: str
    provider: str = "openai_compatible"
    base_url: str
    model: str
    task: str = "chat_llm"
    api_key: str = ""
    tenant_id: str = "*"
    enabled: bool = True
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


FORBIDDEN_SCOPE_KEYS = {
    "tenant_id",
    "tenantId",
    "actor_id",
    "actorId",
    "user_id",
    "userId",
}


def _parse_roles(auth: Dict[str, Any]) -> set[str]:
    roles = auth.get("roles", [])
    if isinstance(roles, list):
        return {str(x).strip() for x in roles if str(x).strip()}
    return set()


def _is_platform_admin(auth: Dict[str, Any]) -> bool:
    return "PLATFORM_ADMIN" in _parse_roles(auth)


def _as_payload_dict(body: Any) -> Dict[str, Any]:
    if body is None:
        return {}
    if isinstance(body, dict):
        return body
    model_dump = getattr(body, "model_dump", None)
    if callable(model_dump):
        data = model_dump()
        if isinstance(data, dict):
            return data
    return {}


def _assert_request_scope(
    request: Request,
    auth: Dict[str, Any],
    body: Any = None,
    *,
    allow_platform_admin_header_scope: bool = False,
) -> None:
    token_tenant = str(auth.get("tenant_id") or "").strip()
    token_actor = str(auth.get("actor_id") or "").strip()

    header_tenant = str(request.headers.get("X-Tenant-Id") or "").strip()
    if header_tenant and header_tenant != token_tenant:
        if not (allow_platform_admin_header_scope and _is_platform_admin(auth)):
            raise HTTPException(status_code=403, detail="tenant scope override forbidden")

    header_actor = str(request.headers.get("X-Actor-Id") or "").strip()
    if header_actor and header_actor != token_actor:
        raise HTTPException(status_code=403, detail="actor scope override forbidden")

    payload = _as_payload_dict(body)
    for key in FORBIDDEN_SCOPE_KEYS:
        if key not in payload:
            continue
        value = str(payload.get(key) or "").strip()
        if not value:
            continue
        if "tenant" in key and value != token_tenant:
            raise HTTPException(status_code=403, detail="tenant scope override forbidden")
        if ("actor" in key or "user" in key) and value != token_actor:
            raise HTTPException(status_code=403, detail="actor scope override forbidden")

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for key in FORBIDDEN_SCOPE_KEYS:
            value = str(metadata.get(key) or "").strip()
            if not value:
                continue
            if "tenant" in key and value != token_tenant:
                raise HTTPException(status_code=403, detail="tenant scope override forbidden")
            if ("actor" in key or "user" in key) and value != token_actor:
                raise HTTPException(status_code=403, detail="actor scope override forbidden")


async def require_auth(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing authorization")
    token = authorization.split(" ", 1)[1].strip()
    secret = str(get_setting("chat_jwt_secret", get_setting("execution_jwt_secret", "change-me-in-production")))
    try:
        claims = decode_access_token(token, secret=secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"tenant_id": claims.tenant_id, "actor_id": claims.sub, "roles": claims.roles}


async def require_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing authorization")
    token = authorization.split(" ", 1)[1].strip()
    secret = str(get_setting("chat_jwt_secret", get_setting("execution_jwt_secret", "change-me-in-production")))
    allow_legacy_admin_token = str(get_setting("chat_allow_legacy_admin_token", "false")).lower() in {"1", "true", "yes", "on"}
    if allow_legacy_admin_token:
        expected = str(get_setting("execution_admin_token", "dev-admin-token"))
        if token == expected:
            return {"tenant_id": "platform", "actor_id": "admin", "roles": ["PLATFORM_ADMIN"]}
    try:
        claims = decode_access_token(token, secret=secret)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    roles = set(claims.roles)
    if not ({"PLATFORM_ADMIN", "TENANT_ADMIN"} & roles):
        raise HTTPException(status_code=403, detail="insufficient role")
    return {"tenant_id": claims.tenant_id, "actor_id": claims.sub, "roles": claims.roles}


@app.get("/api/v1/system/health")
async def health() -> Dict:
    return {"status": "ok", "service": "chat-plane"}


@app.post("/api/v1/chat/sessions")
async def create_session(body: CreateSessionRequest, request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth, body)
    session = chat_store.create_session(
        tenant_id=auth["tenant_id"],
        actor_id=auth["actor_id"],
        title=body.title,
        kb_id=body.kb_id,
    )
    return {"status": "ok", "item": session.model_dump()}


@app.get("/api/v1/chat/sessions")
async def list_sessions(request: Request, limit: int = 100, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth)
    items = chat_store.list_sessions(auth["tenant_id"], auth["actor_id"], limit=limit)
    return {"status": "ok", "items": [x.model_dump() for x in items]}


@app.get("/api/v1/chat/sessions/{session_id}/messages")
async def list_messages(session_id: str, request: Request, limit: int = 200, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth)
    session = chat_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.tenant_id != auth["tenant_id"] or session.actor_id != auth["actor_id"]:
        raise HTTPException(status_code=403, detail="forbidden")
    items = chat_store.list_messages(session_id, limit=limit)
    return {"status": "ok", "items": [x.model_dump() for x in items]}


@app.patch("/api/v1/chat/sessions/{session_id}")
async def rename_session(session_id: str, body: RenameSessionRequest, request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth, body)
    session = chat_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.tenant_id != auth["tenant_id"] or session.actor_id != auth["actor_id"]:
        raise HTTPException(status_code=403, detail="forbidden")
    updated = chat_store.rename_session(session_id, body.title.strip())
    if not updated:
        raise HTTPException(status_code=404, detail="session not found")
    return {"status": "ok", "item": updated.model_dump()}


@app.delete("/api/v1/chat/sessions/{session_id}")
async def delete_session(session_id: str, request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth)
    session = chat_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.tenant_id != auth["tenant_id"] or session.actor_id != auth["actor_id"]:
        raise HTTPException(status_code=403, detail="forbidden")
    deleted = chat_store.delete_session(session_id)
    return {"status": "ok", "deleted": bool(deleted), "session_id": session_id}


@app.post("/api/v1/chat/sessions/{session_id}/messages/stream")
async def stream_chat(session_id: str, body: ChatRequest, request: Request, auth: Dict = Depends(require_auth)):
    _assert_request_scope(request, auth, body)
    session = chat_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.tenant_id != auth["tenant_id"] or session.actor_id != auth["actor_id"]:
        raise HTTPException(status_code=403, detail="forbidden")

    run = chat_store.create_run(session_id, auth["tenant_id"], auth["actor_id"], mode="CHAT")
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    if not body.top_k or body.top_k <= 0:
        body.top_k = int(get_setting("chat_default_top_k", 6))
    if not body.rank_method:
        body.rank_method = str(get_setting("chat_default_rank_method", "vector"))

    ctx = ChatContext(
        tenant_id=auth["tenant_id"],
        actor_id=auth["actor_id"],
        trace_id=trace_id,
        source_service="chat-plane",
        session=session,
        run=run,
        request=body,
    )

    async def event_generator():
        yield "event: run\ndata: " + json.dumps({"run_id": run.run_id, "session_id": session_id}, ensure_ascii=False) + "\n\n"
        try:
            async for evt in chat_pipeline.run_stream(ctx):
                yield f"event: {evt.get('event', 'message')}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
        except Exception as exc:
            chat_store.complete_run(run.run_id, status="FAILED", error_message=str(exc))
            payload = {"event": "error", "run_id": run.run_id, "message": str(exc)}
            yield "event: error\ndata: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/api/v1/chat/runs/{run_id}/cancel")
async def cancel_run(run_id: str, request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth)
    run = chat_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.tenant_id != auth["tenant_id"] or run.actor_id != auth["actor_id"]:
        raise HTTPException(status_code=403, detail="forbidden")
    ok = chat_store.cancel_run(run_id)
    return {"status": "ok", "run_id": run_id, "cancel_requested": bool(ok)}


@app.get("/api/v1/chat/runs/{run_id}")
async def get_run(run_id: str, request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth)
    run = chat_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="run not found")
    if run.tenant_id != auth["tenant_id"] or run.actor_id != auth["actor_id"]:
        raise HTTPException(status_code=403, detail="forbidden")
    return {"status": "ok", "item": run.model_dump()}


@app.get("/api/v1/chat/agent-canvas/schema")
async def get_agent_canvas_schema(request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth)
    return {"status": "ok", "item": canvas_schema(), "tenant_id": auth["tenant_id"]}


@app.get("/api/v1/chat/auth/context")
async def get_auth_context(request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth, allow_platform_admin_header_scope=True)
    return {
        "status": "ok",
        "item": {
            "tenant_id": auth["tenant_id"],
            "actor_id": auth["actor_id"],
            "roles": auth.get("roles", []),
        },
    }


@app.get("/api/v1/admin/chat/config")
async def get_chat_config(_: Dict = Depends(require_admin)) -> Dict:
    base = {
        "chat_system_prompt": str(
            get_setting(
                "chat_system_prompt",
                "你是企业知识库问答助手。优先依据给定证据回答；若证据不足，明确说明并给出下一步建议。",
            )
        ),
        "chat_default_top_k": int(get_setting("chat_default_top_k", 6)),
        "chat_default_rank_method": str(get_setting("chat_default_rank_method", "vector")),
    }
    return {"status": "ok", "item": base}


@app.patch("/api/v1/admin/chat/config")
async def patch_chat_config(body: ChatConfigPatchRequest, _: Dict = Depends(require_admin)) -> Dict:
    updates = body.model_dump(exclude_none=True)
    set_overrides(updates)
    return {"status": "ok", "item": updates}


@app.get("/api/v1/admin/chat/memory-config")
async def get_memory_config_endpoint(_: Dict = Depends(require_admin)) -> Dict:
    defaults = memory_config_defaults()
    cfg = get_memory_config().as_dict()
    return {"status": "ok", "defaults": defaults, "item": cfg}


@app.patch("/api/v1/admin/chat/memory-config")
async def patch_memory_config_endpoint(body: MemoryConfigPatchRequest, _: Dict = Depends(require_admin)) -> Dict:
    updates = normalize_memory_config_updates(body.model_dump(exclude_none=True))
    set_overrides(updates)
    return {"status": "ok", "item": get_memory_config().as_dict(), "updates": updates}


@app.get("/api/v1/admin/chat/api-pool")
async def list_chat_api_pool(task: str | None = None, _: Dict = Depends(require_admin)) -> Dict:
    items = chat_api_pool_manager.list_endpoints(task=task)
    snapshot = chat_api_pool_manager.snapshot()
    return {
        "status": "ok",
        "default_endpoint_id": snapshot.default_endpoint_id,
        "default_endpoint_ids": snapshot.default_endpoint_ids,
        "items": [x.__dict__ for x in items],
    }


@app.post("/api/v1/admin/chat/api-pool")
async def upsert_chat_api_pool(body: ChatApiPoolUpsertRequest, _: Dict = Depends(require_admin)) -> Dict:
    endpoint = chat_api_pool_manager.upsert_endpoint(ChatApiEndpoint(**body.model_dump()))
    return {"status": "ok", "item": endpoint.__dict__}


@app.delete("/api/v1/admin/chat/api-pool/{endpoint_id}")
async def delete_chat_api_pool(endpoint_id: str, _: Dict = Depends(require_admin)) -> Dict:
    deleted = chat_api_pool_manager.delete_endpoint(endpoint_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="endpoint not found")
    return {"status": "ok", "deleted": endpoint_id}


@app.post("/api/v1/admin/chat/api-pool/{endpoint_id}/set-default")
async def set_default_chat_api_pool(
    endpoint_id: str, task: str | None = None, _: Dict = Depends(require_admin)
) -> Dict:
    try:
        default_id = chat_api_pool_manager.set_default(endpoint_id, task=task)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "default_endpoint_id": default_id, "task": task or "auto"}


@app.post("/api/v1/admin/chat/api-pool/reload-default")
async def reload_default_chat_api_pool(_: Dict = Depends(require_admin)) -> Dict:
    config_path = os.getenv(
        "CHAT_API_POOL_DEFAULT_CONFIG",
        os.path.join(os.path.dirname(__file__), "../../config/chat_api_pool.default.json"),
    )
    snapshot = chat_api_pool_manager.reset_from_file(os.path.abspath(config_path))
    return {
        "status": "ok",
        "default_endpoint_id": snapshot.default_endpoint_id,
        "default_endpoint_ids": snapshot.default_endpoint_ids,
        "count": len(snapshot.endpoints),
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"status": "error", "detail": str(exc)})
