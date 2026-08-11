from __future__ import annotations

import asyncio
import csv
import io
import hmac
import json
import os
import time
import uuid
from typing import Any, Dict, Optional

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from runtime.api_pool import ChatApiAlertRule, ChatApiEndpoint, chat_api_pool_manager
from runtime.auth import AuthError, decode_access_token
from runtime.config import get_setting, set_overrides
from runtime.memory import (
    get_memory_config,
    memory_config_defaults,
    normalize_memory_config_updates,
)
from runtime.pipeline import canvas_schema, chat_pipeline
from runtime.llm_client import llm_client
from runtime.models import ChatContext, ChatRequest
from runtime.store import chat_store

app = FastAPI(title="agent-powflow-chat-plane", version="0.1.0")
_memory_rerank_worker_task: asyncio.Task | None = None


def _decode_control_access_token(token: str, *, require_tenant: bool) -> Any:
    return decode_access_token(
        token,
        secret=str(get_setting("control_plane_jwt_secret", "change-me-control-in-production")),
        key_id=str(get_setting("control_plane_jwt_key_id", "control-dev-primary")),
        previous_keys=str(get_setting("control_plane_jwt_previous_keys", "")),
        previous_expires_at=str(get_setting("control_plane_jwt_previous_keys_expires_at", "")),
        legacy_accept_until=str(get_setting("control_plane_jwt_legacy_accept_until", "")),
        issuer="agentpowflow-control",
        audience="control-plane",
        require_tenant=require_tenant,
    )


def _sse_event(event_name: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False)
    lines = [f"event: {event_name}"]
    lines.extend(f"data: {line}" for line in data.splitlines() or [""])
    return "\n".join(lines) + "\n\n"

cors_origins = str(
    get_setting(
        "execution_cors_origins",
        "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:5177,http://localhost:5180,http://127.0.0.1:5173,http://127.0.0.1:5174,http://127.0.0.1:5175,http://127.0.0.1:5177,http://127.0.0.1:5180",
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


class InternalAgentInvokeRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    actor_id: str = Field(min_length=1, max_length=128)
    agent_snapshot: Dict[str, Any]
    input: Any
    conversation_id: Optional[str] = None


def _published_agent_citations(items: list[Dict[str, Any]], limit: int = 8) -> list[Dict[str, Any]]:
    citations: list[Dict[str, Any]] = []
    for item in (items or [])[: max(0, min(int(limit), 20))]:
        filename = str(
            item.get("filename")
            or item.get("document_name")
            or item.get("doc_name")
            or item.get("source")
            or item.get("doc_id")
            or "-"
        ).strip() or "-"
        text = " ".join(str(item.get("text") or item.get("chunk_text") or "").split())
        score = item.get("score")
        citations.append(
            {
                "index": len(citations) + 1,
                "doc_id": str(item.get("doc_id") or ""),
                "chunk_id": str(item.get("chunk_id") or ""),
                "chunk_index": item.get("chunk_index"),
                "filename": filename,
                "score": score if isinstance(score, (int, float)) else None,
                "text_preview": text[:240],
            }
        )
    return citations


def _resolve_internal_agent_session(
    *, tenant_id: str, actor_id: str, conversation_id: Optional[str], title: str, kb_id: Optional[str]
):
    requested_id = str(conversation_id or "").strip()
    if not requested_id:
        return chat_store.create_session(
            tenant_id=tenant_id,
            actor_id=actor_id,
            title=title,
            kb_id=kb_id,
        )
    session = chat_store.get_session(requested_id)
    # Do not disclose whether an ID belongs to another tenant or actor.
    if session is None or session.tenant_id != tenant_id or session.actor_id != actor_id:
        raise HTTPException(status_code=404, detail="conversation not found")
    return session


def _require_internal_token(supplied: Optional[str]) -> None:
    expected = str(get_setting("control_plane_internal_token", ""))
    if not supplied or not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="invalid internal token")


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
    api_instance_id: str = ""
    api_instance_name: str = ""
    api_instance_remark: str = ""
    tenant_id: str = "*"
    enabled: bool = True
    frozen: bool = False
    priority: int = 100
    weight: int = 100
    max_concurrency: int = 8
    timeout_seconds: int = 120
    retry_max: int = 2
    circuit_fail_threshold: int = 5
    circuit_open_seconds: int = 30
    input_per_1k: float = 0.0
    output_per_1k: float = 0.0


class ChatApiPoolProbeRequest(BaseModel):
    max_tokens: int = Field(default=8, ge=1, le=64)
    timeout_seconds: int = Field(default=30, ge=5, le=60)


CHAT_API_CONSUMER_ENDPOINTS = [
    {
        "task": "chat_llm",
        "pool": "chat",
        "label": "对话主回复 LLM",
        "capability": "llm",
        "description": "用户对话最终回复、Agent 主推理和工具调用决策。",
    },
    {
        "task": "chat_aux",
        "pool": "chat",
        "label": "对话辅助 LLM",
        "capability": "llm",
        "description": "查询改写、工具结果整理、短文本辅助判断等中间步骤。",
    },
    {
        "task": "chat_summary",
        "pool": "chat",
        "label": "对话记忆总结 LLM",
        "capability": "llm",
        "description": "会话总结、长期记忆压缩和上下文整理。",
    },
    {
        "task": "chat_embedding",
        "pool": "chat",
        "label": "对话记忆 Embedding",
        "capability": "embedding",
        "description": "会话记忆、查询改写相关向量化和语义匹配。",
    },
]


class ChatApiAlertRuleUpsertRequest(BaseModel):
    rule_id: str = ""
    name: str
    enabled: bool = True
    metric: str
    scope: str = "global"
    tenant_id: str = "*"
    task: str = ""
    endpoint_id: str = ""
    model: str = ""
    threshold: float = 0.0
    window_days: int = 1
    comparison: str = "gte"
    severity: str = "warning"
    cooldown_seconds: int = 300


class ChatApiAlertAckRequest(BaseModel):
    actor: str = "admin"


def _safe_chat_api_endpoint(endpoint: ChatApiEndpoint) -> Dict[str, Any]:
    item = endpoint.__dict__.copy()
    item.pop("api_key", None)
    return item


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


def _rows_to_csv(rows: list[dict]) -> str:
    if not rows:
        return ""
    keys: list[str] = []
    seen = set()
    for row in rows:
        for k in row.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                keys.append(sk)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in keys})
    return buf.getvalue()


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
    try:
        claims = _decode_control_access_token(token, require_tenant=True)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"tenant_id": claims.tenant_id, "actor_id": claims.sub, "roles": claims.roles}


async def require_admin(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing authorization")
    token = authorization.split(" ", 1)[1].strip()
    allow_legacy_admin_token = str(get_setting("chat_allow_legacy_admin_token", "false")).lower() in {"1", "true", "yes", "on"}
    if allow_legacy_admin_token:
        expected = str(get_setting("execution_admin_token", ""))
        if token == expected:
            return {"tenant_id": "platform", "actor_id": "admin", "roles": ["PLATFORM_ADMIN"]}
    try:
        claims = _decode_control_access_token(token, require_tenant=False)
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


@app.get("/api/v1/chat/models")
async def list_chat_models(auth: Dict = Depends(require_auth)) -> Dict:
    items = chat_api_pool_manager.list_candidates(
        task="chat_llm", tenant_id=auth["tenant_id"], include_unhealthy=True
    )
    return {
        "status": "ok",
        "items": [
            {
                "endpoint_id": item.endpoint_id,
                "name": item.api_instance_name or item.name or item.model,
                "provider": item.provider,
                "model": item.model,
                "is_default": item.endpoint_id
                == (chat_api_pool_manager.get_default("chat_llm", auth["tenant_id"]) or item).endpoint_id,
            }
            for item in items
        ],
    }


@app.post("/api/v1/internal/agents/invoke")
async def invoke_agent_internal(
    body: InternalAgentInvokeRequest,
    x_internal_token: Optional[str] = Header(default=None, alias="X-Internal-Token"),
) -> Dict:
    _require_internal_token(x_internal_token)
    snapshot = body.agent_snapshot or {}
    model_config = dict(snapshot.get("model_config") or {})
    retrieval = dict(model_config.get("retrieval") or {})
    kb_ids = list(snapshot.get("knowledge_base_ids") or [])
    raw_input = body.input
    if isinstance(raw_input, dict) and raw_input.get("message") is not None:
        message = str(raw_input.get("message") or "").strip()
    elif isinstance(raw_input, str):
        message = raw_input.strip()
    else:
        message = json.dumps(raw_input, ensure_ascii=False)
    if not message:
        raise HTTPException(status_code=400, detail="agent input message is empty")

    started = time.perf_counter()
    kb_id = str(kb_ids[0]) if retrieval.get("enabled", True) and kb_ids else None
    session = _resolve_internal_agent_session(
        tenant_id=body.tenant_id,
        actor_id=body.actor_id,
        conversation_id=body.conversation_id,
        title=str(snapshot.get("name") or "Agent API")[:120],
        kb_id=kb_id,
    )
    run = chat_store.create_run(session.session_id, body.tenant_id, body.actor_id, mode="AGENT_API")
    request = ChatRequest(
        message=message,
        use_kb=bool(kb_id),
        use_agent=False,
        kb_id=kb_id,
        top_k=int(retrieval.get("top_k") or 6),
        rank_method=str(retrieval.get("rank_method") or "vector"),
        metadata={"source": "published-agent", "agent_id": str(snapshot.get("agent_id") or "")},
        agent_config={
            "agent_id": str(snapshot.get("agent_id") or ""),
            "instructions": str(snapshot.get("instructions") or ""),
            **model_config,
        },
    )
    ctx = ChatContext(
        tenant_id=body.tenant_id,
        actor_id=body.actor_id,
        trace_id=str(uuid.uuid4()),
        source_service="control-plane",
        session=session,
        run=run,
        request=request,
    )
    output: list[str] = []
    event_count = 0
    first_token_ms: float | None = None
    retrieval_ready_ms: float | None = None
    generation_started_ms: float | None = None
    generation_completed_ms: float | None = None
    stage_ms: dict[str, float] = {}
    async for event in chat_pipeline.run_stream(ctx, cancel_on_disconnect=False):
        event_count += 1
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if event.get("event") == "token":
            if first_token_ms is None:
                first_token_ms = elapsed_ms
            output.append(str(event.get("delta") or ""))
        elif event.get("event") == "stage":
            stage = str(event.get("stage") or "").strip()
            if stage:
                stage_ms[stage.lower()] = round(elapsed_ms, 2)
            if stage in {"RETRIEVAL_DONE", "RETRIEVAL_SKIPPED"}:
                retrieval_ready_ms = elapsed_ms
            elif stage == "GENERATION_STREAMING":
                generation_started_ms = elapsed_ms
            elif stage == "PERSISTED":
                generation_completed_ms = elapsed_ms
        elif event.get("event") == "error":
            raise HTTPException(status_code=502, detail="agent runtime failed")
    total_ms = (time.perf_counter() - started) * 1000.0
    metrics = {
        "total_ms": round(total_ms, 2),
        "retrieval_ready_ms": round(retrieval_ready_ms, 2) if retrieval_ready_ms is not None else None,
        "generation_started_ms": round(generation_started_ms, 2) if generation_started_ms is not None else None,
        "first_token_ms": round(first_token_ms, 2) if first_token_ms is not None else None,
        "generation_ms": round(max(0.0, (generation_completed_ms or total_ms) - generation_started_ms), 2) if generation_started_ms is not None else None,
        "finalize_ms": round(max(0.0, total_ms - generation_completed_ms), 2) if generation_completed_ms is not None else None,
        "retrieved_chunks": len(ctx.retrieval_items or []),
        "event_count": event_count,
        "stage_ms": stage_ms,
    }
    return {
        "status": "completed",
        "run_id": run.run_id,
        "conversation_id": session.session_id,
        "output": "".join(output),
        "citations": _published_agent_citations(ctx.retrieval_items or []),
        "metrics": metrics,
    }


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
        yield _sse_event("run", {"run_id": run.run_id, "session_id": session_id})
        try:
            async for evt in chat_pipeline.run_stream(ctx, cancel_on_disconnect=True):
                yield _sse_event(str(evt.get("event", "message")), evt)
        except Exception as exc:
            chat_store.complete_run(run.run_id, status="FAILED", error_message=str(exc))
            payload = {"event": "error", "run_id": run.run_id, "message": str(exc)}
            yield _sse_event("error", payload)

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
    interrupted = await chat_pipeline.cancel_active_run(run_id) if ok else False
    return {
        "status": "ok",
        "run_id": run_id,
        "cancel_requested": bool(ok),
        "execution_interrupted": bool(interrupted),
    }


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
        "chat_agent_enabled": str(get_setting("chat_agent_enabled", False)).strip().lower() in {"1", "true", "yes", "on"},
        "chat_agent_tool_planning_enabled": str(get_setting("chat_agent_tool_planning_enabled", False)).strip().lower() in {"1", "true", "yes", "on"},
        "chat_agent_aux_summary_tool_enabled": str(get_setting("chat_agent_aux_summary_tool_enabled", False)).strip().lower() in {"1", "true", "yes", "on"},
        "chat_agent_tool_max_rounds": int(get_setting("chat_agent_tool_max_rounds", 3)),
    }
    return {"status": "ok", "item": base}


@app.get("/api/v1/chat/kbs")
async def list_accessible_kbs_for_chat_user(request: Request, auth: Dict = Depends(require_auth)) -> Dict:
    _assert_request_scope(request, auth)
    base = str(get_setting("control_plane_internal_url", "http://control-plane:8080")).rstrip("/")
    internal_token = str(get_setting("control_plane_internal_token", ""))
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    timeout = httpx.Timeout(20.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            list_resp = await client.get(
                f"{base}/api/v1/internal/chat/knowledge-bases",
                params={"tenant_id": str(auth["tenant_id"]), "owner_user_id": str(auth["actor_id"])},
                headers={
                    "X-Internal-Token": internal_token,
                    "X-Source-Service": "chat-plane",
                    "X-Trace-Id": trace_id,
                },
            )
            list_resp.raise_for_status()
            data = list_resp.json() if list_resp.content else {}
            items = data.get("items") if isinstance(data, dict) else []
            if not isinstance(items, list):
                items = []
            return {"status": "ok", "items": items, "trace_id": trace_id}
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"failed to load kb list from control-plane: status={exc.response.status_code}",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"failed to load kb list from control-plane: {exc}") from exc


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
        "items": [_safe_chat_api_endpoint(x) for x in items],
    }


@app.get("/api/v1/admin/chat/api-pool/consumer-endpoints")
async def list_chat_api_consumer_endpoints(_: Dict = Depends(require_admin)) -> Dict:
    return {"status": "ok", "items": CHAT_API_CONSUMER_ENDPOINTS}


@app.post("/api/v1/admin/chat/api-pool")
async def upsert_chat_api_pool(body: ChatApiPoolUpsertRequest, _: Dict = Depends(require_admin)) -> Dict:
    try:
        endpoint = chat_api_pool_manager.upsert_endpoint(ChatApiEndpoint(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "ok", "item": _safe_chat_api_endpoint(endpoint)}


@app.post("/api/v1/admin/chat/api-pool/{endpoint_id}/probe")
async def probe_chat_api_pool(
    endpoint_id: str,
    body: ChatApiPoolProbeRequest,
    _: Dict = Depends(require_admin),
) -> Dict:
    try:
        result = await llm_client.probe_endpoint(
            endpoint_id,
            max_tokens=body.max_tokens,
            total_timeout_seconds=body.timeout_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "provider probe failed",
                "error_type": type(exc).__name__,
                "upstream_status": int(getattr(exc, "probe_status_code", 0) or 0),
                "total_ms": float(getattr(exc, "probe_total_ms", 0.0) or 0.0),
            },
        ) from exc
    return {"status": "ok", "item": result}


@app.delete("/api/v1/admin/chat/api-pool/{endpoint_id}")
async def delete_chat_api_pool(endpoint_id: str, _: Dict = Depends(require_admin)) -> Dict:
    deleted = chat_api_pool_manager.delete_endpoint(endpoint_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="endpoint not found")
    return {"status": "ok", "deleted": endpoint_id}


@app.post("/api/v1/admin/chat/api-pool/{endpoint_id}/freeze")
async def freeze_chat_api_pool(endpoint_id: str, _: Dict = Depends(require_admin)) -> Dict:
    endpoint = chat_api_pool_manager.set_frozen(endpoint_id, True)
    if not endpoint:
        raise HTTPException(status_code=404, detail="endpoint not found")
    return {"status": "ok", "item": _safe_chat_api_endpoint(endpoint)}


@app.post("/api/v1/admin/chat/api-pool/{endpoint_id}/unfreeze")
async def unfreeze_chat_api_pool(endpoint_id: str, _: Dict = Depends(require_admin)) -> Dict:
    endpoint = chat_api_pool_manager.set_frozen(endpoint_id, False)
    if not endpoint:
        raise HTTPException(status_code=404, detail="endpoint not found")
    return {"status": "ok", "item": _safe_chat_api_endpoint(endpoint)}


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
    raise HTTPException(status_code=410, detail="Chat API pool file defaults are disabled; use API management UI/database")


@app.get("/api/v1/admin/chat/api-pool/health")
async def chat_api_pool_health(task: str | None = None, _: Dict = Depends(require_admin)) -> Dict:
    items = chat_api_pool_manager.endpoint_health(task=task)
    return {"status": "ok", "items": items}


@app.get("/api/v1/admin/chat/api-pool/usage/daily")
async def chat_api_pool_usage_daily(
    day_from: str = "",
    day_to: str = "",
    tenant_id: str = "",
    task: str = "",
    endpoint_id: str = "",
    model: str = "",
    limit: int = 200,
    _: Dict = Depends(require_admin),
) -> Dict:
    items = chat_api_pool_manager.usage_daily(
        day_from=day_from,
        day_to=day_to,
        tenant_id=tenant_id,
        task=task,
        endpoint_id=endpoint_id,
        model=model,
        limit=limit,
    )
    return {"status": "ok", "items": items}


@app.get("/api/v1/admin/chat/api-pool/usage/grouped")
async def chat_api_pool_usage_grouped(
    group_by: str = "tenant,task,model,endpoint_id",
    day_from: str = "",
    day_to: str = "",
    tenant_id: str = "",
    task: str = "",
    endpoint_id: str = "",
    model: str = "",
    sort_by: str = "request_count",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 50,
    _: Dict = Depends(require_admin),
) -> Dict:
    item = chat_api_pool_manager.usage_grouped(
        group_by=group_by,
        day_from=day_from,
        day_to=day_to,
        tenant_id=tenant_id,
        task=task,
        endpoint_id=endpoint_id,
        model=model,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )
    return {"status": "ok", "item": item}


@app.get("/api/v1/admin/chat/api-pool/usage/trends")
async def chat_api_pool_usage_trends(
    day_from: str = "",
    day_to: str = "",
    tenant_id: str = "",
    task: str = "",
    endpoint_id: str = "",
    model: str = "",
    limit_days: int = 60,
    _: Dict = Depends(require_admin),
) -> Dict:
    items = chat_api_pool_manager.usage_trends(
        day_from=day_from,
        day_to=day_to,
        tenant_id=tenant_id,
        task=task,
        endpoint_id=endpoint_id,
        model=model,
        limit_days=limit_days,
    )
    return {"status": "ok", "items": items}


@app.get("/api/v1/admin/chat/api-pool/alerts/rules")
async def list_chat_api_alert_rules(_: Dict = Depends(require_admin)) -> Dict:
    return {"status": "ok", "items": chat_api_pool_manager.list_alert_rules()}


@app.get("/api/v1/admin/chat/api-pool/alerts/rules/stats")
async def list_chat_api_alert_rule_stats(limit: int = 200, _: Dict = Depends(require_admin)) -> Dict:
    return {"status": "ok", "items": chat_api_pool_manager.alert_rule_stats(limit=limit)}


@app.post("/api/v1/admin/chat/api-pool/alerts/rules")
async def upsert_chat_api_alert_rule(body: ChatApiAlertRuleUpsertRequest, _: Dict = Depends(require_admin)) -> Dict:
    rid = str(body.rule_id or "").strip() or str(uuid.uuid4())
    item = chat_api_pool_manager.upsert_alert_rule(
        ChatApiAlertRule(
            rule_id=rid,
            name=body.name,
            enabled=body.enabled,
            metric=body.metric,
            scope=body.scope,
            tenant_id=body.tenant_id,
            task=body.task,
            endpoint_id=body.endpoint_id,
            model=body.model,
            threshold=body.threshold,
            window_days=body.window_days,
            comparison=body.comparison,
            severity=body.severity,
            cooldown_seconds=body.cooldown_seconds,
        )
    )
    return {"status": "ok", "item": item}


@app.delete("/api/v1/admin/chat/api-pool/alerts/rules/{rule_id}")
async def delete_chat_api_alert_rule(rule_id: str, _: Dict = Depends(require_admin)) -> Dict:
    ok = chat_api_pool_manager.delete_alert_rule(rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="rule not found")
    return {"status": "ok", "deleted": rule_id}


@app.get("/api/v1/admin/chat/api-pool/alerts/events")
async def list_chat_api_alert_events(
    limit: int = 200,
    page: int = 1,
    page_size: int = 50,
    rule_id: str = "",
    severity: str = "",
    acked: str = "",
    tenant_id: str = "",
    day_from: str = "",
    day_to: str = "",
    _: Dict = Depends(require_admin),
) -> Dict:
    acked_value = None
    if str(acked).strip().lower() in {"true", "1", "yes"}:
        acked_value = True
    elif str(acked).strip().lower() in {"false", "0", "no"}:
        acked_value = False
    if page == 1 and page_size == 50 and not any([rule_id, severity, acked, tenant_id, day_from, day_to]):
        return {"status": "ok", "items": chat_api_pool_manager.list_alert_events(limit=limit)}
    item = chat_api_pool_manager.list_alert_events_paged(
        page=page,
        page_size=page_size,
        rule_id=rule_id,
        severity=severity,
        acked=acked_value,
        tenant_id=tenant_id,
        day_from=day_from,
        day_to=day_to,
    )
    return {"status": "ok", "item": item}


@app.post("/api/v1/admin/chat/api-pool/alerts/evaluate")
async def evaluate_chat_api_alerts(_: Dict = Depends(require_admin)) -> Dict:
    result = chat_api_pool_manager.evaluate_alerts()
    return {"status": "ok", "item": result}


@app.get("/api/v1/admin/chat/api-pool/overview")
async def chat_api_pool_overview(_: Dict = Depends(require_admin)) -> Dict:
    return {"status": "ok", "item": chat_api_pool_manager.overview()}


@app.get("/api/v1/admin/chat/api-pool/insights")
async def chat_api_pool_insights(
    day_from: str = "",
    day_to: str = "",
    limit: int = 10,
    _: Dict = Depends(require_admin),
) -> Dict:
    item = chat_api_pool_manager.insights(day_from=day_from, day_to=day_to, limit=limit)
    return {"status": "ok", "item": item}


@app.get("/api/v1/admin/chat/api-pool/dashboard")
async def chat_api_pool_dashboard(
    day_from: str = "",
    day_to: str = "",
    tenant_id: str = "",
    task: str = "",
    endpoint_id: str = "",
    model: str = "",
    _: Dict = Depends(require_admin),
) -> Dict:
    item = chat_api_pool_manager.dashboard_pack(
        day_from=day_from,
        day_to=day_to,
        tenant_id=tenant_id,
        task=task,
        endpoint_id=endpoint_id,
        model=model,
    )
    return {"status": "ok", "item": item}


@app.get("/api/v1/admin/chat/api-pool/logs")
async def list_chat_api_call_logs(
    limit: int = 200,
    tenant_id: str = "",
    task: str = "",
    endpoint_id: str = "",
    ok: str = "",
    _: Dict = Depends(require_admin),
) -> Dict:
    ok_value = None
    if str(ok).strip().lower() in {"true", "1", "yes"}:
        ok_value = True
    elif str(ok).strip().lower() in {"false", "0", "no"}:
        ok_value = False
    items = chat_api_pool_manager.list_call_logs(
        limit=limit,
        tenant_id=tenant_id,
        task=task,
        endpoint_id=endpoint_id,
        ok=ok_value,
    )
    return {"status": "ok", "items": items}


@app.get("/api/v1/admin/chat/api-pool/logs/grouped")
async def list_chat_api_call_logs_grouped(
    group_by: str = "tenant_id,task,endpoint_id,model",
    tenant_id: str = "",
    task: str = "",
    endpoint_id: str = "",
    ok: str = "",
    limit: int = 200,
    sort_by: str = "request_count",
    sort_dir: str = "desc",
    _: Dict = Depends(require_admin),
) -> Dict:
    ok_value = None
    if str(ok).strip().lower() in {"true", "1", "yes"}:
        ok_value = True
    elif str(ok).strip().lower() in {"false", "0", "no"}:
        ok_value = False
    items = chat_api_pool_manager.call_logs_grouped(
        group_by=group_by,
        tenant_id=tenant_id,
        task=task,
        endpoint_id=endpoint_id,
        ok=ok_value,
        limit=limit,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )
    return {"status": "ok", "items": items}


@app.get("/api/v1/admin/chat/api-pool/export/usage")
async def export_chat_api_usage(
    fmt: str = "json",
    day_from: str = "",
    day_to: str = "",
    tenant_id: str = "",
    task: str = "",
    endpoint_id: str = "",
    model: str = "",
    limit: int = 1000,
    _: Dict = Depends(require_admin),
):
    rows = chat_api_pool_manager.usage_daily(
        day_from=day_from,
        day_to=day_to,
        tenant_id=tenant_id,
        task=task,
        endpoint_id=endpoint_id,
        model=model,
        limit=limit,
    )
    if str(fmt).lower() == "csv":
        content = _rows_to_csv(rows)
        return PlainTextResponse(
            content,
            headers={
                "Content-Disposition": "attachment; filename=chat_api_usage.csv",
                "Content-Type": "text/csv; charset=utf-8",
            },
        )
    return {"status": "ok", "items": rows}


@app.get("/api/v1/admin/chat/api-pool/export/logs")
async def export_chat_api_logs(
    fmt: str = "json",
    tenant_id: str = "",
    task: str = "",
    endpoint_id: str = "",
    ok: str = "",
    limit: int = 1000,
    _: Dict = Depends(require_admin),
):
    ok_value = None
    if str(ok).strip().lower() in {"true", "1", "yes"}:
        ok_value = True
    elif str(ok).strip().lower() in {"false", "0", "no"}:
        ok_value = False
    rows = chat_api_pool_manager.list_call_logs(
        limit=limit,
        tenant_id=tenant_id,
        task=task,
        endpoint_id=endpoint_id,
        ok=ok_value,
    )
    if str(fmt).lower() == "csv":
        content = _rows_to_csv(rows)
        return PlainTextResponse(
            content,
            headers={
                "Content-Disposition": "attachment; filename=chat_api_logs.csv",
                "Content-Type": "text/csv; charset=utf-8",
            },
        )
    return {"status": "ok", "items": rows}


@app.post("/api/v1/admin/chat/api-pool/alerts/events/{event_id}/ack")
async def ack_chat_api_alert_event(
    event_id: str,
    body: ChatApiAlertAckRequest,
    _: Dict = Depends(require_admin),
) -> Dict:
    ok = chat_api_pool_manager.ack_alert_event(event_id, actor=body.actor)
    if not ok:
        raise HTTPException(status_code=404, detail="event not found")
    return {"status": "ok", "acked": event_id}


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
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        _memory_rerank_worker_task = None
    await llm_client.aclose()
