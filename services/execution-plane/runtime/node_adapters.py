from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from runtime.provider_idempotency import canonical_hash, provider_invocation_store, stable_invocation_key
from runtime.safe_http import SafeHttpExecutor


@dataclass
class NodeExecutionContext:
    payload: dict
    state: dict[str, Any]
    provider_mode: str
    should_cancel: Callable[[], bool] | None = None
    invocation_store: Any = None
    gateway: Any = None
    retrieval_search: Callable | None = None
    kb_access_resolver: Callable[[str, str], str | None] | None = None
    http_executor: Any = None
    secret_resolver: Callable[[str, str], Any] | None = None


@dataclass
class NodeExecutionResult:
    state_updates: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    citations: list[dict] = field(default_factory=list)
    provider: dict | str | None = None
    provider_cache_hit: bool = False
    provider_called: bool = False
    tool_called: bool = False
    tool_cache_hit: bool = False


class NodeAdapter(Protocol):
    def execute(self, node: dict, context: NodeExecutionContext) -> NodeExecutionResult: ...


class NoopAdapter:
    def execute(self, node: dict, context: NodeExecutionContext) -> NodeExecutionResult:
        return NodeExecutionResult()


class LlmGenerateAdapter:
    def execute(self, node: dict, context: NodeExecutionContext) -> NodeExecutionResult:
        config = _config(node)
        input_text = str(context.state.get("answer") or context.state.get("text") or "").strip()
        if context.provider_mode == "api_strict":
            response, cache_hit = _execute_idempotent_llm(node, config, input_text, context)
            answer = str(response.get("text") or "").strip()
            if not answer:
                raise RuntimeError("provider returned an empty answer")
            provider = response.get("endpoint") or {}
            return NodeExecutionResult(
                state_updates={"answer": answer, "text": answer},
                details={"provider_cache_hit": cache_hit},
                provider=provider,
                provider_cache_hit=cache_hit,
                provider_called=not cache_hit,
            )
        delay_ms = max(0, min(int(config.get("mock_delay_ms") or 0), 5000))
        if delay_ms:
            _interruptible_sleep(delay_ms, context.should_cancel)
        if bool(config.get("mock_fail")):
            raise RuntimeError("mock workflow failure requested")
        answer = f"[mock:{node.get('id')}] {input_text}"
        return NodeExecutionResult(
            state_updates={"answer": answer, "text": answer}, provider="local-mock", provider_called=True
        )


class SkillCallAdapter:
    def execute(self, node: dict, context: NodeExecutionContext) -> NodeExecutionResult:
        config = _config(node)
        definition = config.get("definition") if isinstance(config.get("definition"), dict) else {}
        if not definition or not str(config.get("skill_id") or "").strip() or int(config.get("version") or 0) < 1:
            raise RuntimeError("skill.call requires a published skill snapshot")
        skill_config = {
            "system_prompt": str(definition.get("instructions") or "").strip(),
            "prompt": str(config.get("prompt") or "{input}"),
            "pool": str(definition.get("model") or "main"),
            "temperature": definition.get("temperature", 0.2),
            "max_tokens": definition.get("max_tokens", 800),
        }
        input_text = str(context.state.get("answer") or context.state.get("text") or "").strip()
        if context.provider_mode == "api_strict":
            response, cache_hit = _execute_idempotent_llm(node, skill_config, input_text, context)
            answer = str(response.get("text") or "").strip()
            if not answer:
                raise RuntimeError("skill provider returned an empty answer")
            return NodeExecutionResult(state_updates={"answer": answer, "text": answer}, details={"skill_id": config["skill_id"], "skill_version": config["version"], "provider_cache_hit": cache_hit}, provider=response.get("endpoint") or {}, provider_called=not cache_hit, provider_cache_hit=cache_hit)
        answer = f"[mock:skill:{config.get('skill_name') or config.get('skill_id')}] {input_text}"
        return NodeExecutionResult(state_updates={"answer": answer, "text": answer}, details={"skill_id": config["skill_id"], "skill_version": config["version"]}, provider="local-mock", provider_called=True)


class McpToolAdapter:
    def execute(self, node: dict, context: NodeExecutionContext) -> NodeExecutionResult:
        config = _config(node)
        endpoint = str(config.get("endpoint_url") or "").strip()
        tool_name = str(config.get("tool_name") or "").strip()
        if not endpoint or not tool_name or not str(config.get("connection_id") or "").strip():
            raise RuntimeError("tool.mcp requires a pinned connection and tool")
        tenant_id = str(context.payload.get("tenant_id") or "").strip(); run_id = str(context.payload.get("run_id") or "").strip(); checksum = str(context.payload.get("workflow_checksum") or "").strip(); node_id = str(node.get("id") or "").strip()
        arguments = _resolve_value(config.get("arguments") or {}, context.state)
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "MCP-Protocol-Version": "2025-11-25"}
        trusted = ["mcp-protocol-version"]
        auth = config.get("auth") if isinstance(config.get("auth"), dict) else {}
        if auth and str(auth.get("type") or "NONE").upper() != "NONE":
            resolver = context.secret_resolver
            if resolver is None:
                from runtime.workflow_secrets import workflow_secret_store
                resolver = workflow_secret_store.resolve
            resolved = resolver(tenant_id, str(auth.get("secret_ref") or ""))
            header = "Authorization" if str(auth.get("type")).upper() == "BEARER" else "X-Api-Key"
            headers[header] = ("Bearer " if header == "Authorization" else "") + resolved.value
            trusted.append(header.lower())
        executor = context.http_executor or SafeHttpExecutor()
        initialize = executor.execute({"method":"POST","url":endpoint,"headers":headers,"trusted_header_names":trusted,"timeout_seconds":config.get("timeout_seconds") or 30,"body":{"jsonrpc":"2.0","id":"init","method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"Agent Powflow","version":"1.0"}}}}, context.should_cancel)
        session_id = str(initialize.get("mcp_session_id") or "")
        if session_id:
            headers["MCP-Session-Id"] = session_id; trusted.append("mcp-session-id")
        request_body = {"jsonrpc":"2.0","id":"call","method":"tools/call","params":{"name":tool_name,"arguments":arguments}}
        store = context.invocation_store or provider_invocation_store
        key = stable_invocation_key(tenant_id=tenant_id, run_id=run_id, workflow_checksum=checksum, node_id=node_id, operation=f"mcp.{tool_name}")
        claim = store.claim(invocation_key=key, request_hash=canonical_hash({"endpoint":endpoint,"tool":tool_name,"arguments":arguments}), tenant_id=tenant_id, run_id=run_id, workflow_checksum=checksum, node_id=node_id, operation=f"mcp.{tool_name}")
        cache_hit = claim.cached_response is not None
        if cache_hit: result = claim.cached_response
        else:
            response = executor.execute({"method":"POST","url":endpoint,"headers":headers,"trusted_header_names":trusted,"timeout_seconds":config.get("timeout_seconds") or 30,"body":request_body}, context.should_cancel)
            result = _mcp_json(response.get("body"))
            if isinstance(result, dict) and result.get("error"): raise RuntimeError(f"MCP tool failed: {result['error']}")
            store.succeed(claim, result)
        payload = result.get("result", result) if isinstance(result, dict) else result
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return NodeExecutionResult(state_updates={str(config.get("output_field") or "tool_response"):payload,"text":text}, details={"connection_id":config["connection_id"],"tool_name":tool_name,"tool_cache_hit":cache_hit}, tool_called=not cache_hit, tool_cache_hit=cache_hit)


class RetrievalSearchAdapter:
    def execute(self, node: dict, context: NodeExecutionContext) -> NodeExecutionResult:
        config = _config(node)
        tenant_id = str(context.payload.get("tenant_id") or "").strip()
        kb_id = str(_resolve_value(config.get("kb_id"), context.state) or context.state.get("kb_id") or "").strip()
        if not tenant_id or not kb_id:
            raise RuntimeError("retrieval.search requires tenant_id and config.kb_id")
        query_field = str(config.get("query_field") or "answer").strip()
        query = str(_lookup(context.state, query_field) or context.state.get("text") or "").strip()
        if not query:
            raise RuntimeError("retrieval.search query is empty")
        top_k = max(1, min(int(config.get("top_k") or 5), 20))
        rank_method = str(config.get("rank_method") or "hybrid").strip().lower()
        candidate_k = max(top_k, min(int(config.get("candidate_k") or max(top_k * 4, 20)), 200))
        max_chunks_per_doc = max(1, min(int(config.get("max_chunks_per_doc") or 3), 20))
        context_window = max(0, min(int(config.get("context_window") if config.get("context_window") is not None else 1), 2))
        rerank_enabled = bool(config.get("rerank_enabled", False))
        rerank_k = max(top_k, min(int(config.get("rerank_k") or 30), 100))
        rerank_required = bool(config.get("rerank_required", False))
        resolver = context.kb_access_resolver
        search_fn = context.retrieval_search
        if resolver is None or search_fn is None:
            from retrieval.service import search_with_meta
            from retrieval.store import kb_store
            resolver = resolver or kb_store.resolve_kb_access_tenant
            search_fn = search_fn or search_with_meta
        owner_tenant = resolver(tenant_id, kb_id)
        if not owner_tenant:
            raise RuntimeError("knowledge base is missing or not accessible to the run tenant")
        from retrieval.schemas import SearchRequest
        doc_ids = config.get("doc_ids") if isinstance(config.get("doc_ids"), list) else None
        result = search_fn(SearchRequest(
            tenant_id=owner_tenant,
            kb_id=kb_id,
            query=query,
            top_k=top_k,
            final_k=top_k,
            candidate_k=candidate_k,
            max_chunks_per_doc=max_chunks_per_doc,
            context_window=context_window,
            rerank_enabled=rerank_enabled,
            rerank_k=rerank_k,
            rerank_required=rerank_required,
            rank_method=rank_method,
            doc_ids=doc_ids,
        ))
        if isinstance(result, dict):
            evidence = result.get("evidence") or []
            retrieval_meta = result.get("retrieval") or result.get("meta") or {}
        else:
            evidence = result
            retrieval_meta = {"requested_rank_method": rank_method, "effective_rank_method": "legacy"}
        citations = [_citation(item, kb_id) for item in evidence]
        output_field = str(config.get("output_field") or "evidence").strip() or "evidence"
        return NodeExecutionResult(
            state_updates={output_field: evidence, "citations": citations},
            citations=citations,
            details={
                "kb_id": kb_id,
                "result_count": len(evidence),
                "query_field": query_field,
                "rank_method": retrieval_meta.get("effective_rank_method") or rank_method,
                "retrieval": retrieval_meta,
            },
        )


class HttpToolAdapter:
    def execute(self, node: dict, context: NodeExecutionContext) -> NodeExecutionResult:
        config = _config(node)
        request = {
            "method": str(config.get("method") or "GET").upper(),
            "url": _resolve_template(str(config.get("url") or ""), context.state),
            "headers": _resolve_value(config.get("headers") or {}, context.state),
            "query": _resolve_value(config.get("query") or {}, context.state),
            "timeout_seconds": config.get("timeout_seconds") or 10,
        }
        if "body" in config:
            request["body"] = _resolve_value(config["body"], context.state)
        store = context.invocation_store or provider_invocation_store
        tenant_id = str(context.payload.get("tenant_id") or "").strip()
        run_id = str(context.payload.get("run_id") or "").strip()
        checksum = str(context.payload.get("workflow_checksum") or "").strip()
        node_id = str(node.get("id") or "").strip()
        if not all((tenant_id, run_id, checksum, node_id)):
            raise RuntimeError("tool.http requires tenant_id, run_id, workflow_checksum, and node id")
        request_for_hash = dict(request)
        auth = config.get("auth") if isinstance(config.get("auth"), dict) else None
        if auth:
            reference = str(auth.get("secret_ref") or "").strip()
            header = str(auth.get("header") or "Authorization").strip()
            if header.lower() not in {"authorization", "x-api-key"}:
                raise RuntimeError("tool.http auth.header must be Authorization or X-Api-Key")
            resolver = context.secret_resolver
            if resolver is None:
                from runtime.workflow_secrets import workflow_secret_store
                resolver = workflow_secret_store.resolve
            resolved = resolver(tenant_id, reference)
            prefix = str(auth.get("prefix") if auth.get("prefix") is not None else ("Bearer " if header.lower() == "authorization" else ""))
            if "\r" in prefix or "\n" in prefix or len(prefix) > 64:
                raise RuntimeError("tool.http auth.prefix is invalid")
            request["headers"] = dict(request["headers"])
            request["headers"][header] = f"{prefix}{resolved.value}"
            request["trusted_header_names"] = [header]
            request_for_hash = dict(request_for_hash)
            request_for_hash["auth"] = {
                "secret_id": resolved.secret_id,
                "fingerprint": resolved.fingerprint,
                "version": resolved.version,
                "header": header.lower(),
                "prefix": prefix,
            }
        key = stable_invocation_key(
            tenant_id=tenant_id, run_id=run_id, workflow_checksum=checksum, node_id=node_id, operation="tool.http"
        )
        if request["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            request["headers"] = dict(request["headers"])
            request["headers"]["Idempotency-Key"] = key
            request_for_hash["headers"] = dict(request_for_hash["headers"])
            request_for_hash["headers"]["Idempotency-Key"] = key
        claim = store.claim(
            invocation_key=key,
            request_hash=canonical_hash(request_for_hash),
            tenant_id=tenant_id,
            run_id=run_id,
            workflow_checksum=checksum,
            node_id=node_id,
            operation="tool.http",
        )
        cache_hit = claim.cached_response is not None
        if cache_hit:
            response = claim.cached_response
        else:
            executor = context.http_executor or SafeHttpExecutor()
            _raise_if_cancelled(context.should_cancel)
            try:
                response = executor.execute(request, context.should_cancel)
                store.succeed(claim, response)
            except Exception as exc:
                store.fail(claim, str(exc))
                raise
        output_field = str(config.get("output_field") or "tool_response").strip() or "tool_response"
        body = response.get("body") if isinstance(response, dict) else response
        text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, sort_keys=True)
        return NodeExecutionResult(
            state_updates={output_field: response, "text": text},
            details={"status_code": response.get("status_code"), "tool_cache_hit": cache_hit},
            tool_called=not cache_hit,
            tool_cache_hit=cache_hit,
        )


class NodeAdapterRegistry:
    def __init__(self) -> None:
        noop = NoopAdapter()
        self._adapters: dict[str, NodeAdapter] = {
            "start": noop,
            "condition.if": noop,
            "parallel.fork": noop,
            "parallel.join": noop,
            "llm.generate": LlmGenerateAdapter(),
            "retrieval.search": RetrievalSearchAdapter(),
            "skill.call": SkillCallAdapter(),
            "tool.http": HttpToolAdapter(),
            "tool.mcp": McpToolAdapter(),
            "end": noop,
        }

    @property
    def supported_types(self) -> frozenset[str]:
        return frozenset(self._adapters)

    def execute(self, node: dict, context: NodeExecutionContext) -> NodeExecutionResult:
        node_type = str(node.get("type") or "")
        adapter = self._adapters.get(node_type)
        if adapter is None:
            raise RuntimeError(f"unsupported workflow node type: {node_type}")
        return adapter.execute(node, context)


def _execute_idempotent_llm(node: dict, config: dict, input_text: str, context: NodeExecutionContext):
    store = context.invocation_store or provider_invocation_store
    gateway = context.gateway
    if gateway is None:
        from models.ai_gateway import ai_gateway
        gateway = ai_gateway
    payload = context.payload
    tenant_id = str(payload.get("tenant_id") or "").strip()
    run_id = str(payload.get("run_id") or "").strip()
    checksum = str(payload.get("workflow_checksum") or "").strip()
    node_id = str(node.get("id") or "").strip()
    if not all((tenant_id, run_id, checksum, node_id)):
        raise RuntimeError("api_strict requires tenant_id, run_id, workflow_checksum, and llm node id")
    system_prompt = str(config.get("system_prompt") or "You are a helpful assistant.").strip()
    prompt_template = str(config.get("prompt") or "{input}")
    request = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt_template.replace("{input}", input_text).replace("{text}", input_text)},
        ],
        "pool": str(config.get("pool") or "main"),
        "task": str(config.get("task") or "llm"),
        "temperature": max(0.0, min(float(config.get("temperature", 0.2)), 2.0)),
        "max_tokens": max(1, min(int(config.get("max_tokens", 600)), 32768)),
    }
    key = stable_invocation_key(
        tenant_id=tenant_id, run_id=run_id, workflow_checksum=checksum, node_id=node_id, operation="llm.chat_completion"
    )
    claim = store.claim(
        invocation_key=key,
        request_hash=canonical_hash(request),
        tenant_id=tenant_id,
        run_id=run_id,
        workflow_checksum=checksum,
        node_id=node_id,
        operation="llm.chat_completion",
    )
    if claim.cached_response is not None:
        return claim.cached_response, True
    _raise_if_cancelled(context.should_cancel)
    try:
        response = gateway.chat_completion(
            tenant_id,
            request["messages"],
            pool=request["pool"],
            task=request["task"],
            strict=True,
            temperature=request["temperature"],
            max_tokens=request["max_tokens"],
            idempotency_key=key,
            should_cancel=context.should_cancel,
        )
        store.succeed(claim, response)
        return response, False
    except Exception as exc:
        store.fail(claim, str(exc))
        raise


def _citation(item: dict, kb_id: str) -> dict:
    return {
        "kb_id": kb_id,
        "doc_id": str(item.get("doc_id") or ""),
        "chunk_id": str(item.get("chunk_id") or ""),
        "chunk_index": int(item.get("chunk_index") or 0),
        "source": str(item.get("source") or item.get("filename") or "unknown"),
        "score": float(item.get("score") or 0.0),
        "text": str(item.get("text") or ""),
    }


def _config(node: dict) -> dict:
    return node.get("config") if isinstance(node.get("config"), dict) else {}


def _lookup(state: dict[str, Any], path: str) -> Any:
    current: Any = state
    for part in str(path).split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _resolve_template(value: str, state: dict[str, Any]) -> str:
    if value.startswith("${") and value.endswith("}"):
        resolved = _lookup(state, value[2:-1])
        return "" if resolved is None else str(resolved)
    return value


def _resolve_value(value: Any, state: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _resolve_template(value, state)
    if isinstance(value, list):
        return [_resolve_value(item, state) for item in value]
    if isinstance(value, dict):
        return {str(key): _resolve_value(item, state) for key, item in value.items()}
    return value


def _mcp_json(body: Any) -> Any:
    if not isinstance(body, str):
        return body
    for line in reversed(body.splitlines()):
        if line.startswith("data:"):
            try: return json.loads(line[5:].strip())
            except json.JSONDecodeError: continue
    try: return json.loads(body)
    except json.JSONDecodeError: raise RuntimeError("MCP server returned an unsupported response")


def _raise_if_cancelled(should_cancel) -> None:
    if should_cancel and should_cancel():
        raise RuntimeError("TASK_CANCELLED")


def _interruptible_sleep(delay_ms: int, should_cancel) -> None:
    import time
    deadline = time.monotonic() + delay_ms / 1000.0
    while True:
        _raise_if_cancelled(should_cancel)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


node_adapter_registry = NodeAdapterRegistry()
