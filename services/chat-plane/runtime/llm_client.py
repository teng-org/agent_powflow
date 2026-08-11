from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

from .api_pool import ChatApiCallLog, ChatApiEndpoint, chat_api_pool_manager
from .config import get_setting


class LlmStreamTruncatedError(RuntimeError):
    def __init__(self, message: str, *, finish_reason: str = "", endpoint_id: str = "", output_chars: int = 0) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason
        self.endpoint_id = endpoint_id
        self.output_chars = output_chars


class LlmClient:
    def __init__(self) -> None:
        self.timeout_seconds = int(get_setting("chat_llm_timeout_seconds", 120))
        self.provider_mode = os.getenv("CHAT_PROVIDER_MODE", "api_strict").strip().lower()
        self._keepalive_enabled = bool(get_setting("chat_http_keepalive_enabled", True))
        self._http_client: Optional[httpx.AsyncClient] = None

    def _client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20 if self._keepalive_enabled else 0,
                    keepalive_expiry=30.0,
                ),
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None

    @staticmethod
    def _mock_completion(messages: List[Dict[str, str]]) -> str:
        prompt = next(
            (str((item or {}).get("content") or "") for item in reversed(messages) if (item or {}).get("role") == "user"),
            "",
        )
        compact = " ".join(prompt.split())
        return f"[mock] 已基于本地测试上下文生成回答：{compact[-500:]}。此响应未调用外部模型。"

    def _headers(self, api_key: str) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, int(len(str(text or "")) / 3.5))

    def _resolve_endpoint(
        self,
        task: str,
        tenant_id: str = "*",
        *,
        advance: bool = True,
        exclude_endpoint_ids: Optional[set[str]] = None,
    ) -> Tuple[Optional[ChatApiEndpoint], str, str, str]:
        endpoint = chat_api_pool_manager.choose_endpoint(
            task=task,
            tenant_id=tenant_id,
            advance=advance,
            exclude_endpoint_ids=exclude_endpoint_ids,
        )
        if not endpoint and task in {"chat_aux", "chat_summary"}:
            endpoint = chat_api_pool_manager.choose_endpoint(
                task="chat_llm",
                tenant_id=tenant_id,
                advance=advance,
                exclude_endpoint_ids=exclude_endpoint_ids,
            )
        if endpoint:
            key = str(endpoint.api_key or "").strip()
            return endpoint, endpoint.base_url.rstrip("/"), endpoint.model, key
        return None, "", "", ""

    def runtime_meta(self) -> Dict[str, str]:
        if self.provider_mode == "mock":
            return {
                "base_url": "local://mock",
                "model": "deterministic-mock",
                "key_source": "none",
                "api_key_configured": "no",
                "embedding_base_url": "local://mock",
                "embedding_model": "deterministic-mock",
                "embedding_key_source": "none",
                "embedding_api_key_configured": "no",
            }
        ep_chat, base_url, model, api_key = self._resolve_endpoint("chat_llm", advance=False)
        ep_emb, emb_base_url, emb_model, emb_api_key = self._resolve_endpoint("chat_embedding", advance=False)
        return {
            "base_url": base_url,
            "model": model,
            "key_source": (
                f"chat_api_pool.endpoint:{ep_chat.endpoint_id}" if ep_chat else "chat_api_pool.unconfigured"
            ),
            "api_key_configured": "yes" if bool(api_key) else "no",
            "embedding_base_url": emb_base_url,
            "embedding_model": emb_model,
            "embedding_key_source": (
                f"chat_api_pool.endpoint:{ep_emb.endpoint_id}" if ep_emb else "chat_api_pool.unconfigured"
            ),
            "embedding_api_key_configured": "yes" if bool(emb_api_key) else "no",
        }

    async def _request_json(
        self,
        *,
        task: str,
        path: str,
        payload: Dict[str, Any],
        tenant_id: str = "*",
        input_text_for_usage: str = "",
        exclude_endpoint_ids: Optional[set[str]] = None,
        attempt_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> Dict[str, Any]:
        last_exc: Optional[Exception] = None
        configured_retry_max = int(get_setting("chat_request_retry_max", 2))
        max_attempts = max(1, configured_retry_max + 1)
        attempt = 0
        failed_endpoint_ids: set[str] = set(exclude_endpoint_ids or set())
        while attempt < max_attempts:
            endpoint, base_url, model, api_key = self._resolve_endpoint(
                task=task,
                tenant_id=tenant_id,
                exclude_endpoint_ids=failed_endpoint_ids,
            )
            if not api_key:
                raise RuntimeError(f"API pool endpoint or key missing for task={task}")
            timeout_s = int(endpoint.timeout_seconds if endpoint else self.timeout_seconds)
            if endpoint:
                max_attempts = max(max_attempts, int(endpoint.retry_max) + 1)
            endpoint_id = endpoint.endpoint_id if endpoint else "runtime-fallback"
            input_price = float(endpoint.input_per_1k if endpoint else 0.0)
            output_price = float(endpoint.output_per_1k if endpoint else 0.0)
            url = f"{base_url}{path}"
            request_payload = dict(payload)
            if model and "model" in request_payload:
                request_payload["model"] = model
            if attempt_callback:
                await attempt_callback(
                    {
                        "task": task,
                        "attempt": attempt,
                        "endpoint_id": endpoint_id,
                        "base_url": base_url,
                        "model": model,
                        "path": path,
                        "stream": False,
                    }
                )
            acquired = True
            if endpoint:
                acquired = chat_api_pool_manager.try_acquire_slot(endpoint.endpoint_id)
            if not acquired:
                last_exc = RuntimeError(f"endpoint busy: {endpoint.endpoint_id}")
                failed_endpoint_ids.add(endpoint.endpoint_id)
                attempt += 1
                continue
            started = time.perf_counter()
            try:
                timeout = httpx.Timeout(timeout_s)
                resp = await self._client().post(
                    url,
                    headers=self._headers(api_key),
                    json=request_payload,
                    timeout=timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                latency_ms = (time.perf_counter() - started) * 1000.0
                usage = data.get("usage") if isinstance(data, dict) else {}
                in_tokens = int((usage or {}).get("prompt_tokens") or self._estimate_tokens(input_text_for_usage))
                out_tokens = int((usage or {}).get("completion_tokens") or 0)
                chat_api_pool_manager.record_result(
                    endpoint_id,
                    ok=True,
                    latency_ms=latency_ms,
                    tenant_id=tenant_id,
                    task=task,
                    model=model,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    input_per_1k=input_price,
                    output_per_1k=output_price,
                )
                cost_total = (in_tokens / 1000.0) * input_price + (out_tokens / 1000.0) * output_price
                chat_api_pool_manager.add_call_log(
                    ChatApiCallLog(
                        log_id=uuid.uuid4().hex,
                        tenant_id=str(tenant_id or "*"),
                        task=str(task or ""),
                        endpoint_id=endpoint_id,
                        model=str(model or ""),
                        base_url=str(base_url),
                        path=str(path),
                        ok=True,
                        status_code=int(resp.status_code),
                        error="",
                        latency_ms=round(float(latency_ms), 2),
                        retry_count=int(attempt),
                        input_tokens=in_tokens,
                        output_tokens=out_tokens,
                        cost_total=round(float(cost_total), 6),
                        request_summary=str(input_text_for_usage or "")[:240],
                        response_summary=str(data)[:240],
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                return data if isinstance(data, dict) else {}
            except Exception as exc:
                if isinstance(exc, LlmStreamTruncatedError):
                    setattr(exc, "attempted_endpoint_ids", set(failed_endpoint_ids) | {endpoint_id})
                    raise
                last_exc = exc
                latency_ms = (time.perf_counter() - started) * 1000.0
                is_retryable = self._is_retryable_request_error(exc)
                status_code = self._http_status_code(exc)
                chat_api_pool_manager.record_result(
                    endpoint_id,
                    ok=False,
                    latency_ms=latency_ms,
                    error=str(exc),
                    tenant_id=tenant_id,
                    task=task,
                    model=model,
                    input_tokens=self._estimate_tokens(input_text_for_usage),
                    output_tokens=0,
                    input_per_1k=input_price,
                    output_per_1k=output_price,
                )
                in_tokens = self._estimate_tokens(input_text_for_usage)
                cost_total = (in_tokens / 1000.0) * input_price
                chat_api_pool_manager.add_call_log(
                    ChatApiCallLog(
                        log_id=uuid.uuid4().hex,
                        tenant_id=str(tenant_id or "*"),
                        task=str(task or ""),
                        endpoint_id=endpoint_id,
                        model=str(model or ""),
                        base_url=str(base_url),
                        path=str(path),
                        ok=False,
                        status_code=int(status_code),
                        error=str(exc)[:500],
                        latency_ms=round(float(latency_ms), 2),
                        retry_count=int(attempt),
                        input_tokens=in_tokens,
                        output_tokens=0,
                        cost_total=round(float(cost_total), 6),
                        request_summary=str(input_text_for_usage or "")[:240],
                        response_summary="",
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                if attempt >= max_attempts - 1 or not is_retryable:
                    break
                failed_endpoint_ids.add(endpoint_id)
                await self._sleep_before_retry(attempt, exc)
            finally:
                if endpoint:
                    chat_api_pool_manager.release_slot(endpoint.endpoint_id)
            attempt += 1
        if last_exc is not None:
            setattr(last_exc, "attempted_endpoint_ids", set(failed_endpoint_ids))
            raise last_exc
        raise RuntimeError("request failed without explicit exception")

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        *,
        tenant_id: str = "*",
        endpoint_id: Optional[str] = None,
        temperature: float = 0.2,
        top_p: float = 1.0,
        max_tokens: Optional[int] = None,
        attempt_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> AsyncGenerator[str, None]:
        if self.provider_mode == "mock":
            if attempt_callback:
                await attempt_callback({
                    "task": "chat_llm", "attempt": 0, "endpoint_id": "local-mock",
                    "base_url": "local://mock", "model": "deterministic-mock",
                    "path": "/chat/completions(stream)", "stream": True,
                })
            text = self._mock_completion(messages)
            for index in range(0, len(text), 48):
                await asyncio.sleep(0)
                yield text[index:index + 48]
            return
        prompt_text = "\n".join(str((m or {}).get("content") or "") for m in messages)
        max_attempts = max(1, int(get_setting("chat_stream_retry_max", 2)) + 1)
        last_exc: Optional[Exception] = None
        failed_endpoint_ids: set[str] = set()

        for attempt in range(max_attempts):
            endpoint = None
            if endpoint_id:
                candidate = chat_api_pool_manager.get_endpoint(endpoint_id)
                allowed = chat_api_pool_manager.list_candidates(
                    task="chat_llm", tenant_id=tenant_id, include_unhealthy=True
                )
                if candidate and any(item.endpoint_id == candidate.endpoint_id for item in allowed):
                    endpoint = candidate
                if endpoint is None:
                    raise RuntimeError("selected model endpoint is unavailable for current workspace")
            if endpoint:
                base_url, model, api_key = endpoint.base_url.rstrip("/"), endpoint.model, str(endpoint.api_key or "").strip()
            else:
                endpoint, base_url, model, api_key = self._resolve_endpoint(
                    task="chat_llm",
                    tenant_id=tenant_id,
                    exclude_endpoint_ids=failed_endpoint_ids,
                )
            if not api_key:
                raise RuntimeError("API pool endpoint or key missing for task=chat_llm")

            endpoint_id = endpoint.endpoint_id if endpoint else "runtime-fallback"
            timeout_s = int(endpoint.timeout_seconds if endpoint else self.timeout_seconds)
            input_price = float(endpoint.input_per_1k if endpoint else 0.0)
            output_price = float(endpoint.output_per_1k if endpoint else 0.0)
            acquired = True
            if endpoint:
                acquired = chat_api_pool_manager.try_acquire_slot(endpoint.endpoint_id)
            if not acquired:
                last_exc = RuntimeError(f"endpoint busy: {endpoint.endpoint_id}")
                failed_endpoint_ids.add(endpoint.endpoint_id)
                continue

            url = f"{base_url}/chat/completions"
            payload = {
                "model": model,
                "messages": messages,
                "temperature": max(0.0, min(float(temperature), 2.0)),
                "top_p": max(0.01, min(float(top_p), 1.0)),
                "stream": True,
                "max_tokens": max(64, min(int(max_tokens or self._runtime_stream_max_tokens()), 32768)),
            }
            if attempt_callback:
                await attempt_callback(
                    {
                        "task": "chat_llm",
                        "attempt": attempt,
                        "endpoint_id": endpoint_id,
                        "base_url": base_url,
                        "model": model,
                        "path": "/chat/completions(stream)",
                        "stream": True,
                    }
                )
            started = time.perf_counter()
            out_chars = 0
            finish_reason = ""
            response_headers_ms = 0.0
            first_token_ms = 0.0
            try:
                timeout = httpx.Timeout(timeout_s)
                async with asyncio.timeout(self._runtime_stream_total_timeout_seconds(timeout_s)):
                    async with self._client().stream(
                        "POST", url, headers=self._headers(api_key), json=payload, timeout=timeout
                    ) as resp:
                        response_headers_ms = (time.perf_counter() - started) * 1000.0
                        resp.raise_for_status()
                        async for raw_line in resp.aiter_lines():
                            if not raw_line:
                                continue
                            line = raw_line.strip()
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                obj = json.loads(data)
                            except Exception:
                                continue
                            choice = (obj.get("choices") or [{}])[0] or {}
                            finish_reason = str(choice.get("finish_reason") or finish_reason or "")
                            delta = (choice.get("delta") or {}).get("content")
                            if delta:
                                if first_token_ms <= 0:
                                    first_token_ms = (time.perf_counter() - started) * 1000.0
                                out_chars += len(str(delta))
                                yield str(delta)

                latency_ms = (time.perf_counter() - started) * 1000.0
                chat_api_pool_manager.record_result(
                    endpoint_id,
                    ok=True,
                    latency_ms=latency_ms,
                    tenant_id=tenant_id,
                    task="chat_llm",
                    model=model,
                    input_tokens=self._estimate_tokens(prompt_text),
                    output_tokens=self._estimate_tokens("x" * out_chars),
                    input_per_1k=input_price,
                    output_per_1k=output_price,
                )
                chat_api_pool_manager.add_call_log(
                    ChatApiCallLog(
                        log_id=uuid.uuid4().hex,
                        tenant_id=str(tenant_id or "*"),
                        task="chat_llm",
                        endpoint_id=endpoint_id,
                        model=str(model or ""),
                        base_url=str(base_url),
                        path="/chat/completions(stream)",
                        ok=True,
                        status_code=200,
                        error=f"finish_reason={finish_reason}" if finish_reason else "",
                        latency_ms=round(float(latency_ms), 2),
                        retry_count=attempt,
                        input_tokens=self._estimate_tokens(prompt_text),
                        output_tokens=self._estimate_tokens("x" * out_chars),
                        cost_total=round(
                            (self._estimate_tokens(prompt_text) / 1000.0) * input_price
                            + (self._estimate_tokens("x" * out_chars) / 1000.0) * output_price,
                            6,
                        ),
                        request_summary=prompt_text[:240],
                        response_summary=f"stream_chars={out_chars}; finish_reason={finish_reason or '-'}",
                        response_headers_ms=round(float(response_headers_ms), 2),
                        first_token_ms=round(float(first_token_ms), 2),
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                if finish_reason.lower() in {"length", "max_tokens", "max_completion_tokens"}:
                    exc = LlmStreamTruncatedError(
                        f"stream stopped by token limit: finish_reason={finish_reason}, output_chars={out_chars}",
                        finish_reason=finish_reason,
                        endpoint_id=endpoint_id,
                        output_chars=out_chars,
                    )
                    setattr(exc, "attempted_endpoint_ids", set(failed_endpoint_ids) | {endpoint_id})
                    raise exc
                return
            except Exception as exc:
                last_exc = exc
                latency_ms = (time.perf_counter() - started) * 1000.0
                partial_success_min = self._runtime_stream_partial_success_min_chars()
                if out_chars >= partial_success_min and self._is_retryable_stream_error(exc):
                    # Tokens have already been yielded to the caller. For sufficiently long output,
                    # avoid retrying here because it would duplicate visible text. Short partials are
                    # treated as failures so the pipeline can fall back to non-stream completion.
                    chat_api_pool_manager.record_result(
                        endpoint_id,
                        ok=True,
                        latency_ms=latency_ms,
                        tenant_id=tenant_id,
                        task="chat_llm",
                        model=model,
                        input_tokens=self._estimate_tokens(prompt_text),
                        output_tokens=self._estimate_tokens("x" * out_chars),
                        input_per_1k=input_price,
                        output_per_1k=output_price,
                    )
                    chat_api_pool_manager.add_call_log(
                        ChatApiCallLog(
                            log_id=uuid.uuid4().hex,
                            tenant_id=str(tenant_id or "*"),
                            task="chat_llm",
                            endpoint_id=endpoint_id,
                            model=str(model or ""),
                            base_url=str(base_url),
                            path="/chat/completions(stream)",
                            ok=True,
                            status_code=200,
                            error=f"stream ended early after partial output: {str(exc)[:300]}",
                            latency_ms=round(float(latency_ms), 2),
                            retry_count=attempt,
                            input_tokens=self._estimate_tokens(prompt_text),
                            output_tokens=self._estimate_tokens("x" * out_chars),
                            cost_total=round(
                                (self._estimate_tokens(prompt_text) / 1000.0) * input_price
                                + (self._estimate_tokens("x" * out_chars) / 1000.0) * output_price,
                                6,
                            ),
                            request_summary=prompt_text[:240],
                            response_summary=f"stream_chars={out_chars}; partial_close=true",
                            response_headers_ms=round(float(response_headers_ms), 2),
                            first_token_ms=round(float(first_token_ms), 2),
                            created_at=datetime.now(timezone.utc).isoformat(),
                        )
                    )
                    return

                chat_api_pool_manager.record_result(
                    endpoint_id,
                    ok=False,
                    latency_ms=latency_ms,
                    error=str(exc),
                    tenant_id=tenant_id,
                    task="chat_llm",
                    model=model,
                    input_tokens=self._estimate_tokens(prompt_text),
                    output_tokens=0,
                    input_per_1k=input_price,
                    output_per_1k=output_price,
                )
                chat_api_pool_manager.add_call_log(
                    ChatApiCallLog(
                        log_id=uuid.uuid4().hex,
                        tenant_id=str(tenant_id or "*"),
                        task="chat_llm",
                        endpoint_id=endpoint_id,
                        model=str(model or ""),
                        base_url=str(base_url),
                        path="/chat/completions(stream)",
                        ok=False,
                        status_code=self._http_status_code(exc),
                        error=str(exc)[:500],
                        latency_ms=round(float(latency_ms), 2),
                        retry_count=attempt,
                        input_tokens=self._estimate_tokens(prompt_text),
                        output_tokens=0,
                        cost_total=round((self._estimate_tokens(prompt_text) / 1000.0) * input_price, 6),
                        request_summary=prompt_text[:240],
                        response_summary="",
                        response_headers_ms=round(float(response_headers_ms), 2),
                        first_token_ms=round(float(first_token_ms), 2),
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
                if attempt >= max_attempts - 1 or not self._is_retryable_stream_error(exc):
                    setattr(exc, "attempted_endpoint_ids", set(failed_endpoint_ids) | {endpoint_id})
                    raise
                failed_endpoint_ids.add(endpoint_id)
                await self._sleep_before_retry(attempt, exc)
                continue
            finally:
                if endpoint:
                    chat_api_pool_manager.release_slot(endpoint.endpoint_id)
        if last_exc is not None:
            setattr(last_exc, "attempted_endpoint_ids", set(failed_endpoint_ids))
            raise last_exc
        raise RuntimeError("stream request failed without explicit exception")

    @staticmethod
    def _is_retryable_stream_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            LlmClient._is_retryable_request_error(exc)
            or "incomplete chunked read" in text
            or "server disconnected" in text
            or "peer closed connection" in text
            or "responseended" in text
        )

    @staticmethod
    def _http_status_code(exc: Exception) -> int:
        if isinstance(exc, httpx.HTTPStatusError):
            return int(exc.response.status_code)
        return 0

    @staticmethod
    def _is_retryable_request_error(exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return int(exc.response.status_code) in {408, 425, 429, 500, 502, 503, 504}
        return isinstance(exc, (httpx.TimeoutException, httpx.TransportError))

    @staticmethod
    async def _sleep_before_retry(failed_attempt: int, exc: Exception) -> None:
        try:
            initial_ms = max(0, min(int(get_setting("chat_provider_retry_initial_ms", 250)), 30_000))
            max_ms = max(initial_ms, min(int(get_setting("chat_provider_retry_max_ms", 5_000)), 60_000))
        except Exception:
            initial_ms, max_ms = 250, 5_000
        delay_seconds = min(max_ms, initial_ms * (2 ** max(0, failed_attempt))) / 1000.0
        if isinstance(exc, httpx.HTTPStatusError):
            retry_after = str(exc.response.headers.get("retry-after") or "").strip()
            try:
                delay_seconds = max(delay_seconds, min(float(retry_after), 60.0))
            except (TypeError, ValueError):
                pass
        if delay_seconds > 0:
            await asyncio.sleep(delay_seconds)

    @staticmethod
    def _runtime_stream_max_tokens() -> int:
        try:
            return max(256, min(8192, int(get_setting("chat_stream_max_tokens", 4096))))
        except Exception:
            return 4096

    @staticmethod
    def _runtime_stream_partial_success_min_chars() -> int:
        try:
            return max(1, min(12000, int(get_setting("chat_stream_partial_success_min_chars", 1200))))
        except Exception:
            return 1200

    @staticmethod
    def _runtime_stream_total_timeout_seconds(endpoint_timeout_seconds: int) -> int:
        try:
            configured = int(get_setting("chat_stream_total_timeout_seconds", endpoint_timeout_seconds))
        except Exception:
            configured = endpoint_timeout_seconds
        return max(5, min(configured, 600))

    async def probe_endpoint(
        self,
        endpoint_id: str,
        *,
        max_tokens: int = 8,
        total_timeout_seconds: int = 30,
    ) -> Dict[str, Any]:
        """Run one no-retry, low-token provider request against an exact endpoint."""
        endpoint = chat_api_pool_manager.get_endpoint(endpoint_id)
        if not endpoint:
            raise ValueError("endpoint not found")
        if not endpoint.enabled:
            raise ValueError("endpoint is disabled")
        if not str(endpoint.api_key or "").strip():
            raise ValueError("endpoint API key is missing")
        if not chat_api_pool_manager.try_acquire_slot(endpoint.endpoint_id):
            raise RuntimeError("endpoint is busy or its circuit is open")

        started = time.perf_counter()
        headers_ms = 0.0
        first_token_ms = 0.0
        output_size = 0
        status_code = 0
        task = str(endpoint.task or "")
        is_embedding = task == "chat_embedding"
        path = "/embeddings" if is_embedding else "/chat/completions"
        try:
            timeout_seconds = max(5, min(int(total_timeout_seconds), 60))
            timeout = httpx.Timeout(min(int(endpoint.timeout_seconds), timeout_seconds))
            async with asyncio.timeout(timeout_seconds):
                if is_embedding:
                    response = await self._client().post(
                        f"{endpoint.base_url.rstrip('/')}{path}",
                        headers=self._headers(endpoint.api_key),
                        json={"model": endpoint.model, "input": "agent-powflow endpoint probe"},
                        timeout=timeout,
                    )
                    status_code = int(response.status_code)
                    headers_ms = (time.perf_counter() - started) * 1000.0
                    response.raise_for_status()
                    data = response.json()
                    vector = (((data.get("data") or [{}])[0]) or {}).get("embedding") or []
                    output_size = len(vector)
                    result_detail = {"vector_dimensions": output_size}
                else:
                    payload = {
                        "model": endpoint.model,
                        "messages": [{"role": "user", "content": "Reply with exactly OK."}],
                        "temperature": 0,
                        "stream": True,
                        "max_tokens": max(1, min(int(max_tokens), 64)),
                    }
                    finish_reason = ""
                    reasoning_chars = 0
                    async with self._client().stream(
                        "POST",
                        f"{endpoint.base_url.rstrip('/')}{path}",
                        headers=self._headers(endpoint.api_key),
                        json=payload,
                        timeout=timeout,
                    ) as response:
                        status_code = int(response.status_code)
                        headers_ms = (time.perf_counter() - started) * 1000.0
                        response.raise_for_status()
                        async for raw_line in response.aiter_lines():
                            line = str(raw_line or "").strip()
                            if not line.startswith("data:"):
                                continue
                            raw_data = line[5:].strip()
                            if raw_data == "[DONE]":
                                break
                            try:
                                item = json.loads(raw_data)
                            except Exception:
                                continue
                            choice = (item.get("choices") or [{}])[0] or {}
                            finish_reason = str(choice.get("finish_reason") or finish_reason)
                            delta = (choice.get("delta") or {}).get("content")
                            reasoning_delta = (choice.get("delta") or {}).get("reasoning_content")
                            if reasoning_delta:
                                reasoning_chars += len(str(reasoning_delta))
                            if delta:
                                if first_token_ms <= 0:
                                    first_token_ms = (time.perf_counter() - started) * 1000.0
                                output_size += len(str(delta))
                    result_detail = {
                        "output_chars": output_size,
                        "reasoning_chars": reasoning_chars,
                        "finish_reason": finish_reason or "unknown",
                        "result_status": "PASS" if output_size > 0 else "DEGRADED",
                    }

            total_ms = (time.perf_counter() - started) * 1000.0
            chat_api_pool_manager.record_result(
                endpoint.endpoint_id, ok=True, latency_ms=total_ms, task=task, model=endpoint.model
            )
            return {
                "endpoint_id": endpoint.endpoint_id,
                "provider": endpoint.provider,
                "task": task,
                "model": endpoint.model,
                "status_code": status_code,
                "response_headers_ms": round(headers_ms, 2),
                "first_token_ms": round(first_token_ms, 2),
                "total_ms": round(total_ms, 2),
                "result_status": "PASS" if is_embedding else result_detail.get("result_status", "PASS"),
                **result_detail,
            }
        except Exception as exc:
            total_ms = (time.perf_counter() - started) * 1000.0
            chat_api_pool_manager.record_result(
                endpoint.endpoint_id,
                ok=False,
                latency_ms=total_ms,
                error=str(exc),
                task=task,
                model=endpoint.model,
            )
            setattr(exc, "probe_status_code", status_code)
            setattr(exc, "probe_total_ms", round(total_ms, 2))
            raise
        finally:
            chat_api_pool_manager.release_slot(endpoint.endpoint_id)

    async def complete_text(
        self,
        messages: List[Dict[str, str]],
        *,
        max_tokens: int = 400,
        temperature: float = 0.2,
        tenant_id: str = "*",
        task: str = "chat_llm",
        exclude_endpoint_ids: Optional[set[str]] = None,
        attempt_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
    ) -> str:
        if self.provider_mode == "mock":
            if attempt_callback:
                await attempt_callback({
                    "task": task, "attempt": 0, "endpoint_id": "local-mock",
                    "base_url": "local://mock", "model": "deterministic-mock",
                    "path": "/chat/completions", "stream": False,
                })
            return self._mock_completion(messages)
        if task not in {"chat_llm", "chat_aux", "chat_summary"}:
            task = "chat_llm"
        payload = {
            "model": "",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        in_txt = "\n".join(str((m or {}).get("content") or "") for m in messages)
        obj = await self._request_json(
            task=task,
            path="/chat/completions",
            payload=payload,
            tenant_id=tenant_id,
            input_text_for_usage=in_txt,
            exclude_endpoint_ids=exclude_endpoint_ids,
            attempt_callback=attempt_callback,
        )
        content = (((obj.get("choices") or [{}])[0]).get("message") or {}).get("content")
        return str(content or "").strip()

    async def embed_text(
        self,
        text: str,
        *,
        tenant_id: str = "*",
    ) -> List[float]:
        t = str(text or "").strip()
        if not t:
            return []
        if self.provider_mode == "mock":
            dimensions = 256
            vector = [0.0] * dimensions
            tokens = [t[index:index + 2] for index in range(max(1, len(t) - 1))]
            for token in tokens:
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                vector[int.from_bytes(digest[:4], "big") % dimensions] += 1.0
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            return [value / norm for value in vector]
        obj = await self._request_json(
            task="chat_embedding",
            path="/embeddings",
            payload={"model": "", "input": t},
            tenant_id=tenant_id,
            input_text_for_usage=t,
        )
        data = obj.get("data") or []
        if not data:
            return []
        emb = (data[0] or {}).get("embedding") or []
        return [float(x) for x in emb]


llm_client = LlmClient()
