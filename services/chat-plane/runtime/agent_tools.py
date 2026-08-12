from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import socket
from typing import Any, Dict, List
from urllib.parse import urlparse

import httpx
from jsonschema import Draft202012Validator

from .llm_client import llm_client
from .models import AgentJavaScriptToolConfig, AgentMcpToolConfig, AgentRuntimeConfig, AgentSkillConfig, ChatContext


def tool_alias(kind: str, identity: str, name: str) -> str:
    digest = hashlib.sha256(f"{kind}:{identity}:{name}".encode("utf-8")).hexdigest()[:10]
    raw = f"{kind}_{digest}_{name}".lower()
    return re.sub(r"[^a-z0-9_-]+", "_", raw)[:64].strip("_") or "powflow_tool"


def _schema(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) and value.get("type") else {"type": "object", "properties": {}}


def build_tool_catalog(config: AgentRuntimeConfig) -> tuple[List[Dict[str, Any]], Dict[str, tuple[str, Any]]]:
    tools: List[Dict[str, Any]] = []
    lookup: Dict[str, tuple[str, Any]] = {}
    for skill in config.skills:
        alias = tool_alias("skill", skill.skill_id, skill.name or skill.skill_id)
        tools.append({"type": "function", "function": {
            "name": alias,
            "description": skill.description or f"执行 Skill：{skill.name or skill.skill_id}",
            "parameters": _schema(skill.input_schema),
            "strict": True,
        }})
        lookup[alias] = ("skill", skill)
    for tool in config.mcp_tools:
        alias = tool_alias("mcp", tool.connection_id, tool.tool_name)
        tools.append({"type": "function", "function": {
            "name": alias,
            "description": tool.description or f"调用 MCP 工具：{tool.title or tool.tool_name}",
            "parameters": _schema(tool.input_schema),
            "strict": True,
        }})
        lookup[alias] = ("mcp", tool)
    for tool in config.javascript_tools:
        definition = tool.definition or {}
        alias = tool_alias("js", tool.tool_id, tool.name or tool.tool_id)
        tools.append({"type":"function","function":{
            "name":alias,"description":tool.description or f"执行 JavaScript 工具：{tool.name or tool.tool_id}",
            "parameters":_schema(definition.get("input_schema")),"strict":True,
        }})
        lookup[alias] = ("javascript", tool)
    return tools, lookup


def _validate(arguments: Dict[str, Any], schema: Dict[str, Any]) -> None:
    errors = sorted(Draft202012Validator(_schema(schema)).iter_errors(arguments), key=lambda item: list(item.path))
    if errors:
        path = ".".join(str(item) for item in errors[0].path) or "$"
        raise ValueError(f"工具参数不符合 Schema：{path} {errors[0].message}")


def _is_write_tool(tool: AgentMcpToolConfig) -> bool:
    annotations = tool.annotations or {}
    if annotations.get("readOnlyHint") is True:
        return False
    if annotations.get("destructiveHint") is True:
        return True
    if annotations.get("readOnlyHint") is False:
        return True
    # Unannotated tools are not trusted as read-only. Only well-known query verbs
    # may run under the default policy; everything else requires explicit opt-in.
    return not bool(re.match(
        r"^(get|list|search|read|fetch|find|lookup|describe|query|check)(?:_|$)",
        tool.tool_name.lower(),
    ))


async def _validate_mcp_destination(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("MCP 运行时仅允许公开 HTTPS Streamable HTTP 端点")
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("MCP 端点域名无法解析") from exc
    addresses = {item[4][0] for item in infos}
    if not addresses:
        raise ValueError("MCP 端点没有可用地址")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("MCP 端点不得解析到本机、内网或保留地址")


def _mcp_payload_text(result: Any, limit: int) -> str:
    payload = result.get("result", result) if isinstance(result, dict) else result
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + f"...（已截断 {len(text) - limit} 字符）"


async def _post_mcp(endpoint: str, body: Dict[str, Any], headers: Dict[str, str], timeout: int) -> tuple[Dict[str, Any], str]:
    await _validate_mcp_destination(endpoint)
    async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(endpoint, headers=headers, json=body)
        response.raise_for_status()
        raw = response.text.strip()
        if raw.startswith("{"):
            data = response.json()
        else:
            lines = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
            data = json.loads(lines[-1]) if lines else {}
        return data if isinstance(data, dict) else {}, response.headers.get("MCP-Session-Id", "")


async def execute_mcp(tool: AgentMcpToolConfig, arguments: Dict[str, Any], ctx: ChatContext) -> str:
    policy = ctx.request.agent_config.tool_policy
    _validate(arguments, tool.input_schema)
    if _is_write_tool(tool) and not policy.allow_write_tools:
        raise PermissionError("该 MCP 工具可能修改外部系统，当前 Agent 未允许写操作")
    headers = {
        "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-11-25", "Mcp-Method": "initialize",
    }
    auth = tool.auth or {}
    if str(auth.get("type") or "NONE").upper() != "NONE":
        reference = str(auth.get("secret_ref") or "").strip()
        if not reference:
            raise PermissionError("MCP 鉴权连接缺少密钥引用")
        base = os.getenv("EXECUTION_API_BASE_URL", "http://execution-api:8090").rstrip("/")
        token = os.getenv("CONTROL_PLANE_INTERNAL_TOKEN", "")
        async with httpx.AsyncClient(timeout=httpx.Timeout(policy.timeout_seconds)) as client:
            response = await client.post(
                f"{base}/api/v1/internal/workflow-secrets/resolve",
                headers={"X-Internal-Token": token},
                json={"tenant_id": ctx.tenant_id, "secret_ref": reference},
            )
            response.raise_for_status()
            secret = str(response.json().get("value") or "")
        if str(auth.get("type")).upper() == "BEARER": headers["Authorization"] = f"Bearer {secret}"
        else: headers["X-Api-Key"] = secret
    init, session = await _post_mcp(tool.endpoint_url, {
        "jsonrpc": "2.0", "id": "init", "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "Powflow Agent", "version": "1.0"}},
    }, headers, policy.timeout_seconds)
    if init.get("error"): raise RuntimeError("MCP 初始化失败")
    if session: headers["MCP-Session-Id"] = session
    headers["Mcp-Method"] = "notifications/initialized"
    await _post_mcp(tool.endpoint_url, {
        "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
    }, headers, policy.timeout_seconds)
    headers["Mcp-Method"] = "tools/call"; headers["Mcp-Name"] = tool.tool_name
    result, _ = await _post_mcp(tool.endpoint_url, {
        "jsonrpc": "2.0", "id": "call", "method": "tools/call",
        "params": {"name": tool.tool_name, "arguments": arguments},
    }, headers, policy.timeout_seconds)
    if result.get("error"): raise RuntimeError("MCP 工具执行失败")
    return _mcp_payload_text(result, policy.result_max_chars)


async def execute_skill(skill: AgentSkillConfig, arguments: Dict[str, Any], ctx: ChatContext) -> str:
    _validate(arguments, skill.input_schema)
    result = await llm_client.complete_text([
        {"role": "system", "content": skill.instructions},
        {"role": "user", "content": json.dumps(arguments, ensure_ascii=False)},
    ], tenant_id=ctx.tenant_id, max_tokens=skill.max_tokens, temperature=0)
    return result[: ctx.request.agent_config.tool_policy.result_max_chars]


async def execute_javascript(tool: AgentJavaScriptToolConfig, arguments: Dict[str, Any], ctx: ChatContext) -> str:
    definition = tool.definition or {}
    _validate(arguments, definition.get("input_schema") or {"type":"object"})
    if str(definition.get("risk_level") or "COMPUTE").upper() == "WRITE" and not ctx.request.agent_config.tool_policy.allow_write_tools:
        raise PermissionError("该 JavaScript 工具被标记为写操作，当前 Agent 未允许写工具")
    base = os.getenv("EXECUTION_API_BASE_URL", "http://execution-api:8090").rstrip("/")
    token = os.getenv("CONTROL_PLANE_INTERNAL_TOKEN", "")
    async with httpx.AsyncClient(timeout=httpx.Timeout(ctx.request.agent_config.tool_policy.timeout_seconds + 2)) as client:
        response = await client.post(f"{base}/api/v1/internal/javascript-tools/execute", headers={"X-Internal-Token":token}, json={
            "tenant_id":ctx.tenant_id,"actor_id":ctx.actor_id,"tool_id":tool.tool_id,"tool_version":tool.version,
            "definition":definition,"arguments":arguments,
        })
        response.raise_for_status()
        result = response.json().get("output")
    text = json.dumps(result, ensure_ascii=False, sort_keys=True)
    return text[:ctx.request.agent_config.tool_policy.result_max_chars]


async def execute_tool(kind: str, config: Any, arguments: Dict[str, Any], ctx: ChatContext) -> str:
    timeout = ctx.request.agent_config.tool_policy.timeout_seconds
    async with asyncio.timeout(timeout + 2):
        if kind == "skill": return await execute_skill(config, arguments, ctx)
        if kind == "javascript": return await execute_javascript(config, arguments, ctx)
        return await execute_mcp(config, arguments, ctx)
