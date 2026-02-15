import hashlib
import json
import math
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import httpx

from models.api_pool import ApiEndpoint, api_pool_manager, aux_api_pool_manager
from retrieval.store import kb_store


class AiGateway:
    OVERVIEW_KEYS = [
        "overview",
        "doc_overview",
        "kb_overview",
        "abstract",
        "结论",
        "概述",
    ]
    SUMMARY_KEYS = [
        "summary",
        "doc_summary",
        "kb_summary",
        "摘要",
        "要点",
        "highlights",
        "key_points",
        "points",
    ]

    def __init__(self) -> None:
        self._bootstrapped = False
        self._runtime_config_cache: dict[str, Any] = {}
        self._runtime_config_mtime: float = -1.0
        self.main_pool_config = os.getenv(
            "API_POOL_DEFAULT_CONFIG",
            "/app/services/execution-plane/config/api_pool.default.json",
        )
        self.aux_pool_config = os.getenv(
            "AUX_API_POOL_DEFAULT_CONFIG",
            "/app/services/execution-plane/config/aux_api_pool.default.json",
        )
        self.runtime_config_path = os.getenv(
            "RUNTIME_CONFIG_PATH",
            "/app/config/runtime-config.json",
        )

    def _ensure_bootstrap(self) -> None:
        if self._bootstrapped:
            return
        api_pool_manager.bootstrap(os.path.abspath(self.main_pool_config))
        aux_api_pool_manager.bootstrap(os.path.abspath(self.aux_pool_config))
        self._bootstrapped = True

    def _manager(self, pool: str):
        return aux_api_pool_manager if pool == "aux" else api_pool_manager

    def _resolve_endpoint(self, tenant_id: str, pool: str, task: str) -> ApiEndpoint | None:
        self._ensure_bootstrap()
        return self._manager(pool).get_default_for_tenant(tenant_id=tenant_id, task=task)

    def _resolve_embedding_endpoint(self, tenant_id: str, pool: str, task: str) -> ApiEndpoint | None:
        """
        Resolve embedding endpoint with task alias fallback.
        Many deployments only configure a generic `embedding` task endpoint.
        """
        tried: list[str] = []

        def _try(t: str) -> ApiEndpoint | None:
            if not t or t in tried:
                return None
            tried.append(t)
            return self._resolve_endpoint(tenant_id=tenant_id, pool=pool, task=t)

        # 1) exact task first
        endpoint = _try((task or "").strip())
        if endpoint and endpoint.enabled:
            return endpoint

        # 2) fallback aliases
        aliases: list[str] = []
        if pool == "main":
            aliases = [
                "embedding",
                "doc_embedding",
                "chunk_embedding",
                "chunk_commit_embedding",
                "reindex_embedding",
            ]
        elif pool == "aux":
            aliases = [
                "summary_embedding",
                "embedding",
            ]
        else:
            aliases = ["embedding"]

        for alias in aliases:
            endpoint = _try(alias)
            if endpoint and endpoint.enabled:
                return endpoint
        return None

    def _local_embedding(self, text: str, dimensions: int = 256) -> list[float]:
        vec = [0.0] * dimensions
        tokens = [t for t in (text or "").lower().split() if t]
        if not tokens:
            return vec
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()  # nosec B324
            idx = int.from_bytes(digest[:4], "big") % dimensions
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _load_runtime_config(self) -> dict[str, Any]:
        path_candidates = [
            self.runtime_config_path,
            os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../config/runtime-config.json")),
        ]
        target = ""
        for p in path_candidates:
            if p and os.path.exists(p):
                target = p
                break
        if not target:
            return {}
        try:
            mtime = os.path.getmtime(target)
        except OSError:
            return {}
        if self._runtime_config_cache and self._runtime_config_mtime == mtime:
            return self._runtime_config_cache
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._runtime_config_cache = data if isinstance(data, dict) else {}
            self._runtime_config_mtime = mtime
            return self._runtime_config_cache
        except Exception:
            # Avoid noisy failures in hot paths; return stale cache if available.
            return self._runtime_config_cache

    def _runtime_key(self, *keys: str) -> str:
        conf = self._load_runtime_config()
        for key in keys:
            value = str(conf.get(key, "") or "").strip()
            if value:
                return value
        return ""

    def _runtime_int(self, key: str, default: int, min_value: int | None = None, max_value: int | None = None) -> int:
        conf = self._load_runtime_config()
        raw = conf.get(key, default)
        try:
            value = int(raw)
        except Exception:
            value = int(default)
        if min_value is not None and value < min_value:
            value = min_value
        if max_value is not None and value > max_value:
            value = max_value
        return value

    def _runtime_bool(self, key: str, default: bool = False) -> bool:
        conf = self._load_runtime_config()
        raw = conf.get(key, default)
        if isinstance(raw, bool):
            return raw
        text = str(raw or "").strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    def _endpoint_api_key(self, endpoint: ApiEndpoint) -> str:
        if endpoint.api_key:
            return endpoint.api_key
        provider = (endpoint.provider or "").strip().lower()
        base_url = (endpoint.base_url or "").strip().lower()
        # Runtime config is the primary source for API keys.
        if provider == "siliconflow":
            return self._runtime_key("siliconflow_api_key", "embedding_api_key")
        if provider == "openai":
            return self._runtime_key("openai_api_key")
        if provider == "openai_compatible":
            if "siliconflow.cn" in base_url:
                return self._runtime_key("siliconflow_api_key", "openai_api_key")
            return self._runtime_key("openai_api_key", "siliconflow_api_key")
        # Backward-compatible env fallback.
        if endpoint.provider == "siliconflow":
            return os.getenv("SILICONFLOW_API_KEY", "")
        if endpoint.provider in {"openai", "openai_compatible"}:
            return os.getenv("OPENAI_API_KEY", "")
        return ""

    def _api_key_fingerprint(self, api_key: str) -> str:
        key = (api_key or "").strip()
        if not key:
            return "none"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]  # nosec B324

    def _parse_usage_tokens(self, body: dict[str, Any]) -> dict[str, int]:
        usage = body.get("usage") if isinstance(body, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
        total_tokens = usage.get("total_tokens", None)
        try:
            in_t = max(0, int(input_tokens or 0))
        except Exception:
            in_t = 0
        try:
            out_t = max(0, int(output_tokens or 0))
        except Exception:
            out_t = 0
        if total_tokens is None:
            all_tokens = in_t + out_t
        else:
            try:
                all_tokens = max(0, int(total_tokens or 0))
            except Exception:
                all_tokens = in_t + out_t
        return {"input_tokens": in_t, "output_tokens": out_t, "total_tokens": all_tokens}

    def _estimate_cost_usd(self, endpoint: ApiEndpoint, usage_tokens: dict[str, int]) -> float:
        try:
            endpoint_in_rate = float(getattr(endpoint, "input_per_1k", 0.0) or 0.0)
            endpoint_out_rate = float(getattr(endpoint, "output_per_1k", 0.0) or 0.0)
        except Exception:
            endpoint_in_rate = 0.0
            endpoint_out_rate = 0.0
        conf = self._load_runtime_config()
        pricing = conf.get("api_model_pricing", {})
        in_rate = endpoint_in_rate
        out_rate = endpoint_out_rate
        if in_rate <= 0.0 and out_rate <= 0.0 and isinstance(pricing, dict):
            model_key = str(getattr(endpoint, "model", "") or "").strip()
            item = pricing.get(model_key) or pricing.get("*")
            if isinstance(item, dict):
                try:
                    in_rate = float(item.get("input_per_1k", 0.0) or 0.0)
                    out_rate = float(item.get("output_per_1k", 0.0) or 0.0)
                except Exception:
                    in_rate = 0.0
                    out_rate = 0.0
        input_tokens = max(0, int(usage_tokens.get("input_tokens", 0) or 0))
        output_tokens = max(0, int(usage_tokens.get("output_tokens", 0) or 0))
        cost = (input_tokens / 1000.0) * in_rate + (output_tokens / 1000.0) * out_rate
        return float(max(0.0, round(cost, 8)))

    def _record_api_usage(
        self,
        *,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        pool: str,
        task_category: str,
        endpoint: ApiEndpoint,
        api_key: str,
        api_path: str,
        success: bool,
        usage_tokens: dict[str, int] | None = None,
        error_reason: str = "",
    ) -> None:
        usage_tokens = usage_tokens or {}
        try:
            kb_store.record_api_token_usage(
                tenant_id=tenant_id,
                kb_id=kb_id,
                doc_id=doc_id,
                pool=pool,
                task_category=(task_category or "").strip().lower(),
                endpoint_id=endpoint.endpoint_id,
                provider=endpoint.provider,
                base_url=endpoint.base_url,
                api_path=api_path,
                model=endpoint.model,
                api_key_fingerprint=self._api_key_fingerprint(api_key),
                success=bool(success),
                error_reason=(error_reason or "")[:400],
                request_count=1,
                input_tokens=int(usage_tokens.get("input_tokens", 0) or 0),
                output_tokens=int(usage_tokens.get("output_tokens", 0) or 0),
                total_tokens=int(usage_tokens.get("total_tokens", 0) or 0),
                estimated_cost_usd=self._estimate_cost_usd(endpoint, usage_tokens),
            )
        except Exception:
            return

    def _embed_openai_compatible(
        self,
        endpoint: ApiEndpoint,
        text: str,
        *,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        pool: str,
        task_category: str,
    ) -> list[float]:
        api_key = self._endpoint_api_key(endpoint)
        if not api_key:
            raise RuntimeError(f"Missing API key for endpoint {endpoint.endpoint_id}")
        slot_holder = str(uuid.uuid4())
        slot_limit = self._runtime_int("api_slot_max_per_endpoint", 8, min_value=1, max_value=64)
        slot_wait = self._runtime_int("api_slot_wait_seconds", 60, min_value=1, max_value=600)
        slot_ttl = self._runtime_int("api_slot_ttl_seconds", 120, min_value=10, max_value=3600)
        endpoint_slot_id = str(endpoint.endpoint_id or f"{endpoint.provider}:{endpoint.model}").strip()
        slot_no = kb_store.acquire_api_request_slot(
            endpoint_id=endpoint_slot_id,
            holder_id=slot_holder,
            tenant_id=tenant_id,
            task_category=task_category,
            max_slots=slot_limit,
            wait_seconds=slot_wait,
            ttl_seconds=slot_ttl,
        )
        url = endpoint.base_url.rstrip("/") + "/embeddings"
        payload = {
            "model": endpoint.model,
            "input": text,
            "encoding_format": "float",
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=40.0) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                body = resp.json()
            self._record_api_usage(
                tenant_id=tenant_id,
                kb_id=kb_id,
                doc_id=doc_id,
                pool=pool,
                task_category=task_category,
                endpoint=endpoint,
                api_key=api_key,
                api_path="/embeddings",
                success=True,
                usage_tokens=self._parse_usage_tokens(body),
            )
        except Exception as exc:
            self._record_api_usage(
                tenant_id=tenant_id,
                kb_id=kb_id,
                doc_id=doc_id,
                pool=pool,
                task_category=task_category,
                endpoint=endpoint,
                api_key=api_key,
                api_path="/embeddings",
                success=False,
                usage_tokens={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                error_reason=str(exc),
            )
            raise
        finally:
            kb_store.release_api_request_slot(endpoint_id=endpoint_slot_id, slot_no=slot_no, holder_id=slot_holder)
        data = body.get("data", [])
        if not data:
            raise ValueError("Empty embedding data")
        vector = data[0].get("embedding", [])
        if not vector:
            raise ValueError("Embedding vector missing")
        return [float(x) for x in vector]

    def embed_text(
        self,
        tenant_id: str,
        text: str,
        *,
        pool: str = "main",
        task: str = "embedding",
        strict: bool = False,
        local_dimensions: int = 256,
        kb_id: str = "",
        doc_id: str = "",
    ) -> list[float]:
        endpoint = self._resolve_embedding_endpoint(tenant_id=tenant_id, pool=pool, task=task)
        if not endpoint or not endpoint.enabled:
            if strict:
                raise RuntimeError(f"No endpoint configured for pool={pool}, task={task}")
            return self._local_embedding(text, local_dimensions)
        try:
            if endpoint.provider in {"siliconflow", "openai", "openai_compatible"}:
                return self._embed_openai_compatible(
                    endpoint,
                    text,
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    pool=pool,
                    task_category=task,
                )
            raise RuntimeError(f"Unsupported embedding provider: {endpoint.provider}")
        except Exception:
            if strict:
                raise
            return self._local_embedding(text, local_dimensions)

    def _chat_openai_compatible(
        self,
        endpoint: ApiEndpoint,
        messages: list[dict[str, str]],
        *,
        tenant_id: str,
        kb_id: str,
        doc_id: str,
        pool: str,
        task_category: str,
        temperature: float = 0.2,
        max_tokens: int = 600,
    ) -> str:
        api_key = self._endpoint_api_key(endpoint)
        if not api_key:
            raise RuntimeError(f"Missing API key for endpoint {endpoint.endpoint_id}")
        slot_holder = str(uuid.uuid4())
        slot_limit = self._runtime_int("api_slot_max_per_endpoint", 8, min_value=1, max_value=64)
        slot_wait = self._runtime_int("api_slot_wait_seconds", 60, min_value=1, max_value=600)
        slot_ttl = self._runtime_int("api_slot_ttl_seconds", 120, min_value=10, max_value=3600)
        endpoint_slot_id = str(endpoint.endpoint_id or f"{endpoint.provider}:{endpoint.model}").strip()
        slot_no = kb_store.acquire_api_request_slot(
            endpoint_id=endpoint_slot_id,
            holder_id=slot_holder,
            tenant_id=tenant_id,
            task_category=task_category,
            max_slots=slot_limit,
            wait_seconds=slot_wait,
            ttl_seconds=slot_ttl,
        )
        url = endpoint.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": endpoint.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                body = resp.json()
            self._record_api_usage(
                tenant_id=tenant_id,
                kb_id=kb_id,
                doc_id=doc_id,
                pool=pool,
                task_category=task_category,
                endpoint=endpoint,
                api_key=api_key,
                api_path="/chat/completions",
                success=True,
                usage_tokens=self._parse_usage_tokens(body),
            )
        except Exception as exc:
            self._record_api_usage(
                tenant_id=tenant_id,
                kb_id=kb_id,
                doc_id=doc_id,
                pool=pool,
                task_category=task_category,
                endpoint=endpoint,
                api_key=api_key,
                api_path="/chat/completions",
                success=False,
                usage_tokens={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                error_reason=str(exc),
            )
            raise
        finally:
            kb_store.release_api_request_slot(endpoint_id=endpoint_slot_id, slot_no=slot_no, holder_id=slot_holder)
        choices = body.get("choices", [])
        if not choices:
            raise ValueError("Empty completion choices")
        content = (((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        if not content:
            raise ValueError("Empty completion content")
        return content

    def chat_completion(
        self,
        tenant_id: str,
        messages: list[dict[str, str]],
        *,
        pool: str = "main",
        task: str = "llm",
        strict: bool = False,
        temperature: float = 0.2,
        max_tokens: int = 600,
        kb_id: str = "",
        doc_id: str = "",
    ) -> dict[str, Any]:
        endpoint = self._resolve_endpoint(tenant_id=tenant_id, pool=pool, task=task)
        if not endpoint or not endpoint.enabled:
            if strict:
                raise RuntimeError(f"No endpoint configured for pool={pool}, task={task}")
            return {
                "text": "",
                "endpoint": {"endpoint_id": "none", "provider": "none", "model": "none"},
            }
        if endpoint.provider == "mock":
            if strict:
                raise RuntimeError("mock endpoint is disabled in strict mode")
            return {
                "text": json.dumps({"overview": "mock overview", "summary": "mock summary"}, ensure_ascii=False),
                "endpoint": {
                    "endpoint_id": endpoint.endpoint_id,
                    "provider": endpoint.provider,
                    "model": endpoint.model,
                },
            }
        try:
            if endpoint.provider in {"siliconflow", "openai", "openai_compatible"}:
                text = self._chat_openai_compatible(
                    endpoint=endpoint,
                    messages=messages,
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    pool=pool,
                    task_category=task,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return {
                    "text": text,
                    "endpoint": {
                        "endpoint_id": endpoint.endpoint_id,
                        "provider": endpoint.provider,
                        "model": endpoint.model,
                    },
                }
            raise RuntimeError(f"Unsupported LLM provider: {endpoint.provider}")
        except Exception:
            if strict:
                raise
            return {
                "text": "",
                "endpoint": {
                    "endpoint_id": endpoint.endpoint_id,
                    "provider": endpoint.provider,
                    "model": endpoint.model,
                },
            }

    def _extract_json(self, raw: str) -> dict[str, Any]:
        text = (raw or "").strip()
        if not text:
            return {}
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
        if fence:
            candidate = (fence.group(1) or "").strip()
            if candidate:
                try:
                    parsed = json.loads(candidate)
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    pass
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}

    def _coerce_text(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (list, tuple)):
            parts = [self._coerce_text(v) for v in value]
            parts = [p for p in parts if p]
            return "；".join(parts).strip()
        if isinstance(value, dict):
            try:
                return json.dumps(value, ensure_ascii=False).strip()
            except Exception:
                return str(value).strip()
        return str(value).strip()

    def _pick_text(self, data: dict[str, Any], keys: list[str]) -> str:
        lowered = {str(k).strip().lower(): v for k, v in data.items()}
        for key in keys:
            if key in data:
                text = self._coerce_text(data.get(key))
                if text:
                    return text
            text = self._coerce_text(lowered.get(str(key).strip().lower()))
            if text:
                return text
        return ""

    def _repair_summary_json(
        self,
        tenant_id: str,
        raw_text: str,
        *,
        mode: str,
        max_tokens: int = 360,
    ) -> dict[str, Any]:
        repair_max_tokens = self._runtime_int("summary_repair_max_tokens", max_tokens, min_value=120, max_value=2400)
        summary_min_chars = self._runtime_int("summary_min_chars", 120, min_value=1, max_value=3000)
        summary_max_chars = self._runtime_int("summary_max_chars", 900, min_value=summary_min_chars, max_value=6000)
        overview_max_chars = self._runtime_int("summary_overview_max_chars", 80, min_value=20, max_value=300)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个JSON修复助手。"
                    "请把输入内容整理为严格 JSON："
                    f'{{"overview":"一句话概述（1-{overview_max_chars}字）","summary":"摘要（{summary_min_chars}-{summary_max_chars}字）"}}'
                ),
            },
            {
                "role": "user",
                "content": f"模式：{mode}\n请仅输出 JSON，不要输出其他文字。\n输入：\n{(raw_text or '')[:6000]}",
            },
        ]
        resp = self.chat_completion(
            tenant_id=tenant_id,
            messages=messages,
            pool="aux",
            task="summary_llm",
            strict=True,
            temperature=0.0,
            max_tokens=repair_max_tokens,
        )
        return self._extract_json(resp.get("text", ""))

    def _hard_split_text(self, text: str, max_len: int) -> list[str]:
        value = (text or "").strip()
        if not value:
            return []
        if len(value) <= max_len:
            return [value]
        out: list[str] = []
        start = 0
        while start < len(value):
            part = value[start : start + max_len].strip()
            if part:
                out.append(part)
            start += max_len
        return out

    def _split_summary_units(self, text: str, unit_max_chars: int) -> list[str]:
        value = (text or "").strip()
        if not value:
            return []
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", value) if p and p.strip()]
        if not paragraphs:
            paragraphs = [value]
        units: list[str] = []
        for para in paragraphs:
            if len(para) <= unit_max_chars:
                units.append(para)
                continue
            sentences = [s.strip() for s in re.split(r"(?<=[。！？!?；;])\s+|(?<=\.)\s+", para) if s and s.strip()]
            if not sentences:
                sentences = [para]
            buffer = ""
            for sent in sentences:
                if len(sent) > unit_max_chars:
                    if buffer:
                        units.append(buffer)
                        buffer = ""
                    units.extend(self._hard_split_text(sent, unit_max_chars))
                    continue
                candidate = f"{buffer} {sent}".strip() if buffer else sent
                if buffer and len(candidate) > unit_max_chars:
                    units.append(buffer)
                    buffer = sent
                else:
                    buffer = candidate
            if buffer:
                units.append(buffer)
        return [u for u in units if u]

    def _chunk_text_for_summary(self, text: str, chunk_size: int = 1600, overlap: int = 120, max_chunks: int = 48) -> list[str]:
        value = (text or "").strip()
        if not value:
            return []
        if len(value) <= chunk_size:
            return [value]
        stable_enabled = self._runtime_bool("summary_segment_stable", True)
        if stable_enabled:
            unit_max_chars = self._runtime_int(
                "summary_segment_unit_max_chars",
                min(max(240, chunk_size), 2200),
                min_value=120,
                max_value=8000,
            )
            units = self._split_summary_units(value, unit_max_chars=unit_max_chars)
            if units:
                chunks: list[str] = []
                current: list[str] = []
                current_len = 0
                for unit in units:
                    add_len = len(unit) + (1 if current else 0)
                    if current and (current_len + add_len) > chunk_size:
                        piece = "\n".join(current).strip()
                        if piece:
                            chunks.append(piece)
                        if len(chunks) >= max_chunks:
                            return chunks
                        carry: list[str] = []
                        carry_len = 0
                        if overlap > 0:
                            for item in reversed(current):
                                item_len = len(item) + (1 if carry else 0)
                                if carry and (carry_len + item_len) > overlap:
                                    break
                                carry.insert(0, item)
                                carry_len += item_len
                                if carry_len >= overlap:
                                    break
                        current = carry
                        current_len = len("\n".join(current)) if current else 0
                    if current:
                        current.append(unit)
                        current_len += len(unit) + 1
                    else:
                        current = [unit]
                        current_len = len(unit)
                if current and len(chunks) < max_chunks:
                    piece = "\n".join(current).strip()
                    if piece:
                        chunks.append(piece)
                if chunks:
                    return chunks[:max_chunks]
        step = max(1, chunk_size - max(0, overlap))
        chunks: list[str] = []
        start = 0
        while start < len(value) and len(chunks) < max_chunks:
            piece = value[start : start + chunk_size].strip()
            if piece:
                chunks.append(piece)
            start += step
        return chunks

    def _progress_emit(
        self,
        progress_cb: Callable[[str, int, int], None] | None,
        stage: str,
        current: int,
        total: int,
    ) -> None:
        if not progress_cb:
            return
        try:
            progress_cb(stage, int(current), int(total))
        except Exception:
            return

    def _check_cancel(self, should_cancel: Callable[[], bool] | None = None) -> None:
        if not should_cancel:
            return
        try:
            if should_cancel():
                raise RuntimeError("TASK_CANCELLED")
        except RuntimeError:
            raise
        except Exception:
            return

    def _summarize_segment(
        self,
        tenant_id: str,
        title: str,
        segment_text: str,
        kb_id: str = "",
        doc_id: str = "",
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        self._check_cancel(should_cancel)
        summary_min_chars = self._runtime_int("summary_min_chars", 120, min_value=1, max_value=3000)
        summary_max_chars = self._runtime_int("summary_max_chars", 900, min_value=summary_min_chars, max_value=6000)
        overview_max_chars = self._runtime_int("summary_overview_max_chars", 80, min_value=20, max_value=300)
        segment_max_tokens = self._runtime_int("summary_segment_max_tokens", 900, min_value=200, max_value=3200)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个文档总结助手。"
                    "现在给你一些文本片段，请提炼其中的有效信息。"
                    "请输出严格 JSON："
                    f'{{"overview":"一句话概述（1-{overview_max_chars}字）","summary":"摘要（{summary_min_chars}-{summary_max_chars}字）"}}'
                ),
            },
            {
                "role": "user",
                "content": f"标题：{title}\n请总结以下内容片段：\n{segment_text}",
            },
        ]
        resp = self.chat_completion(
            tenant_id=tenant_id,
            messages=messages,
            pool="aux",
            task="summary_llm",
            strict=True,
            temperature=0.1,
            max_tokens=segment_max_tokens,
            kb_id=kb_id,
            doc_id=doc_id,
        )
        self._check_cancel(should_cancel)
        data = self._extract_json(resp.get("text", ""))
        overview = self._pick_text(data, self.OVERVIEW_KEYS)
        summary = self._pick_text(data, self.SUMMARY_KEYS)
        if not overview or not summary:
            repaired = self._repair_summary_json(tenant_id, resp.get("text", ""), mode="segment")
            overview = overview or self._pick_text(repaired, self.OVERVIEW_KEYS)
            summary = summary or self._pick_text(repaired, self.SUMMARY_KEYS)
        if not overview:
            raise ValueError("summary_llm returned empty 'overview' for segment")
        if not summary:
            raise ValueError("summary_llm returned empty 'summary' for segment")
        return {"overview": overview, "summary": summary}

    def summarize_document(
        self,
        tenant_id: str,
        filename: str,
        text: str,
        progress_cb: Callable[[str, int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        kb_id: str = "",
        doc_id: str = "",
    ) -> dict[str, Any]:
        seg_chunk_size = self._runtime_int("summary_segment_chars", 1600, min_value=300, max_value=8000)
        seg_overlap = self._runtime_int("summary_segment_overlap", 120, min_value=0, max_value=1200)
        seg_max_chunks = self._runtime_int("summary_segment_max_chunks", 48, min_value=1, max_value=200)
        seg_concurrency = self._runtime_int("summary_segment_concurrency", 3, min_value=1, max_value=16)
        summary_min_chars = self._runtime_int("summary_min_chars", 120, min_value=1, max_value=3000)
        summary_max_chars = self._runtime_int("summary_max_chars", 900, min_value=summary_min_chars, max_value=6000)
        overview_max_chars = self._runtime_int("summary_overview_max_chars", 80, min_value=20, max_value=300)
        doc_max_tokens = self._runtime_int("summary_doc_max_tokens", 1200, min_value=300, max_value=4000)

        segments = self._chunk_text_for_summary(
            text,
            chunk_size=seg_chunk_size,
            overlap=seg_overlap,
            max_chunks=seg_max_chunks,
        )
        if not segments:
            segments = [(text or "")[:12000]]
        seg_total = max(1, len(segments))
        self._progress_emit(progress_cb, "SUMMARIZING_DOC", 0, seg_total)
        self._check_cancel(should_cancel)
        summary_llm_ep = self._resolve_endpoint(tenant_id=tenant_id, pool="aux", task="summary_llm")
        summary_model = str((summary_llm_ep.model if summary_llm_ep else "") or "")
        signature_payload = {
            "text_sha": hashlib.sha256((text or "").encode("utf-8")).hexdigest(),
            "seg_chunk_size": seg_chunk_size,
            "seg_overlap": seg_overlap,
            "seg_max_chunks": seg_max_chunks,
            "seg_stable": self._runtime_bool("summary_segment_stable", True),
            "seg_unit_max_chars": self._runtime_int("summary_segment_unit_max_chars", min(max(240, seg_chunk_size), 2200), min_value=120, max_value=8000),
            "summary_model": summary_model,
        }
        doc_signature = hashlib.sha256(json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        cached_by_index: dict[int, dict[str, Any]] = {}
        cached_by_hash: dict[str, dict[str, Any]] = {}
        use_cache = bool(kb_id and doc_id)
        if use_cache:
            try:
                for row in kb_store.get_summary_segment_cache(
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    doc_signature=doc_signature,
                ):
                    cached_by_index[int(row.get("segment_index") or -1)] = row
                cached_by_hash = kb_store.get_summary_segment_cache_latest_by_hash(
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                )
            except Exception:
                cached_by_index = {}
                cached_by_hash = {}

        layer1_map: dict[int, str] = {}
        # When cancellation is enabled, prefer interruptible sequential execution
        # instead of launching many concurrent requests up-front.
        if should_cancel:
            seg_concurrency = 1

        if seg_total == 1 or seg_concurrency <= 1:
            for idx, seg in enumerate(segments, start=1):
                self._check_cancel(should_cancel)
                seg_hash = hashlib.sha256((seg or "").encode("utf-8")).hexdigest()
                cached = cached_by_index.get(idx)
                if cached and str(cached.get("segment_hash") or "") == seg_hash and str(cached.get("status") or "").upper() == "DONE":
                    part = {
                        "overview": str(cached.get("overview") or ""),
                        "summary": str(cached.get("summary") or ""),
                    }
                elif str((cached_by_hash.get(seg_hash) or {}).get("status") or "").upper() == "DONE":
                    reused = cached_by_hash.get(seg_hash) or {}
                    part = {
                        "overview": str(reused.get("overview") or ""),
                        "summary": str(reused.get("summary") or ""),
                    }
                else:
                    part = self._summarize_segment(
                        tenant_id,
                        f"{filename} - 第{idx}段",
                        seg,
                        kb_id=kb_id,
                        doc_id=doc_id,
                        should_cancel=should_cancel,
                    )
                    if use_cache:
                        cached_by_hash[seg_hash] = {
                            "segment_hash": seg_hash,
                            "overview": part["overview"],
                            "summary": part["summary"],
                            "status": "DONE",
                        }
                if use_cache:
                    kb_store.upsert_summary_segment_cache(
                        tenant_id=tenant_id,
                        kb_id=kb_id,
                        doc_id=doc_id,
                        doc_signature=doc_signature,
                        segment_index=idx,
                        segment_hash=seg_hash,
                        overview=part["overview"],
                        summary=part["summary"],
                        status="DONE",
                    )
                layer1_map[idx] = f"[段{idx}] 结论：{part['overview']}\n要点：{part['summary']}"
                self._progress_emit(progress_cb, "SUMMARIZING_DOC", idx, seg_total)
        else:
            workers = min(seg_concurrency, seg_total)
            done = 0
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="summary-seg") as executor:
                future_map = {}
                for idx, seg in enumerate(segments, start=1):
                    seg_hash = hashlib.sha256((seg or "").encode("utf-8")).hexdigest()
                    cached = cached_by_index.get(idx)
                    if cached and str(cached.get("segment_hash") or "") == seg_hash and str(cached.get("status") or "").upper() == "DONE":
                        layer1_map[idx] = f"[段{idx}] 结论：{str(cached.get('overview') or '')}\n要点：{str(cached.get('summary') or '')}"
                        done += 1
                        self._progress_emit(progress_cb, "SUMMARIZING_DOC", done, seg_total)
                        continue
                    reused = cached_by_hash.get(seg_hash) if use_cache else None
                    if reused and str(reused.get("status") or "").upper() == "DONE":
                        part_overview = str(reused.get("overview") or "")
                        part_summary = str(reused.get("summary") or "")
                        layer1_map[idx] = f"[段{idx}] 结论：{part_overview}\n要点：{part_summary}"
                        if use_cache:
                            kb_store.upsert_summary_segment_cache(
                                tenant_id=tenant_id,
                                kb_id=kb_id,
                                doc_id=doc_id,
                                doc_signature=doc_signature,
                                segment_index=idx,
                                segment_hash=seg_hash,
                                overview=part_overview,
                                summary=part_summary,
                                status="DONE",
                            )
                        done += 1
                        self._progress_emit(progress_cb, "SUMMARIZING_DOC", done, seg_total)
                        continue
                    future = executor.submit(
                        self._summarize_segment,
                        tenant_id,
                        f"{filename} - 第{idx}段",
                        seg,
                        kb_id,
                        doc_id,
                        should_cancel,
                    )
                    future_map[future] = (idx, seg_hash)
                for future in as_completed(future_map):
                    self._check_cancel(should_cancel)
                    idx, seg_hash = future_map[future]
                    part = future.result()
                    layer1_map[idx] = f"[段{idx}] 结论：{part['overview']}\n要点：{part['summary']}"
                    if use_cache:
                        kb_store.upsert_summary_segment_cache(
                            tenant_id=tenant_id,
                            kb_id=kb_id,
                            doc_id=doc_id,
                            doc_signature=doc_signature,
                            segment_index=idx,
                            segment_hash=seg_hash,
                            overview=part["overview"],
                            summary=part["summary"],
                            status="DONE",
                        )
                        cached_by_hash[seg_hash] = {
                            "segment_hash": seg_hash,
                            "overview": part["overview"],
                            "summary": part["summary"],
                            "status": "DONE",
                        }
                    done += 1
                    self._progress_emit(progress_cb, "SUMMARIZING_DOC", done, seg_total)

        layer1 = [layer1_map[i] for i in sorted(layer1_map)]

        merged_layer1 = "\n\n".join(layer1)[:16000]
        self._check_cancel(should_cancel)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个文档总结助手。"
                    "现在给你一些文本片段，请提炼其中的有效信息。"
                    "请输出严格 JSON："
                    f'{{"overview":"一句话概述（1-{overview_max_chars}字）","summary":"摘要（{summary_min_chars}-{summary_max_chars}字）"}}'
                ),
            },
            {
                "role": "user",
                "content": f"文件名：{filename}\n以下是分段摘要，请汇总为文档级总结：\n{merged_layer1}",
            },
        ]
        resp = self.chat_completion(
            tenant_id=tenant_id,
            messages=messages,
            pool="aux",
            task="summary_llm",
            strict=True,
            temperature=0.1,
            max_tokens=doc_max_tokens,
            kb_id=kb_id,
            doc_id=doc_id,
        )
        self._check_cancel(should_cancel)
        data = self._extract_json(resp.get("text", ""))
        overview = self._pick_text(data, self.OVERVIEW_KEYS)
        summary = self._pick_text(data, self.SUMMARY_KEYS)
        if not overview or not summary:
            repaired = self._repair_summary_json(tenant_id, resp.get("text", ""), mode="document")
            overview = overview or self._pick_text(repaired, self.OVERVIEW_KEYS)
            summary = summary or self._pick_text(repaired, self.SUMMARY_KEYS)
        if not overview:
            raise ValueError("summary_llm returned empty 'overview' for document")
        if not summary:
            raise ValueError("summary_llm returned empty 'summary' for document")
        self._progress_emit(progress_cb, "SUMMARY_EMBEDDING_DOC", 0, 1)
        self._check_cancel(should_cancel)
        emb = self.embed_text(
            tenant_id=tenant_id,
            text=f"{overview}\n{summary}",
            pool="aux",
            task="summary_embedding",
            strict=True,
            local_dimensions=256,
            kb_id=kb_id,
            doc_id=doc_id,
        )
        self._progress_emit(progress_cb, "SUMMARY_EMBEDDING_DOC", 1, 1)
        return {"overview": overview, "summary": summary, "embedding": emb, "endpoint": resp.get("endpoint", {})}

    def summarize_kb(
        self,
        tenant_id: str,
        kb_id: str,
        kb_name: str,
        doc_summaries: list[dict[str, str]],
        progress_cb: Callable[[str, int, int], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        kb_batch_size = self._runtime_int("summary_kb_batch_size", 20, min_value=5, max_value=200)
        kb_batch_max_tokens = self._runtime_int("summary_kb_batch_max_tokens", 900, min_value=300, max_value=4000)
        kb_final_max_tokens = self._runtime_int("summary_kb_final_max_tokens", 1400, min_value=400, max_value=6000)
        summary_min_chars = self._runtime_int("summary_min_chars", 120, min_value=1, max_value=3000)
        summary_max_chars = self._runtime_int("summary_max_chars", 900, min_value=summary_min_chars, max_value=6000)
        kb_summary_max_chars = self._runtime_int("summary_kb_max_chars", 1200, min_value=summary_min_chars, max_value=8000)
        overview_max_chars = self._runtime_int("summary_overview_max_chars", 80, min_value=20, max_value=300)
        lines = []
        for item in doc_summaries[:120]:
            lines.append(f"- {item.get('filename','unknown')}: {item.get('overview','')}\n{item.get('summary','')}")

        # Layer 1: summarize document summaries in batches.
        batch_size = kb_batch_size
        batch_outputs: list[str] = []
        batch_total = max(1, math.ceil(len(lines) / batch_size)) if lines else 1
        self._progress_emit(progress_cb, "SUMMARIZING_KB", 0, batch_total)
        self._check_cancel(should_cancel)
        for i in range(0, len(lines), batch_size):
            batch = lines[i : i + batch_size]
            if not batch:
                continue
            self._check_cancel(should_cancel)
            batch_no = (i // batch_size) + 1
            batch_text = "\n".join(batch)[:12000]
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个文档总结助手。"
                        "现在给你一些文本片段，请提炼其中的有效信息。"
                        "请输出严格 JSON："
                        f'{{"overview":"该批次核心概述（1-{overview_max_chars}字）","summary":"批次摘要（{summary_min_chars}-{summary_max_chars}字）"}}'
                    ),
                },
                {
                    "role": "user",
                    "content": f"知识库：{kb_name}\n请总结这一批文档摘要：\n{batch_text}",
                },
            ]
            batch_resp = self.chat_completion(
                tenant_id=tenant_id,
                messages=messages,
                pool="aux",
                task="summary_llm",
                strict=True,
                temperature=0.1,
                max_tokens=kb_batch_max_tokens,
                kb_id=kb_id,
                doc_id="",
            )
            self._check_cancel(should_cancel)
            batch_data = self._extract_json(batch_resp.get("text", ""))
            batch_overview = self._pick_text(batch_data, self.OVERVIEW_KEYS)
            batch_summary = self._pick_text(batch_data, self.SUMMARY_KEYS)
            if not batch_overview or not batch_summary:
                repaired = self._repair_summary_json(tenant_id, batch_resp.get("text", ""), mode=f"kb_batch_{batch_no}")
                batch_overview = batch_overview or self._pick_text(repaired, self.OVERVIEW_KEYS)
                batch_summary = batch_summary or self._pick_text(repaired, self.SUMMARY_KEYS)
            if not batch_overview:
                raise ValueError(f"summary_llm returned empty 'overview' for kb batch {batch_no}")
            if not batch_summary:
                raise ValueError(f"summary_llm returned empty 'summary' for kb batch {batch_no}")
            batch_outputs.append(f"[批次{batch_no}] 结论：{batch_overview}\n要点：{batch_summary}")
            self._progress_emit(progress_cb, "SUMMARIZING_KB", batch_no, batch_total)

        prompt = "\n\n".join(batch_outputs or lines)[:18000]
        self._check_cancel(should_cancel)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个知识库总结助手。"
                    "现在给你一些文档摘要片段，请提炼其中的有效信息。"
                    "请输出严格 JSON："
                    f'{{"overview":"知识库一句话概述（1-{overview_max_chars}字）","summary":"知识库摘要（{summary_min_chars}-{kb_summary_max_chars}字）"}}'
                ),
            },
            {
                "role": "user",
                "content": f"知识库：{kb_name}\n以下是批次摘要/文档摘要集合，请生成知识库级总结：\n{prompt}",
            },
        ]
        resp = self.chat_completion(
            tenant_id=tenant_id,
            messages=messages,
            pool="aux",
            task="summary_llm",
            strict=True,
            temperature=0.1,
            max_tokens=kb_final_max_tokens,
            kb_id=kb_id,
            doc_id="",
        )
        self._check_cancel(should_cancel)
        data = self._extract_json(resp.get("text", ""))
        overview = self._pick_text(data, self.OVERVIEW_KEYS)
        summary = self._pick_text(data, self.SUMMARY_KEYS)
        if not overview or not summary:
            repaired = self._repair_summary_json(tenant_id, resp.get("text", ""), mode="kb")
            overview = overview or self._pick_text(repaired, self.OVERVIEW_KEYS)
            summary = summary or self._pick_text(repaired, self.SUMMARY_KEYS)
        if not overview:
            raise ValueError("summary_llm returned empty 'overview' for kb")
        if not summary:
            raise ValueError("summary_llm returned empty 'summary' for kb")
        self._progress_emit(progress_cb, "SUMMARY_EMBEDDING_KB", 0, 1)
        self._check_cancel(should_cancel)
        emb = self.embed_text(
            tenant_id=tenant_id,
            text=f"{overview}\n{summary}",
            pool="aux",
            task="summary_embedding",
            strict=True,
            local_dimensions=256,
            kb_id=kb_id,
            doc_id="",
        )
        self._progress_emit(progress_cb, "SUMMARY_EMBEDDING_KB", 1, 1)
        return {"overview": overview, "summary": summary, "embedding": emb, "endpoint": resp.get("endpoint", {})}


ai_gateway = AiGateway()
