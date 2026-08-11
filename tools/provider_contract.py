#!/usr/bin/env python3
"""Opt-in, bounded-cost contract probe for OpenAI-compatible providers.

The probe is deliberately isolated from normal smoke tests. It never reads an
API key from a file, never prints response content or vectors, and refuses all
network calls until the paid-call approval flag and a positive cost ceiling are
both supplied.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import math
import os
import socket
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib import error, parse, request


RATE_LIMIT_HEADERS = (
    "retry-after",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens",
)
REQUEST_ID_HEADERS = ("x-request-id", "request-id", "x-trace-id", "trace-id")


class ContractError(RuntimeError):
    """A safe, user-facing contract failure without provider response bodies."""


@dataclass(frozen=True)
class ContractConfig:
    provider: str
    base_url: str
    chat_model: str
    embedding_model: str
    api_key_env: str
    input_per_million_usd: float
    output_per_million_usd: float
    embedding_per_million_usd: float
    max_cost_usd: float
    max_output_tokens: int = 8
    require_rate_limit_headers: bool = False
    timeout_seconds: float = 30.0
    allow_paid_provider_calls: bool = False


def _positive_number(value: float, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ContractError(f"{name} must be a positive finite number")
    return number


def validate_config(config: ContractConfig, environ: Mapping[str, str]) -> str:
    if not config.allow_paid_provider_calls:
        raise ContractError(
            "external provider calls are disabled; pass --allow-paid-provider-calls "
            "only after approving provider usage and cost"
        )
    if not config.provider.strip():
        raise ContractError("provider must not be empty")
    if not config.chat_model.strip() or not config.embedding_model.strip():
        raise ContractError("chat_model and embedding_model must not be empty")
    _positive_number(config.max_cost_usd, "max_cost_usd")
    if min(
        config.input_per_million_usd,
        config.output_per_million_usd,
        config.embedding_per_million_usd,
    ) < 0:
        raise ContractError("provider prices cannot be negative")
    if not any(
        value > 0
        for value in (
            config.input_per_million_usd,
            config.output_per_million_usd,
            config.embedding_per_million_usd,
        )
    ):
        raise ContractError("at least one non-zero provider price is required")
    if not (1 <= int(config.max_output_tokens) <= 32):
        raise ContractError("max_output_tokens must be between 1 and 32")
    if not (1 <= float(config.timeout_seconds) <= 120):
        raise ContractError("timeout_seconds must be between 1 and 120")

    parsed = parse.urlparse(config.base_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ContractError("base_url must be an absolute HTTPS URL")

    api_key = str(environ.get(config.api_key_env) or "").strip()
    if not api_key:
        raise ContractError(f"API key environment variable is empty: {config.api_key_env}")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ContractError(f"provider host cannot be resolved: {parsed.hostname}") from exc
    for raw_address in addresses:
        address = ipaddress.ip_address(raw_address)
        if not address.is_global:
            raise ContractError("provider host must resolve only to public addresses")

    # Fixed prompts are tiny, but tokenizer rules vary. Refuse before the call
    # using deliberately conservative input budgets.
    preflight_max = (
        128 * config.input_per_million_usd
        + int(config.max_output_tokens) * config.output_per_million_usd
        + 64 * config.embedding_per_million_usd
    ) / 1_000_000.0
    if preflight_max > config.max_cost_usd:
        raise ContractError(
            f"preflight maximum cost {preflight_max:.8f} USD exceeds ceiling "
            f"{config.max_cost_usd:.8f} USD"
        )
    return api_key


def _endpoint(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/{suffix.lstrip('/')}"


def _request_json(
    url: str,
    payload: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout_seconds: float,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    req = request.Request(url=url, data=body, method="POST", headers=dict(headers))
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            status = int(response.status)
            response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            raw = response.read(2 * 1024 * 1024 + 1)
    except error.HTTPError as exc:
        raise ContractError(f"provider returned HTTP {int(exc.code)} for {parse.urlparse(url).path}") from None
    except (error.URLError, TimeoutError, OSError) as exc:
        raise ContractError(f"provider transport failed for {parse.urlparse(url).path}: {type(exc).__name__}") from None
    if len(raw) > 2 * 1024 * 1024:
        raise ContractError("provider response exceeded 2 MiB")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractError("provider returned a non-JSON response") from None
    if not isinstance(decoded, dict):
        raise ContractError("provider JSON response must be an object")
    return status, decoded, response_headers


def _usage(body: Mapping[str, Any], *, require_output: bool) -> dict[str, int]:
    raw = body.get("usage")
    if not isinstance(raw, Mapping):
        raise ContractError("provider response is missing usage")
    input_tokens = raw.get("prompt_tokens", raw.get("input_tokens"))
    output_tokens = raw.get("completion_tokens", raw.get("output_tokens", 0))
    total_tokens = raw.get("total_tokens")
    try:
        parsed = {
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "total_tokens": int(total_tokens),
        }
    except (TypeError, ValueError):
        raise ContractError("provider usage fields must be integers") from None
    if parsed["input_tokens"] <= 0 or parsed["total_tokens"] <= 0:
        raise ContractError("provider usage token counts must be positive")
    if require_output and parsed["output_tokens"] <= 0:
        raise ContractError("chat completion usage must include output tokens")
    if parsed["total_tokens"] < parsed["input_tokens"] + parsed["output_tokens"]:
        raise ContractError("provider total_tokens is smaller than input + output")
    return parsed


def _protocol_metadata(headers: Mapping[str, str]) -> dict[str, Any]:
    normalized = {str(k).lower(): str(v) for k, v in headers.items()}
    rate_limit_names = sorted(name for name in RATE_LIMIT_HEADERS if name in normalized)
    request_id_name = next((name for name in REQUEST_ID_HEADERS if normalized.get(name)), "")
    return {
        "rate_limit_header_names": rate_limit_names,
        "request_id_header": request_id_name,
    }


def run_contract(
    config: ContractConfig,
    *,
    environ: Mapping[str, str] | None = None,
    requester: Callable[
        [str, Mapping[str, Any], Mapping[str, str], float],
        tuple[int, dict[str, Any], dict[str, str]],
    ] = _request_json,
) -> dict[str, Any]:
    environment = os.environ if environ is None else environ
    api_key = validate_config(config, environment)
    run_id = uuid.uuid4().hex
    common_headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "agent-powflow-provider-contract/1.0",
    }

    chat_headers = dict(common_headers)
    chat_headers["Idempotency-Key"] = f"apf-contract-chat-{run_id}"
    chat_status, chat_body, chat_response_headers = requester(
        _endpoint(config.base_url, "chat/completions"),
        {
            "model": config.chat_model,
            "messages": [{"role": "user", "content": "Reply with exactly: APF_OK"}],
            "temperature": 0,
            "max_tokens": int(config.max_output_tokens),
        },
        chat_headers,
        float(config.timeout_seconds),
    )
    choices = chat_body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ContractError("chat response is missing choices[0]")
    message = choices[0].get("message")
    if not isinstance(message, Mapping) or not str(message.get("content") or "").strip():
        raise ContractError("chat response is missing message content")
    chat_usage = _usage(chat_body, require_output=True)

    embedding_headers = dict(common_headers)
    embedding_headers["Idempotency-Key"] = f"apf-contract-embedding-{run_id}"
    embedding_status, embedding_body, embedding_response_headers = requester(
        _endpoint(config.base_url, "embeddings"),
        {"model": config.embedding_model, "input": "agent powflow contract"},
        embedding_headers,
        float(config.timeout_seconds),
    )
    data = embedding_body.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], Mapping):
        raise ContractError("embedding response is missing data[0]")
    vector = data[0].get("embedding")
    if not isinstance(vector, list) or not vector:
        raise ContractError("embedding response contains an empty vector")
    if any(not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in vector):
        raise ContractError("embedding vector contains non-finite values")
    embedding_usage = _usage(embedding_body, require_output=False)

    actual_cost = (
        chat_usage["input_tokens"] * config.input_per_million_usd
        + chat_usage["output_tokens"] * config.output_per_million_usd
        + embedding_usage["input_tokens"] * config.embedding_per_million_usd
    ) / 1_000_000.0
    if actual_cost > config.max_cost_usd:
        raise ContractError(
            f"observed estimated cost {actual_cost:.8f} USD exceeded ceiling "
            f"{config.max_cost_usd:.8f} USD"
        )

    chat_protocol = _protocol_metadata(chat_response_headers)
    embedding_protocol = _protocol_metadata(embedding_response_headers)
    rate_limit_names = sorted(
        set(chat_protocol["rate_limit_header_names"])
        | set(embedding_protocol["rate_limit_header_names"])
    )
    if config.require_rate_limit_headers and not rate_limit_names:
        raise ContractError("provider responses exposed no recognized rate-limit headers")

    # Report intentionally excludes key, prompts, generated content, vectors and
    # raw header values. The run hash is enough to correlate a local audit.
    return {
        "schema_version": 1,
        "passed": True,
        "provider": config.provider,
        "provider_host": parse.urlparse(config.base_url).hostname,
        "chat": {
            "status": chat_status,
            "model_requested": config.chat_model,
            "usage": chat_usage,
            **chat_protocol,
        },
        "embedding": {
            "status": embedding_status,
            "model_requested": config.embedding_model,
            "dimensions": len(vector),
            "usage": embedding_usage,
            **embedding_protocol,
        },
        "estimated_cost_usd": round(actual_cost, 8),
        "max_cost_usd": round(config.max_cost_usd, 8),
        "rate_limit_headers_observed": rate_limit_names,
        "run_fingerprint": hashlib.sha256(run_id.encode("ascii")).hexdigest()[:16],
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--chat-model", required=True)
    parser.add_argument("--embedding-model", required=True)
    parser.add_argument("--api-key-env", required=True)
    parser.add_argument("--input-per-million-usd", type=float, required=True)
    parser.add_argument("--output-per-million-usd", type=float, required=True)
    parser.add_argument("--embedding-per-million-usd", type=float, required=True)
    parser.add_argument("--max-cost-usd", type=float, required=True)
    parser.add_argument("--max-output-tokens", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=30)
    parser.add_argument("--require-rate-limit-headers", action="store_true")
    parser.add_argument("--allow-paid-provider-calls", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = ContractConfig(
        provider=args.provider,
        base_url=args.base_url,
        chat_model=args.chat_model,
        embedding_model=args.embedding_model,
        api_key_env=args.api_key_env,
        input_per_million_usd=args.input_per_million_usd,
        output_per_million_usd=args.output_per_million_usd,
        embedding_per_million_usd=args.embedding_per_million_usd,
        max_cost_usd=args.max_cost_usd,
        max_output_tokens=args.max_output_tokens,
        require_rate_limit_headers=args.require_rate_limit_headers,
        timeout_seconds=args.timeout_seconds,
        allow_paid_provider_calls=args.allow_paid_provider_calls,
    )
    try:
        report = run_contract(config)
    except ContractError as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
