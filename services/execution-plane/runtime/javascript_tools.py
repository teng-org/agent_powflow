from __future__ import annotations

import json
from typing import Any

import quickjs
from jsonschema import Draft202012Validator


class JavaScriptToolError(RuntimeError):
    pass


def _validate(value: Any, schema: dict, label: str) -> None:
    errors = sorted(Draft202012Validator(schema or {"type": "object"}).iter_errors(value), key=lambda item: list(item.path))
    if errors:
        path = ".".join(map(str, errors[0].path)) or "$"
        raise JavaScriptToolError(f"{label} 不符合 Schema：{path} {errors[0].message}")


def execute_javascript_tool(definition: dict, arguments: dict) -> dict:
    code = str(definition.get("code") or "").strip()
    if not code:
        raise JavaScriptToolError("工具代码为空")
    input_schema = definition.get("input_schema") or {"type": "object"}
    output_schema = definition.get("output_schema") or {}
    _validate(arguments, input_schema, "输入")
    timeout_ms = max(10, min(int(definition.get("timeout_ms") or 1000), 5000))
    memory_mb = max(4, min(int(definition.get("memory_mb") or 16), 64))
    ctx = quickjs.Context()
    ctx.set_memory_limit(memory_mb * 1024 * 1024)
    ctx.set_max_stack_size(512 * 1024)
    ctx.set_time_limit(timeout_ms / 1000)
    wrapper = f"""
    'use strict';
    const console = undefined, fetch = undefined, XMLHttpRequest = undefined,
          WebSocket = undefined, process = undefined, require = undefined,
          Deno = undefined, Function = undefined;
    const __tool = (input) => {{ {code}\n }};
    JSON.stringify(__tool(JSON.parse({json.dumps(json.dumps(arguments, ensure_ascii=False))})));
    """
    try:
        raw = ctx.eval(wrapper)
        result = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        message = str(exc).splitlines()[0][:300]
        raise JavaScriptToolError(f"JavaScript 执行失败：{message}") from exc
    if output_schema:
        _validate(result, output_schema, "输出")
    encoded = json.dumps(result, ensure_ascii=False)
    if len(encoded.encode("utf-8")) > 256 * 1024:
        raise JavaScriptToolError("工具输出超过 256KB 限制")
    return {"output": result, "duration_limit_ms": timeout_ms, "memory_limit_mb": memory_mb}
