from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, Dict, Optional

import httpx
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
_memory_rerank_worker_task: asyncio.Task | None = None

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
    title: str = Field(default="新对话", max_length=120)
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
    memory_profile_refresh_new_messages_threshold: Optional[int] = None
    memory_profile_sample_messages: Optional[int] = None
    memory_profile_sample_max_chars: Optional[int] = None
    memory_recent_topics_limit: Optional[int] = None
    memory_recent_events_limit: Optional[int] = None
    memory_budget_total_tokens: Optional[int] = None
    memory_budget_l0_ratio: Optional[float] = None
    memory_budget_l1_ratio: Optional[float] = None
    memory_budget_l2_ratio: Optional[float] = None
    memory_budget_l3_ratio: Optional[float] = None
    memory_budget_l4_ratio: Optional[float] = None
    memory_budget_l5_ratio: Optional[float] = None
    memory_keyword_top_k: Optional[int] = None
    memory_rerank_enabled: Optional[bool] = None
    memory_rerank_batch_size: Optional[int] = None


class MemoryRerankRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=200, ge=1, le=2000)


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
        expected = str(get_setting("execution_admin_token", ""))
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


@app.get("/api/v1/chat/config")
async def get_chat_config_user(request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth)
    base = {
        # 用户态只读配置：可见当前生效的默认 prompt 与检索参数
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


@app.get("/api/v1/chat/kbs")
async def list_accessible_kbs_for_chat_user(request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth)
    base = str(get_setting("execution_api_base_url", "http://execution-api:8090")).rstrip("/")
    admin_username = str(get_setting("execution_admin_username", "admin"))
    admin_password = str(get_setting("execution_admin_password", "admin123"))
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    timeout = httpx.Timeout(20.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            login_resp = await client.post(
                f"{base}/api/v1/admin/auth/login",
                json={"username": admin_username, "password": admin_password},
                headers={
                    "Content-Type": "application/json",
                    "X-Source-Service": "chat-plane",
                    "X-Trace-Id": trace_id,
                },
            )
            login_resp.raise_for_status()
            login_data = login_resp.json()
            access_token = str(login_data.get("access_token") or "").strip()
            if not access_token:
                raise HTTPException(status_code=502, detail="execution admin login returned empty token")

            list_resp = await client.get(
                f"{base}/api/v1/admin/kbs",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Tenant-Id": str(auth["tenant_id"]),
                    "X-Actor-Id": str(auth["actor_id"]),
                    "X-Source-Service": "chat-plane",
                    "X-Trace-Id": trace_id,
                },
            )
            list_resp.raise_for_status()
            data = list_resp.json()
            items = data.get("items") if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            return {"status": "ok", "items": items, "trace_id": trace_id}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"failed to load kb list from execution-api: {exc}") from exc


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


@app.post("/api/v1/admin/chat/memory/rerank")
async def rerank_memory_endpoint(body: MemoryRerankRequest, _: Dict = Depends(require_admin)) -> Dict:
    session = chat_store.get_session(body.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    job = chat_store.enqueue_memory_rerank_job(
        session_id=body.session_id,
        tenant_id=session.tenant_id,
        actor_id=session.actor_id,
        requested_by="admin",
        request_limit=body.limit,
    )
    return {"status": "ok", "item": job}


@app.get("/api/v1/admin/chat/memory/rerank/jobs")
async def list_memory_rerank_jobs(limit: int = 50, _: Dict = Depends(require_admin)) -> Dict:
    items = chat_store.list_memory_rerank_jobs(limit=limit)
    return {"status": "ok", "items": items}


@app.get("/api/v1/admin/chat/memory/rerank/jobs/{job_id}")
async def get_memory_rerank_job(job_id: str, _: Dict = Depends(require_admin)) -> Dict:
    item = chat_store.get_memory_rerank_job(job_id)
    if not item:
        raise HTTPException(status_code=404, detail="job not found")
    return {"status": "ok", "item": item}


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


@app.get("/api/v1/admin/chat/store/status")
async def get_chat_store_status(_: Dict = Depends(require_admin)) -> Dict:
    return {"status": "ok", "item": chat_store.get_store_status()}


@app.post("/api/v1/admin/chat/store/flush")
async def flush_chat_store(_: Dict = Depends(require_admin)) -> Dict:
    result = chat_store.force_flush()
    return result


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"status": "error", "detail": str(exc)})


async def _memory_rerank_worker_loop() -> None:
    while True:
        try:
            job = chat_store.claim_next_memory_rerank_job(worker_id="chat-plane-api")
            if not job:
                await asyncio.sleep(0.8)
                continue
            job_id = str(job.get("job_id") or "")
            session_id = str(job.get("session_id") or "")
            limit = int(job.get("request_limit") or 200)
            try:
                result = await chat_pipeline.rerank_memory_items(session_id=session_id, limit=limit)
                chat_store.complete_memory_rerank_job(
                    job_id,
                    status="SUCCEEDED",
                    result=result if isinstance(result, dict) else {"status": "ok"},
                )
            except Exception as exc:
                chat_store.complete_memory_rerank_job(
                    job_id,
                    status="FAILED",
                    error_message=str(exc),
                )
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(1.0)


@app.on_event("startup")
async def _startup_background_workers() -> None:
    global _memory_rerank_worker_task
    if _memory_rerank_worker_task is None or _memory_rerank_worker_task.done():
        _memory_rerank_worker_task = asyncio.create_task(_memory_rerank_worker_loop())


@app.on_event("shutdown")
async def _shutdown_background_workers() -> None:
    global _memory_rerank_worker_task
    if _memory_rerank_worker_task is not None:
        _memory_rerank_worker_task.cancel()
        try:
            await _memory_rerank_worker_task
        except Exception:
            pass
        _memory_rerank_worker_task = None
