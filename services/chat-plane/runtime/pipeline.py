from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional, TypedDict

from langgraph.graph import END, StateGraph

from .llm_client import llm_client
from .llm_client import LlmStreamTruncatedError
from .config import get_setting
from .memory import MemoryConfig, get_memory_config
from .models import ChatContext, NodeResult
from .plugins import node_registry
from .retrieval_client import search_kb
from .session_vector_store import session_vector_store
from .store import chat_store


class ChatGraphState(TypedDict):
    ctx: ChatContext
    done: int
    total: int
    cancelled: bool


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _retrieval_doc_name(item: Dict[str, Any]) -> str:
    return str(
        item.get("filename")
        or item.get("document_name")
        or item.get("doc_name")
        or item.get("source")
        or item.get("doc_id")
        or "-"
    ).strip() or "-"


def _retrieval_preview_item(item: Dict[str, Any], text_limit: int = 360) -> Dict[str, Any]:
    text = str(item.get("text") or item.get("chunk_text") or "")
    doc_name = _retrieval_doc_name(item)
    return {
        "doc_id": str(item.get("doc_id") or ""),
        "chunk_id": str(item.get("chunk_id") or ""),
        "chunk_index": item.get("chunk_index"),
        "filename": doc_name,
        "document_name": doc_name,
        "source": doc_name,
        "score": item.get("score"),
        "scores": dict(item.get("scores") or {}),
        "rank_method": str(item.get("rank_method") or ""),
        "index_revision": str(item.get("index_revision") or ""),
        "matched_text_preview": str(item.get("matched_text") or "")[:text_limit],
        "context_chunk_ids": list(item.get("context_chunk_ids") or []),
        "context_range": list(item.get("context_range") or []),
        "context_window": item.get("context_window", 0),
        "text_preview": text[:text_limit],
    }


class NormalizeNode:
    plugin_id = "normalize"

    async def run(self, ctx: ChatContext) -> NodeResult:
        question = ctx.request.message.strip()
        ctx.request.message = question
        history = chat_store.list_messages(ctx.session.session_id, limit=60)
        ctx.messages = history
        return NodeResult(
            stage="NORMALIZED",
            info={
                "history_messages": str(len(history)),
                "question_chars": str(len(question)),
            },
        )


class RetrievalNode:
    plugin_id = "retrieval"

    @staticmethod
    def _clip(text: Any, limit: int = 1200) -> str:
        value = str(text or "").strip()
        if len(value) <= limit:
            return value
        return value[:limit] + f"\n... truncated {len(value) - limit} chars"

    @staticmethod
    def _json_obj(raw: str) -> Dict[str, Any]:
        text = str(raw or "").strip()
        if not text:
            return {}
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            text = match.group(0)
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    async def _plan_tools(self, ctx: ChatContext, *, round_no: int, previous_steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not _truthy(get_setting("chat_agent_tool_planning_enabled", False)):
            return {}
        kb_id = ctx.request.kb_id or ctx.session.kb_id or ""
        tools = [
            {
                "name": "rag_search",
                "description": "Search the mounted knowledge base for evidence chunks. Use when the answer should be grounded in uploaded documents.",
                "args": {"query": "string", "top_k": "integer 1-20"},
            },
            {
                "name": "aux_summarize",
                "description": "Use a smaller auxiliary LLM to summarize retrieved chunks or intermediate context.",
                "args": {"focus": "string"},
            },
            {"name": "final", "description": "Stop tool use and build the final answer.", "args": {}},
        ]
        prior = json.dumps(previous_steps[-6:], ensure_ascii=False)
        try:
            raw = await llm_client.complete_text(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are an agent tool planner. Return strict JSON only. "
                            "Decide the next tool call for the current user request. "
                            "Prefer rag_search when the answer needs citations, uploaded KB evidence, or factual grounding. "
                            "Use aux_summarize after retrieval when many chunks need compression. "
                            "Schema: {\"tool\":\"rag_search|aux_summarize|final\", \"query\":\"...\", \"top_k\":10, \"reason\":\"...\"}."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"mounted_kb_id={kb_id or '-'}\n"
                            f"use_kb={ctx.request.use_kb}\n"
                            f"default_top_k={ctx.request.top_k}\n"
                            f"round={round_no}\n"
                            f"rewritten_query={ctx.retrieval_query or ctx.request.message}\n"
                            f"user_request={ctx.request.message}\n"
                            f"previous_steps={prior}\n"
                            f"tools={json.dumps(tools, ensure_ascii=False)}"
                        ),
                    },
                ],
                max_tokens=260,
                temperature=0.0,
                tenant_id=ctx.tenant_id,
                task="chat_aux",
            )
            return self._json_obj(raw)
        except Exception as exc:
            return {"tool": "", "reason": f"planner_failed: {str(exc)[:180]}"}

    async def _summarize_retrieval(self, ctx: ChatContext, focus: str) -> str:
        if not ctx.retrieval_items:
            return ""
        max_items = max(1, min(int(get_setting("chat_agent_summary_max_chunks", 12)), 30))
        snippets = []
        for idx, item in enumerate(ctx.retrieval_items[:max_items], start=1):
            text = self._clip(item.get("text") or item.get("chunk_text") or "", 1200)
            source = _retrieval_doc_name(item)
            snippets.append(
                f"[{idx}] document={source} doc_id={item.get('doc_id', '-')} "
                f"chunk={item.get('chunk_id', '-')} score={item.get('score', '-')}\n{text}"
            )
        try:
            summary = await llm_client.complete_text(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是辅助总结工具。只总结给定检索片段，保留可用于最终回答的证据点、来源编号和限制。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"总结目标：{focus or ctx.request.message}\n\n检索片段：\n" + "\n\n".join(snippets),
                    },
                ],
                max_tokens=max(160, min(int(get_setting("chat_agent_summary_max_tokens", 700)), 2000)),
                temperature=0.0,
                tenant_id=ctx.tenant_id,
                task="chat_aux",
            )
            return summary.strip()
        except Exception:
            return ""

    @staticmethod
    def _norm_keyword_score(v: float) -> float:
        return max(0.0, min(1.0, float(v) / 5.0))

    @staticmethod
    def _norm_vector_score(v: float) -> float:
        fv = float(v)
        if fv < 0.0:
            fv = (fv + 1.0) / 2.0
        return max(0.0, min(1.0, fv))

    @staticmethod
    def _parse_iso_ts(value: Any) -> Optional[datetime]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        except Exception:
            return None

    def _recency_score(self, updated_at: Any) -> float:
        ts = self._parse_iso_ts(updated_at)
        if ts is None:
            return 0.5
        now = datetime.now(timezone.utc)
        age_seconds = max(0.0, (now - ts).total_seconds())
        age_days = age_seconds / 86400.0
        half_life_days = float(get_setting("memory_session_fusion_recency_half_life_days", 7.0))
        half_life_days = max(0.1, half_life_days)
        return max(0.0, min(1.0, 0.5 ** (age_days / half_life_days)))

    async def run(self, ctx: ChatContext) -> NodeResult:
        retrieval_query = str(ctx.retrieval_query or ctx.request.message or "").strip()
        if not retrieval_query:
            retrieval_query = str(ctx.request.message or "").strip()
        session_rag_enabled = str(get_setting("memory_session_rag_enabled", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if session_rag_enabled:
            keyword_items = chat_store.search_session_segments(
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                query=retrieval_query,
                limit=int(get_setting("memory_session_keyword_top_k", 4)),
                session_id=ctx.session.session_id,
            )
            vector_items: List[Dict[str, Any]] = []
            session_vector_enabled = str(
                get_setting("memory_session_vector_enabled", "true")
            ).strip().lower() in {"1", "true", "yes", "on"}
            if session_vector_enabled:
                try:
                    qvec = await llm_client.embed_text(retrieval_query, tenant_id=ctx.tenant_id)
                    vector_items = await session_vector_store.search(
                        qvec,
                        ctx.tenant_id,
                        ctx.actor_id,
                        limit=int(get_setting("memory_session_vector_top_k", 4)),
                        session_id=ctx.session.session_id,
                    )
                except Exception:
                    vector_items = []
            keyword_comp_items = chat_store.search_session_segments_by_keywords(
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                query=retrieval_query,
                limit=max(1, int(get_setting("memory_keyword_top_k", 4))),
                session_id=ctx.session.session_id,
            )
            merged: Dict[str, Dict[str, Any]] = {}
            weight_keyword = float(get_setting("memory_session_fusion_weight_keyword", 0.55))
            weight_vector = float(get_setting("memory_session_fusion_weight_vector", 0.35))
            weight_recency = float(get_setting("memory_session_fusion_weight_recency", 0.10))
            hybrid_bonus = float(get_setting("memory_session_fusion_hybrid_bonus", 0.05))
            for item in keyword_items:
                sid = str(item.get("segment_id") or "")
                if not sid:
                    continue
                one = dict(item)
                one["source"] = one.get("source") or "keyword"
                one["keyword_score_raw"] = float(one.get("score") or 0.0)
                one["vector_score_raw"] = 0.0
                merged[sid] = one
            for item in vector_items:
                sid = str(item.get("segment_id") or "")
                if not sid:
                    continue
                if sid in merged:
                    merged[sid]["vector_score_raw"] = max(
                        float(merged[sid].get("vector_score_raw") or 0.0),
                        float(item.get("score") or 0.0),
                    )
                    merged[sid]["source"] = "hybrid"
                    if not merged[sid].get("text") and item.get("text"):
                        merged[sid]["text"] = item.get("text")
                        merged[sid]["text_preview"] = item.get("text_preview")
                    if not merged[sid].get("updated_at") and item.get("updated_at"):
                        merged[sid]["updated_at"] = item.get("updated_at")
                else:
                    one = dict(item)
                    one["keyword_score_raw"] = 0.0
                    one["vector_score_raw"] = float(one.get("score") or 0.0)
                    merged[sid] = one
            merged_items = list(merged.values())
            for one in merged_items:
                keyword_norm = self._norm_keyword_score(float(one.get("keyword_score_raw") or 0.0))
                vector_norm = self._norm_vector_score(float(one.get("vector_score_raw") or 0.0))
                recency_norm = self._recency_score(one.get("updated_at"))
                source = str(one.get("source") or "")
                one["keyword_norm"] = round(keyword_norm, 4)
                one["vector_norm"] = round(vector_norm, 4)
                one["recency_norm"] = round(recency_norm, 4)
                one["fusion_score"] = round(
                    weight_keyword * keyword_norm
                    + weight_vector * vector_norm
                    + weight_recency * recency_norm
                    + (hybrid_bonus if source == "hybrid" else 0.0),
                    6,
                )
                one["score"] = one["fusion_score"]
            merged_items.sort(key=lambda x: float(x.get("fusion_score") or x.get("score") or 0.0), reverse=True)
            ctx.session_retrieval_items = merged_items[: int(get_setting("memory_session_rag_top_k", 4))]
            ctx.keyword_context_items = keyword_comp_items[: max(1, int(get_setting("memory_keyword_top_k", 4)))]
        else:
            ctx.session_retrieval_items = []
            ctx.keyword_context_items = []

        if not ctx.request.use_kb:
            ctx.retrieval_items = []
            return NodeResult(
                stage="RETRIEVAL_SKIPPED",
                info={"session_rag_count": str(len(ctx.session_retrieval_items))},
            )
        kb_id = ctx.request.kb_id or ctx.session.kb_id
        if not kb_id:
            ctx.retrieval_items = []
            return NodeResult(
                stage="RETRIEVAL_SKIPPED",
                info={"session_rag_count": str(len(ctx.session_retrieval_items))},
            )
        system_agent_enabled = _truthy(get_setting("chat_agent_enabled", False))
        request_agent_enabled = _truthy(ctx.request.use_agent, system_agent_enabled)
        retrieval_config = ctx.request.agent_config.retrieval if ctx.request.agent_config else None
        configured_top_k = retrieval_config.top_k if retrieval_config else ctx.request.top_k
        configured_rank_method = retrieval_config.rank_method if retrieval_config else ctx.request.rank_method
        rerank_enabled = retrieval_config.rerank_enabled if retrieval_config else ctx.request.rerank_enabled
        rerank_k = retrieval_config.rerank_k if retrieval_config else ctx.request.rerank_k
        rerank_required = retrieval_config.rerank_required if retrieval_config else ctx.request.rerank_required
        if not request_agent_enabled:
            top_k = max(1, min(int(configured_top_k or 6), 20))
            retrieval_result = await search_kb(
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                trace_id=ctx.trace_id,
                kb_id=kb_id,
                query=retrieval_query or ctx.request.message,
                top_k=top_k,
                rank_method=configured_rank_method,
                rerank_enabled=rerank_enabled,
                rerank_k=rerank_k,
                rerank_required=rerank_required,
            )
            items = list(retrieval_result.get("evidence") or [])
            ctx.retrieval_meta = dict(retrieval_result.get("retrieval") or {})
            if (
                configured_rank_method == "vector"
                and ctx.request.agent_config
                and ctx.request.agent_config.retrieval.score_threshold > 0
            ):
                threshold = ctx.request.agent_config.retrieval.score_threshold
                items = [item for item in items if float(item.get("score") or 0) >= threshold]
            ctx.retrieval_items = items
            ctx.retrieval_query = retrieval_query or ctx.request.message
            preview = []
            for item in items[:top_k]:
                preview.append(_retrieval_preview_item(item))
            return NodeResult(
                stage="RETRIEVAL_DONE",
                info={
                    "count": str(len(items)),
                    "requested_top_k": str(configured_top_k),
                    "returned_count": str(len(items)),
                    "preview_count": str(len(preview)),
                    "rank_method": str(ctx.retrieval_meta.get("effective_rank_method") or configured_rank_method),
                    "requested_rank_method": configured_rank_method,
                    "retrieval_meta": dict(ctx.retrieval_meta),
                    "kb_id": kb_id,
                    "top_k": str(configured_top_k),
                    "retrieval_query": str(ctx.retrieval_query)[:240],
                    "agent_enabled": "false",
                    "agent_tool_steps": [],
                    "agent_tool_summary_count": "0",
                    "session_rag_count": str(len(ctx.session_retrieval_items)),
                    "keyword_context_count": str(len(ctx.keyword_context_items)),
                    "preview": preview,  # type: ignore[dict-item]
                    "session_recall_breakdown": [
                        {
                            "segment_id": str(x.get("segment_id") or ""),
                            "source": str(x.get("source") or ""),
                            "fusion_score": x.get("fusion_score"),
                            "keyword_norm": x.get("keyword_norm"),
                            "vector_norm": x.get("vector_norm"),
                            "recency_norm": x.get("recency_norm"),
                            "text_preview": str(x.get("text_preview") or "")[:140],
                        }
                        for x in (ctx.session_retrieval_items or [])[:8]
                    ],
                    "keyword_context_preview": [
                        {
                            "segment_id": str(x.get("segment_id") or ""),
                            "score": x.get("score"),
                            "keyword_overlap_count": x.get("keyword_overlap_count"),
                            "text_preview": str(x.get("text_preview") or "")[:140],
                        }
                        for x in (ctx.keyword_context_items or [])[:8]
                    ],
                },
            )
        max_rounds = max(1, min(int(get_setting("chat_agent_tool_max_rounds", 3)), 8))
        allow_aux_summary = _truthy(get_setting("chat_agent_aux_summary_tool_enabled", False))
        steps: List[Dict[str, Any]] = []
        for round_no in range(1, max_rounds + 1):
            plan = await self._plan_tools(ctx, round_no=round_no, previous_steps=steps)
            tool = str(plan.get("tool") or "").strip()
            if not tool:
                tool = "rag_search" if round_no == 1 else ("aux_summarize" if allow_aux_summary and ctx.retrieval_items and not ctx.agent_tool_summaries else "final")
            reason = str(plan.get("reason") or "")
            if tool == "final":
                steps.append({"round": round_no, "tool": "final", "status": "skipped", "reason": reason or "Planner stopped tool use."})
                break
            if tool == "rag_search":
                query = str(plan.get("query") or retrieval_query or ctx.request.message).strip()
                try:
                    top_k = max(1, min(int(plan.get("top_k") or configured_top_k or 6), 20))
                except Exception:
                    top_k = max(1, min(int(configured_top_k or 6), 20))
                retrieval_result = await search_kb(
                    tenant_id=ctx.tenant_id,
                    actor_id=ctx.actor_id,
                    trace_id=ctx.trace_id,
                    kb_id=kb_id,
                    query=query,
                    top_k=top_k,
                    rank_method=configured_rank_method,
                    rerank_enabled=rerank_enabled,
                    rerank_k=rerank_k,
                    rerank_required=rerank_required,
                )
                items = list(retrieval_result.get("evidence") or [])
                ctx.retrieval_meta = dict(retrieval_result.get("retrieval") or {})
                if (
                    configured_rank_method == "vector"
                    and ctx.request.agent_config
                    and ctx.request.agent_config.retrieval.score_threshold > 0
                ):
                    threshold = ctx.request.agent_config.retrieval.score_threshold
                    items = [item for item in items if float(item.get("score") or 0) >= threshold]
                ctx.retrieval_items = items
                ctx.retrieval_query = query
                steps.append(
                    {
                        "round": round_no,
                        "tool": "rag_search",
                        "status": "completed",
                        "reason": reason,
                        "query": query[:240],
                        "top_k": top_k,
                        "returned_count": len(items),
                        "effective_rank_method": str(ctx.retrieval_meta.get("effective_rank_method") or configured_rank_method),
                        "retrieval_meta": dict(ctx.retrieval_meta),
                    }
                )
                continue
            if tool == "aux_summarize" and allow_aux_summary:
                focus = str(plan.get("query") or plan.get("focus") or ctx.request.message)
                summary = await self._summarize_retrieval(ctx, focus)
                if summary:
                    item = {"tool": "aux_summarize", "focus": focus[:240], "summary": summary}
                    ctx.agent_tool_summaries.append(item)
                steps.append(
                    {
                        "round": round_no,
                        "tool": "aux_summarize",
                        "status": "completed" if summary else "skipped",
                        "reason": reason,
                        "summary_chars": len(summary),
                    }
                )
                continue
            steps.append({"round": round_no, "tool": tool or "-", "status": "skipped", "reason": reason or "Unknown or disabled tool."})
            break
        ctx.agent_tool_steps = steps
        items = ctx.retrieval_items or []
        preview = []
        preview_limit = max(1, min(int(ctx.request.top_k or 6), 20))
        for item in items[:preview_limit]:
            preview.append(_retrieval_preview_item(item))
        return NodeResult(
            stage="RETRIEVAL_DONE",
            info={
                "count": str(len(items)),
                "requested_top_k": str(ctx.request.top_k),
                "returned_count": str(len(items)),
                "preview_count": str(len(preview)),
                "rank_method": str(ctx.retrieval_meta.get("effective_rank_method") or ctx.request.rank_method),
                "requested_rank_method": ctx.request.rank_method,
                "retrieval_meta": dict(ctx.retrieval_meta),
                "kb_id": kb_id,
                "top_k": str(ctx.request.top_k),
                "retrieval_query": str(ctx.retrieval_query or retrieval_query)[:240],
                "agent_enabled": "true",
                "agent_tool_steps": ctx.agent_tool_steps,
                "agent_tool_summary_count": str(len(ctx.agent_tool_summaries)),
                "session_rag_count": str(len(ctx.session_retrieval_items)),
                "keyword_context_count": str(len(ctx.keyword_context_items)),
                "session_recall_breakdown": [
                    {
                        "segment_id": str(x.get("segment_id") or ""),
                        "source": str(x.get("source") or ""),
                        "fusion_score": x.get("fusion_score"),
                        "keyword_norm": x.get("keyword_norm"),
                        "vector_norm": x.get("vector_norm"),
                        "recency_norm": x.get("recency_norm"),
                        "text_preview": str(x.get("text_preview") or "")[:140],
                    }
                    for x in (ctx.session_retrieval_items or [])[:8]
                ],
                "preview": preview,  # type: ignore[dict-item]
                "keyword_context_preview": [
                    {
                        "segment_id": str(x.get("segment_id") or ""),
                        "score": x.get("score"),
                        "keyword_overlap_count": x.get("keyword_overlap_count"),
                        "text_preview": str(x.get("text_preview") or "")[:140],
                    }
                    for x in (ctx.keyword_context_items or [])[:8]
                ],
            },
        )


class PromptNode:
    plugin_id = "prompt"

    @staticmethod
    def _chars_by_tokens(tokens: int) -> int:
        return max(1, int(tokens * 3.5))

    @staticmethod
    def _rough_tokens_by_chars(chars: int) -> int:
        return max(1, int(chars / 3.5))

    @staticmethod
    def _trim_messages(messages: List[Dict[str, str]], max_chars: int) -> List[Dict[str, str]]:
        total = sum(len(str((m or {}).get("content") or "")) for m in messages)
        if max_chars <= 0 or total <= max_chars:
            return messages
        kept: List[Dict[str, str]] = []
        used = 0
        system_messages = [m for m in messages if str((m or {}).get("role") or "") == "system"]
        non_system = [m for m in messages if str((m or {}).get("role") or "") != "system"]
        for msg in system_messages[:3]:
            content = str(msg.get("content") or "")
            if used + len(content) > max_chars:
                content = content[: max(0, max_chars - used)]
            kept.append({"role": "system", "content": content})
            used += len(content)
            if used >= max_chars:
                return kept
        tail: List[Dict[str, str]] = []
        for msg in reversed(non_system):
            content = str(msg.get("content") or "")
            if tail and used + len(content) > max_chars:
                break
            if used + len(content) > max_chars:
                content = content[-max(0, max_chars - used) :]
            tail.append({"role": str(msg.get("role") or "user"), "content": content})
            used += len(content)
            if used >= max_chars:
                break
        tail.reverse()
        return kept + tail

    async def run(self, ctx: ChatContext) -> NodeResult:
        prompt_messages: List[Dict[str, str]] = []
        cfg = get_memory_config()
        model_context_tokens = max(4096, int(get_setting("chat_model_context_tokens", 1000000)))
        reserve_ratio = max(0.0, min(float(get_setting("chat_model_context_reserve_ratio", 0.20)), 0.8))
        response_max_tokens = max(256, int(get_setting("chat_stream_max_tokens", 4096)))
        usable_context_tokens = max(1024, int(model_context_tokens * (1.0 - reserve_ratio)) - response_max_tokens)
        hard_char_cap = max(20000, int(get_setting("chat_context_hard_char_cap", 2500000)))
        total_char_budget = min(
            hard_char_cap,
            max(max(800, cfg.context_char_budget), self._chars_by_tokens(usable_context_tokens)),
        )
        ratios = ChatPipeline._normalize_budget_ratios(
            {
                "l0": cfg.budget_l0_ratio,
                "l1": cfg.budget_l1_ratio,
                "l2": cfg.budget_l2_ratio,
                "l3": cfg.budget_l3_ratio,
                "l4": cfg.budget_l4_ratio,
                "l5": cfg.budget_l5_ratio,
            }
        )
        l4_char_budget = max(120, int(total_char_budget * ratios.get("l4", 0.0)))
        agent_config = ctx.request.agent_config
        system_prompt = str(
            (agent_config.instructions if agent_config else "")
            or get_setting(
                "chat_system_prompt",
                "你是企业知识库问答助手。优先依据给定证据回答；若证据不足，明确说明并给出下一步建议。",
            )
        )
        if agent_config and agent_config.role:
            system_prompt = f"角色定位：{agent_config.role.strip()}\n\n{system_prompt}"
        if agent_config and agent_config.response_style:
            system_prompt = f"{system_prompt}\n\n回复要求：{agent_config.response_style.strip()}"
        prompt_messages.append(
            {
                "role": "system",
                "content": system_prompt,
            }
        )
        if agent_config:
            for example in agent_config.reply_examples:
                if example.user.strip() and example.assistant.strip():
                    prompt_messages.append({"role": "user", "content": example.user.strip()})
                    prompt_messages.append({"role": "assistant", "content": example.assistant.strip()})
        for msg in ctx.messages[-10:]:
            if msg.role in {"user", "assistant"}:
                prompt_messages.append({"role": msg.role, "content": msg.content})
        if ctx.retrieval_items:
            snippets = []
            used = 0
            for idx, item in enumerate(ctx.retrieval_items, start=1):
                text = str(item.get("text") or item.get("chunk_text") or "")
                if text:
                    doc_name = _retrieval_doc_name(item)
                    line = (
                        f"[{idx}] 文档：{doc_name}；doc_id：{item.get('doc_id', '-')}; "
                        f"chunk：{item.get('chunk_id', '-')}; score：{item.get('score', '-')}\n{text}\n"
                    )
                    if snippets and used + len(line) > l4_char_budget:
                        break
                    snippets.append(line.rstrip())
                    used += len(line)
            if snippets:
                prompt_messages.append(
                    {
                        "role": "system",
                        "content": "以下是检索证据，请优先引用：\n" + "\n\n".join(snippets[:8]),
                    }
                )
        if ctx.agent_tool_summaries:
            summary_budget = max(400, int(total_char_budget * 0.12))
            summaries = []
            used = 0
            for idx, item in enumerate(ctx.agent_tool_summaries, start=1):
                text = str(item.get("summary") or "").strip()
                if not text:
                    continue
                line = f"[ToolSummary {idx}] focus={item.get('focus', '-')}\n{text}"
                if summaries and used + len(line) > summary_budget:
                    break
                summaries.append(line)
                used += len(line)
            if summaries:
                prompt_messages.append(
                    {
                        "role": "system",
                        "content": "以下是辅助 LLM 工具对检索与中间上下文的总结：\n" + "\n\n".join(summaries),
                    }
                )
        if ctx.session_retrieval_items:
            snippets = []
            used = 0
            for idx, item in enumerate(ctx.session_retrieval_items, start=1):
                text = str(item.get("text") or "")
                if text:
                    line = f"[S{idx}] {text}\n"
                    if snippets and used + len(line) > l4_char_budget:
                        break
                    snippets.append(line.rstrip())
                    used += len(line)
            if snippets:
                prompt_messages.append(
                    {
                        "role": "system",
                        "content": "以下是本会话历史中的相关片段：\n" + "\n\n".join(snippets[:6]),
                    }
                )
        prompt_messages.append({"role": "user", "content": ctx.request.message})
        prompt_messages = self._trim_messages(prompt_messages, total_char_budget)
        ctx.prompt_messages = prompt_messages
        prompt_chars = sum(len(str((m or {}).get("content") or "")) for m in prompt_messages)
        return NodeResult(
            stage="PROMPT_READY",
            info={
                "prompt_messages": str(len(prompt_messages)),
                "model_context_tokens": str(model_context_tokens),
                "usable_context_tokens": str(usable_context_tokens),
                "reserved_ratio": str(reserve_ratio),
                "estimated_prompt_tokens": str(self._rough_tokens_by_chars(prompt_chars)),
                "hard_char_cap": str(hard_char_cap),
                "agent_tool_summary_count": str(len(ctx.agent_tool_summaries)),
            },
        )


class GenerationNode:
    plugin_id = "generation"

    async def run(self, ctx: ChatContext) -> NodeResult:
        # no-op, actual streaming happens in pipeline executor
        return NodeResult(stage="GENERATION_STREAMING")


class PersistNode:
    plugin_id = "persist"

    async def run(self, ctx: ChatContext) -> NodeResult:
        return NodeResult(stage="PERSISTED")


def register_builtin_nodes() -> None:
    for plugin in [NormalizeNode(), RetrievalNode(), PromptNode(), GenerationNode(), PersistNode()]:
        node_registry.register(plugin)


register_builtin_nodes()


class ChatPipeline:
    pipeline_id = "default-chat-v1"
    nodes = ["normalize", "retrieval", "prompt", "generation", "persist"]

    def __init__(self) -> None:
        self._graph = self._build_graph()
        self._emit_var: contextvars.ContextVar[Any] = contextvars.ContextVar(
            f"chat_pipeline_emit_{id(self)}",
            default=None,
        )
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._active_tasks_lock = asyncio.Lock()

    async def cancel_active_run(self, run_id: str) -> bool:
        async with self._active_tasks_lock:
            task = self._active_tasks.get(run_id)
            if task is None or task.done():
                return False
            task.cancel()
            return True

    @staticmethod
    def _rough_tokens_by_chars(chars: int) -> int:
        # lightweight estimate for budgeting/observability; avoids extra tokenizer cost.
        return max(0, int(chars / 3.5))

    @staticmethod
    def _chars_by_tokens(tokens: int) -> int:
        return max(0, int(tokens * 3.5))

    @staticmethod
    def _normalize_budget_ratios(raw: Dict[str, float]) -> Dict[str, float]:
        safe = {k: max(0.0, float(v or 0.0)) for k, v in raw.items()}
        total = sum(safe.values())
        if total <= 0:
            n = float(len(safe) or 1)
            return {k: 1.0 / n for k in safe.keys()}
        return {k: float(v / total) for k, v in safe.items()}

    @staticmethod
    def _llm_prompt_preview(prompt_messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        preview: List[Dict[str, str]] = []
        for msg in prompt_messages[:20]:
            role = str(msg.get("role") or "unknown")
            content = str(msg.get("content") or "")
            if len(content) > 2000:
                content = content[:2000] + "...(truncated)"
            preview.append({"role": role, "content": content})
        return preview

    @staticmethod
    def _error_text(exc: Exception, limit: int = 500) -> str:
        text = str(exc or "").strip()
        if not text:
            text = exc.__class__.__name__
        return text[:limit]

    @staticmethod
    def _fallback_completion_max_tokens() -> int:
        raw = int(get_setting("chat_fallback_completion_max_tokens", get_setting("chat_stream_max_tokens", 4096)))
        return max(512, min(raw, 8192))

    @staticmethod
    def _continuation_max_rounds() -> int:
        return max(0, min(int(get_setting("chat_stream_continuation_max_rounds", 2)), 5))

    async def _complete_continuation(
        self,
        ctx: ChatContext,
        partial_text: str,
        *,
        exclude_endpoint_ids: set[str],
        attempt_callback,
    ) -> str:
        if not partial_text.strip():
            return ""
        messages = list(ctx.prompt_messages)
        messages.append({"role": "assistant", "content": partial_text})
        messages.append(
            {
                "role": "user",
                "content": (
                    "上一条回答因为输出长度限制在中途停止。请从中断处继续完成剩余内容，"
                    "不要重复已经写过的内容，保持相同格式，直接续写。"
                ),
            }
        )
        return await llm_client.complete_text(
            messages,
            max_tokens=self._fallback_completion_max_tokens(),
            temperature=0.2,
            tenant_id=ctx.tenant_id,
            task="chat_llm",
            exclude_endpoint_ids=exclude_endpoint_ids,
            attempt_callback=attempt_callback,
        )

    @staticmethod
    def _truncate_text(text: str, max_chars: int) -> str:
        txt = str(text or "").strip()
        if max_chars <= 0:
            return ""
        if len(txt) <= max_chars:
            return txt
        return txt[:max_chars]

    @staticmethod
    def _heuristic_retrieval_query(question: str) -> str:
        terms = chat_store._query_terms(question)
        if not terms:
            return question
        return " ".join(terms[:12])

    @staticmethod
    def _parse_iso_ts(value: Any) -> Optional[datetime]:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts
        except Exception:
            return None

    def _query_rewrite_quality(self, original: str, rewritten: str) -> Dict[str, Any]:
        src_terms = chat_store._query_terms(original)
        dst_terms = chat_store._query_terms(rewritten)
        src_set = set(src_terms)
        dst_set = set(dst_terms)
        overlap = len(src_set.intersection(dst_set))
        overlap_ratio = (overlap / max(1, len(src_set))) if src_set else 1.0
        len_ratio = (len(rewritten) / max(1, len(original))) if original else 1.0
        min_ratio = float(get_setting("memory_query_rewrite_min_overlap_ratio", 0.35))
        min_chars = int(get_setting("memory_query_rewrite_min_chars", 6))
        max_len_ratio = float(get_setting("memory_query_rewrite_max_len_ratio", 1.6))
        passes = (
            len(rewritten) >= min_chars
            and overlap_ratio >= min_ratio
            and len_ratio <= max_len_ratio
        )
        return {
            "passes": bool(passes),
            "src_terms": len(src_terms),
            "dst_terms": len(dst_terms),
            "overlap_terms": int(overlap),
            "overlap_ratio": round(overlap_ratio, 4),
            "len_ratio": round(len_ratio, 4),
            "rewritten_chars": len(rewritten),
        }

    @staticmethod
    def _norm_keyword_score(v: float) -> float:
        return max(0.0, min(1.0, float(v) / 5.0))

    @staticmethod
    def _norm_vector_score(v: float) -> float:
        fv = float(v)
        if fv < 0.0:
            fv = (fv + 1.0) / 2.0
        return max(0.0, min(1.0, fv))

    def _recency_score(self, updated_at: Any) -> float:
        ts = self._parse_iso_ts(updated_at)
        if ts is None:
            return 0.5
        now = datetime.now(timezone.utc)
        age_seconds = max(0.0, (now - ts).total_seconds())
        age_days = age_seconds / 86400.0
        half_life_days = float(get_setting("memory_session_fusion_recency_half_life_days", 7.0))
        half_life_days = max(0.1, half_life_days)
        return max(0.0, min(1.0, 0.5 ** (age_days / half_life_days)))

    async def _rewrite_retrieval_query(self, ctx: ChatContext) -> str:
        raw = str(ctx.request.message or "").strip()
        if not raw:
            ctx.retrieval_query_meta = {"source": "empty", "passes": True}
            return ""
        enabled = str(get_setting("memory_query_rewrite_enabled", "true")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if not enabled:
            ctx.retrieval_query_meta = {"source": "disabled", "passes": True}
            return raw
        max_chars = int(get_setting("memory_query_rewrite_max_chars", 240))
        use_llm = str(get_setting("memory_query_rewrite_use_llm", "false")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if use_llm:
            try:
                rewritten = await llm_client.complete_text(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是检索查询重写助手。"
                                "请把用户问题改写成便于向量检索和关键词召回的简洁查询，"
                                "保留实体、时间、约束条件，不要扩写，不要解释。"
                            ),
                        },
                        {"role": "user", "content": raw},
                    ],
                    max_tokens=160,
                    temperature=0.0,
                    tenant_id=ctx.tenant_id,
                    task="chat_aux",
                )
                rewritten = self._truncate_text(rewritten, max_chars)
                if rewritten:
                    quality = self._query_rewrite_quality(raw, rewritten)
                    if quality.get("passes"):
                        ctx.retrieval_query_meta = {"source": "llm", **quality}
                        return rewritten
                    ctx.retrieval_query_meta = {"source": "llm_rejected", **quality}
            except Exception:
                pass
        fallback = self._truncate_text(self._heuristic_retrieval_query(raw), max_chars)
        quality = self._query_rewrite_quality(raw, fallback)
        ctx.retrieval_query_meta = {"source": "heuristic", **quality}
        return fallback

    @staticmethod
    def _importance_score(text: str) -> float:
        t = str(text or "")
        base = 0.45 + min(len(t) / 3000.0, 0.35)
        if any(x in t for x in ["必须", "不要", "优先", "结论", "需求", "约束", "TODO", "风险"]):
            base += 0.15
        return max(0.0, min(1.0, base))

    @staticmethod
    def _is_auto_session_title(title: str) -> bool:
        t = str(title or "").strip().lower()
        if not t:
            return True
        if t in {"new chat", "新对话", "new session"}:
            return True
        return t.startswith("chat-")

    @classmethod
    def _is_weak_auto_session_title(cls, title: str) -> bool:
        t = str(title or "").strip()
        if cls._is_auto_session_title(t):
            return True
        if cls._is_greeting_like(t):
            return True
        lowered = t.lower()
        if lowered in {"你好，你能做什么", "你能做什么", "hello"}:
            return True
        if any(x in t for x in ["；", ";", " / ", "|"]):
            parts = [x.strip() for x in re.split(r"[；;|/]+", t) if x.strip()]
            if not parts:
                return True
            if any(cls._is_greeting_like(x) or "你能做什么" in x or "能做什么" in x for x in parts):
                return True
            if len(parts) >= 3:
                return True
        if len(t) <= 2:
            return True
        return False

    @staticmethod
    def _clean_short_title(raw: str, max_chars: int = 15) -> str:
        text = str(raw or "").strip()
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[\"'`【】\[\]\(\){}<>]+", "", text)
        if len(text) > max_chars:
            text = text[:max_chars]
        return text.strip() or "新对话"

    @staticmethod
    def _is_greeting_like(text: str) -> bool:
        t = str(text or "").strip().lower()
        if not t:
            return True
        patterns = [
            r"^(你好|您好|哈喽|嗨|在吗|早上好|晚上好|hello|hi|hey|test|测试)[!！。.\s]*$",
            r"^(请问在吗|有人吗)[!！。.\s]*$",
        ]
        return any(re.match(p, t) for p in patterns)

    @classmethod
    def _is_topic_message_strong(cls, text: str) -> bool:
        t = str(text or "").strip()
        if not t:
            return False
        if cls._is_greeting_like(t):
            return False
        if len(t) >= 10:
            return True
        # shorter text may still be meaningful if contains question/task cues
        cues = ["怎么", "如何", "为什么", "帮我", "请给", "需要", "方案", "计划", "排查", "错误", "?","？"]
        return any(c in t for c in cues)

    @classmethod
    def _title_stability_guard(
        cls,
        user_messages: List[str],
        *,
        attempt_count: int,
        max_attempts: int,
        model_stable: bool,
        title: str,
    ) -> bool:
        if cls._is_weak_auto_session_title(title):
            return False
        if attempt_count >= max_attempts:
            return True
        # Require at least one strong topical message before accepting stability early.
        has_strong = any(cls._is_topic_message_strong(x) for x in user_messages)
        if not has_strong:
            return False
        if attempt_count >= 2:
            return True
        return bool(model_stable)

    @classmethod
    def _select_title_messages(cls, user_messages: List[str], limit: int) -> List[str]:
        cleaned = [str(x or "").strip() for x in user_messages if str(x or "").strip()]
        if not cleaned:
            return []
        topical = [x for x in cleaned if cls._is_topic_message_strong(x)]
        source = topical or [x for x in cleaned if not cls._is_greeting_like(x)] or cleaned
        return source[-max(1, int(limit or 1)) :]

    async def _infer_session_title(
        self,
        user_messages: List[str],
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        joined = "\n".join([f"{idx + 1}. {x}" for idx, x in enumerate(user_messages) if str(x).strip()])
        if not joined.strip():
            return {"title": "新对话", "stable": False, "reason": "empty"}
        system_prompt = (
            "你是会话标题生成助手。"
            "请根据用户消息生成一个6到15个字的中文会话标题，优先概括最新明确主题。"
            "除非完全只有问候或测试，否则应给出可用标题并尽量 stable=true。"
            "输出严格 JSON：{\"title\":\"...\",\"stable\":true/false,\"reason\":\"...\"}。"
            "不要输出任何 JSON 以外内容。"
        )
        user_prompt = (
            f"最多可用消息数={len(user_messages)}，"
            f"当前是否强制给出标题={str(bool(force)).lower()}。\n"
            "用户消息如下：\n"
            f"{joined}\n\n"
            "标题不要包含“你好”“你能做什么”等问候或能力询问。"
            "如果已经能看出主题，stable 置为 true。"
            "如果是强制阶段，必须给出可用标题并 stable=true。"
        )
        try:
            raw = await llm_client.complete_text(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=180,
                temperature=0.0,
                tenant_id="*",
                task="chat_aux",
            )
            text = str(raw or "").strip()
            data: Dict[str, Any] = {}
            try:
                data = json.loads(text)
            except Exception:
                m = re.search(r"\{[\s\S]*\}", text)
                if m:
                    data = json.loads(m.group(0))
            title = self._clean_short_title(str(data.get("title") or ""))
            stable = bool(data.get("stable", False))
            if force and not title:
                title = self._clean_short_title(" ".join(user_messages[:2]))
                stable = True
            return {
                "title": title or self._clean_short_title(" ".join(user_messages[:2])),
                "stable": bool(stable if not force else True),
                "reason": str(data.get("reason") or ""),
                "raw_output": text[:2000],
                "parsed_output": data,
            }
        except Exception:
            fallback = self._clean_short_title("；".join(user_messages)[:32])
            return {
                "title": fallback,
                "stable": bool(force),
                "reason": "fallback",
                "raw_output": "",
                "parsed_output": {},
            }

    @staticmethod
    def _messages_to_text(messages: List[Any], max_chars: int) -> str:
        parts: List[str] = []
        used = 0
        for msg in messages:
            role = getattr(msg, "role", "unknown")
            content = str(getattr(msg, "content", "") or "").strip()
            if not content:
                continue
            line = f"{role}: {content}\n"
            if used + len(line) > max_chars:
                remain = max(0, max_chars - used)
                if remain > 0:
                    parts.append(line[:remain])
                break
            parts.append(line)
            used += len(line)
        return "".join(parts)

    def _build_memory_text(self, ctx: ChatContext, cfg: MemoryConfig) -> str:
        ratios = self._normalize_budget_ratios(
            {
                "l0": cfg.budget_l0_ratio,
                "l1": cfg.budget_l1_ratio,
                "l2": cfg.budget_l2_ratio,
                "l3": cfg.budget_l3_ratio,
                "l4": cfg.budget_l4_ratio,
                "l5": cfg.budget_l5_ratio,
            }
        )
        total_char_budget = min(
            max(800, cfg.context_char_budget),
            self._chars_by_tokens(max(200, int(cfg.budget_total_tokens))),
        )
        budget = max(400, int(total_char_budget * (ratios.get("l1", 0.0) + ratios.get("l2", 0.0) + ratios.get("l3", 0.0))))
        if budget <= 0:
            return ""
        l1_cap = max(120, int(total_char_budget * ratios.get("l1", 0.0)))
        l2_cap = max(120, int(total_char_budget * ratios.get("l2", 0.0)))
        l3_cap = max(120, int(total_char_budget * ratios.get("l3", 0.0)))
        sections: List[str] = []
        used = 0

        def try_add(title: str, body: str, cap: Optional[int] = None) -> None:
            nonlocal used
            txt = str(body or "").strip()
            if not txt:
                return
            if cap is not None and cap > 0:
                txt = txt[:cap]
            block = f"[{title}]\n{txt}\n"
            if used + len(block) > budget:
                remain = max(0, budget - used)
                if remain > 24:
                    sections.append(block[:remain])
                    used += remain
                return
            sections.append(block)
            used += len(block)

        if cfg.hierarchical_enabled:
            try_add("Short Memory Summary", ctx.memory_short_summary, cap=int(l1_cap * 0.4))
            try_add("Long Memory Summary", ctx.memory_long_summary, cap=int(l1_cap * 0.6))

        if cfg.user_profile_enabled:
            try_add("User Profile", ctx.memory_user_profile, cap=l3_cap)

        items = list(ctx.memory_items or [])
        if items:
            scored: List[tuple[float, Dict[str, Any]]] = []
            total = len(items)
            for idx, item in enumerate(items):
                importance = float(item.get("importance_v2", item.get("importance", 0.5)) or 0.5)
                recency = (idx + 1) / max(total, 1)
                score = importance * 0.7 + recency * 0.3
                scored.append((score, item))
            scored.sort(key=lambda x: x[0], reverse=True)
            lines: List[str] = []
            for _, item in scored[:40]:
                text = str(item.get("text") or "").strip()
                if text:
                    lines.append(f"- {text[:280]}")
            if lines:
                try_add("Important Episodic Memory", "\n".join(lines), cap=l2_cap)

        return "".join(sections).strip()

    async def _update_session_title_after_turn(self, ctx: ChatContext) -> Dict[str, Any]:
        session = chat_store.get_session(ctx.session.session_id)
        if not session:
            return {"updated": False}

        user_messages = [
            str(m.content or "").strip()
            for m in chat_store.list_messages(ctx.session.session_id, limit=120)
            if getattr(m, "role", "") == "user" and str(getattr(m, "content", "")).strip()
        ]
        if not user_messages:
            return {"updated": False, "skipped": "no_user_messages"}

        state = chat_store.get_session_title_state(ctx.session.session_id)
        attempt_count = int(state.get("attempt_count") or 0)
        is_stable = bool(state.get("is_stable", False))
        title_is_auto_or_weak = self._is_auto_session_title(session.title) or self._is_weak_auto_session_title(session.title)
        if not title_is_auto_or_weak and not is_stable:
            title_is_auto_or_weak = True
        if not title_is_auto_or_weak:
            return {"updated": False, "skipped": "manual_title", "stable": is_stable, "attempt_count": attempt_count}
        if is_stable and not self._is_weak_auto_session_title(session.title):
            return {"updated": False, "stable": True}

        max_attempts = max(1, min(3, int(get_setting("chat_session_title_max_attempts", 3))))
        if is_stable and self._is_weak_auto_session_title(session.title):
            attempt_count = 0
            chat_store.upsert_session_title_state(ctx.session.session_id, attempt_count=0, is_stable=False)
        next_attempt = min(max_attempts, attempt_count + 1)
        if next_attempt <= attempt_count:
            if attempt_count >= max_attempts:
                next_attempt = max_attempts
            else:
                return {
                    "updated": False,
                    "stable": False,
                    "attempt_count": attempt_count,
                    "max_attempts": max_attempts,
                }

        subset = self._select_title_messages(user_messages, next_attempt)
        force = next_attempt >= max_attempts or len(subset) >= 2
        await self._emit_event(
            {
                "event": "trace",
                "run_id": ctx.run.run_id,
                "title": "Session Title Inference Request",
                "detail": (
                    f"Preparing title inference with messages={len(subset)}, "
                    f"attempt={next_attempt}/{max_attempts}, force={force}."
                ),
                "info": {
                    "attempt_count": next_attempt,
                    "max_attempts": max_attempts,
                    "force": force,
                    "input_messages": [str(x)[:240] for x in subset],
                },
            }
        )
        inferred = await self._infer_session_title(subset, force=force)
        title = self._clean_short_title(str(inferred.get("title") or "新对话"))
        if self._is_weak_auto_session_title(title):
            title = self._clean_short_title("；".join(subset)[:48])
        stable = bool(inferred.get("stable", False))
        stable = self._title_stability_guard(
            subset,
            attempt_count=next_attempt,
            max_attempts=max_attempts,
            model_stable=stable,
            title=title,
        )
        if force and not self._is_weak_auto_session_title(title):
            stable = True

        chat_store.upsert_session_title_state(
            ctx.session.session_id,
            attempt_count=next_attempt,
            is_stable=stable,
        )
        updated = False
        if stable and title and title != session.title:
            renamed = chat_store.rename_session(ctx.session.session_id, title)
            if renamed:
                ctx.session = renamed
                updated = True

        await self._emit_event(
            {
                "event": "trace",
                "run_id": ctx.run.run_id,
                "title": "Session Title Inference",
                "detail": (
                    f"attempt={next_attempt}/{max_attempts}, stable={stable}, "
                    f"title='{title}', updated={updated}."
                ),
                "info": {
                    "attempt_count": next_attempt,
                    "max_attempts": max_attempts,
                    "stable": stable,
                    "title": title,
                    "title_updated": updated,
                    "reason": str(inferred.get("reason") or ""),
                    "guard_has_strong_topic": any(self._is_topic_message_strong(x) for x in subset),
                    "raw_output": str(inferred.get("raw_output") or "")[:2000],
                    "parsed_output": inferred.get("parsed_output") if isinstance(inferred.get("parsed_output"), dict) else {},
                },
            }
        )
        return {
            "updated": updated,
            "stable": stable,
            "attempt_count": next_attempt,
            "max_attempts": max_attempts,
            "title": title,
        }

    async def _update_memory_after_turn(self, ctx: ChatContext) -> None:
        cfg = get_memory_config()
        run_id = ctx.run.run_id
        session_id = ctx.session.session_id
        history_all = chat_store.list_messages(session_id, limit=400)
        source_start_id = history_all[0].message_id if history_all else None
        source_end_id = history_all[-1].message_id if history_all else None
        activity_ttl_seconds = int(get_setting("memory_recent_activity_ttl_seconds", 7 * 24 * 3600))
        cleanup_interval_seconds = int(get_setting("memory_recent_activity_cleanup_interval_seconds", 300))

        if cfg.hierarchical_enabled or cfg.eviction_enabled:
            user_text = str(ctx.request.message or "").strip()
            if user_text:
                chat_store.add_memory_item(
                    session_id,
                    text=f"User asks: {user_text}",
                    importance=self._importance_score(user_text),
                    kind="user_intent",
                )
            assistant_text = str(ctx.response_text or "").strip()
            if assistant_text:
                chat_store.add_memory_item(
                    session_id,
                    text=f"Assistant answer: {assistant_text}",
                    importance=self._importance_score(assistant_text),
                    kind="assistant_answer",
                )
                recent_messages = chat_store.list_messages(session_id, limit=2)
                if len(recent_messages) >= 2:
                    msg_ids = [recent_messages[0].message_id, recent_messages[1].message_id]
                    seg_user = str(recent_messages[0].content or "").strip()
                    seg_assistant = str(recent_messages[1].content or "").strip()
                    segment_text = f"User: {seg_user}\nAssistant: {seg_assistant}".strip()
                    seg_keywords = chat_store._query_terms(f"{seg_user}\n{seg_assistant}")
                    segment_id = chat_store.upsert_session_segment(
                        tenant_id=ctx.tenant_id,
                        actor_id=ctx.actor_id,
                        session_id=session_id,
                        source_message_ids=msg_ids,
                        text_content=segment_text[:8000],
                        token_estimate=self._rough_tokens_by_chars(len(segment_text)),
                        keywords=seg_keywords,
                    )
                    session_vector_upsert_ok = None
                    session_vector_enabled = str(
                        get_setting("memory_session_vector_enabled", "true")
                    ).strip().lower() in {"1", "true", "yes", "on"}
                    if segment_id and session_vector_enabled:
                        try:
                            segment_vec = await llm_client.embed_text(segment_text[:6000], tenant_id=ctx.tenant_id)
                            session_vector_upsert_ok = await session_vector_store.upsert_segment(
                                segment_id=segment_id,
                                vector=segment_vec,
                                payload={
                                    "segment_id": segment_id,
                                    "tenant_id": ctx.tenant_id,
                                    "actor_id": ctx.actor_id,
                                    "session_id": session_id,
                                    "source_message_ids": msg_ids,
                                    "text": segment_text[:6000],
                                    "text_preview": segment_text[:220],
                                    "token_estimate": self._rough_tokens_by_chars(len(segment_text)),
                                    "keywords": seg_keywords,
                                    "updated_at": ctx.run.updated_at,
                                },
                            )
                            if session_vector_upsert_ok:
                                chat_store.mark_session_segment_embedding_ref(segment_id, segment_id)
                        except Exception:
                            session_vector_upsert_ok = False
                    await self._emit_event(
                        {
                            "event": "trace",
                            "run_id": run_id,
                            "title": "Session Segment Upsert",
                            "detail": (
                                f"Upserted session segment id={segment_id or '-'} with 2-source messages; "
                                f"vector_upsert={session_vector_upsert_ok}."
                            ),
                            "info": {
                                "segment_id": segment_id,
                                "source_message_ids": msg_ids,
                                "keyword_count": len(seg_keywords),
                                "vector_upsert_ok": session_vector_upsert_ok,
                            },
                        }
                    )

            conflict_stats = chat_store.resolve_memory_conflicts(session_id)
            if int(conflict_stats.get("shadowed", 0)) > 0:
                await self._emit_event(
                    {
                        "event": "trace",
                        "run_id": run_id,
                        "title": "Memory Conflict Resolver",
                        "detail": (
                            f"Resolved memory conflicts: active={conflict_stats.get('active', 0)}, "
                            f"shadowed={conflict_stats.get('shadowed', 0)}."
                        ),
                        "info": conflict_stats,
                    }
                )

        if cfg.eviction_enabled:
            stats = chat_store.apply_memory_eviction(session_id, max_items=cfg.memory_item_max)
            await self._emit_event(
                {
                    "event": "trace",
                    "run_id": run_id,
                    "title": "Memory Eviction",
                    "detail": (
                        f"Applied weighted eviction: kept {stats.get('kept', 0)}/"
                        f"{stats.get('total', 0)} memory items."
                    ),
                    "info": stats,
                }
            )

        if cfg.auto_summary_enabled and len(history_all) >= cfg.compress_trigger_messages:
            transcript = self._messages_to_text(history_all, max_chars=max(3000, cfg.context_char_budget * 2))
            try:
                short_raw = await llm_client.complete_text(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是对话记忆压缩助手。请将以下对话压缩为可复用的短期记忆，"
                                f"聚焦目标、约束、当前结论，控制在 {cfg.short_summary_max_chars} 字以内。"
                            ),
                        },
                        {"role": "user", "content": transcript},
                    ],
                    max_tokens=520,
                    temperature=0.1,
                    tenant_id=ctx.tenant_id,
                    task="chat_aux",
                )
                long_raw = await llm_client.complete_text(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是对话记忆压缩助手。请将以下对话整理为中长期记忆，"
                                "提炼稳定偏好、背景事实、长期任务与边界，不要编造，"
                                f"控制在 {cfg.long_summary_max_chars} 字以内。"
                            ),
                        },
                        {"role": "user", "content": transcript},
                    ],
                    max_tokens=900,
                    temperature=0.1,
                    tenant_id=ctx.tenant_id,
                    task="chat_aux",
                )
                short_summary = self._truncate_text(short_raw, cfg.short_summary_max_chars)
                long_summary = self._truncate_text(long_raw, cfg.long_summary_max_chars)
                chat_store.upsert_session_memory(
                    session_id=session_id,
                    short_summary=short_summary,
                    long_summary=long_summary,
                )
                short_summary_id = chat_store.upsert_session_summary(
                    session_id=session_id,
                    tenant_id=ctx.tenant_id,
                    actor_id=ctx.actor_id,
                    summary_type="short",
                    content=short_summary,
                    source_message_start_id=source_start_id,
                    source_message_end_id=source_end_id,
                )
                long_summary_id = chat_store.upsert_session_summary(
                    session_id=session_id,
                    tenant_id=ctx.tenant_id,
                    actor_id=ctx.actor_id,
                    summary_type="long",
                    content=long_summary,
                    source_message_start_id=source_start_id,
                    source_message_end_id=source_end_id,
                )
                await self._emit_event(
                    {
                        "event": "trace",
                        "run_id": run_id,
                        "title": "Auto Memory Compression",
                        "detail": (
                            f"Updated hierarchical memory summaries "
                            f"(short={len(short_summary)} chars, long={len(long_summary)} chars)."
                        ),
                        "info": {
                            "short_summary_id": short_summary_id,
                            "long_summary_id": long_summary_id,
                        },
                    }
                )
            except Exception as exc:
                await self._emit_event(
                    {
                        "event": "trace",
                        "run_id": run_id,
                        "title": "Auto Memory Compression",
                        "detail": f"Skipped due to error: {exc}",
                    }
                )

        cleanup_stats = chat_store.cleanup_expired_recent_activities(
            run_interval_seconds=cleanup_interval_seconds
        )
        if int(cleanup_stats.get("ran", 0)) == 1:
            await self._emit_event(
                {
                    "event": "trace",
                    "run_id": run_id,
                    "title": "Recent Activity Cleanup",
                    "detail": f"Cleanup finished, deleted={cleanup_stats.get('deleted', 0)}.",
                    "info": cleanup_stats,
                }
            )

        if cfg.user_profile_enabled and history_all:
            threshold = max(1, int(cfg.profile_refresh_new_messages_threshold))
            profile_memory = chat_store.get_user_profile_memory(ctx.tenant_id, ctx.actor_id)
            last_sampled_at = str(profile_memory.get("last_profile_message_at") or "").strip() or None
            new_user_messages = chat_store.count_messages_since(
                ctx.tenant_id,
                ctx.actor_id,
                since_created_at=last_sampled_at,
                role="user",
            )
            if new_user_messages >= threshold:
                samples = chat_store.list_recent_conversation_samples(
                    tenant_id=ctx.tenant_id,
                    actor_id=ctx.actor_id,
                    limit_messages=cfg.profile_sample_messages,
                    max_chars=cfg.profile_sample_max_chars,
                    since_created_at=last_sampled_at,
                    roles=["user", "assistant"],
                )
                if samples:
                    sample_lines: List[str] = []
                    max_sample_created_at: Optional[str] = None
                    for item in samples:
                        role = str(item.get("role") or "unknown")
                        content = str(item.get("content") or "").strip()
                        if content:
                            sample_lines.append(f"{role}: {content}")
                        ts = str(item.get("created_at") or "").strip()
                        if ts:
                            if not max_sample_created_at:
                                max_sample_created_at = ts
                            else:
                                a = self._parse_iso_ts(max_sample_created_at)
                                b = self._parse_iso_ts(ts)
                                if a is None or (b is not None and b > a):
                                    max_sample_created_at = ts
                    sample_text = "\n".join(sample_lines)
                    prev = profile_memory.get("profile_summary", "")
                    try:
                        profile_raw = await llm_client.complete_text(
                            [
                                {
                                    "role": "system",
                                    "content": (
                                        "你是用户画像维护助手。"
                                        "根据已有画像与最近多会话样本，输出更新后的用户画像。"
                                        "你可以自由决定补充、删除、合并、改写内容。"
                                        "重点关注：身份背景、擅长领域/关注主题、沟通风格、偏好、约束/禁忌、长期目标。"
                                        "不要固定模板，不要输出推理过程，不要编造。"
                                    ),
                                },
                                {
                                    "role": "user",
                                    "content": f"已有画像：\n{prev}\n\n最近对话样本：\n{sample_text}",
                                },
                            ],
                            max_tokens=850,
                            temperature=0.2,
                            tenant_id=ctx.tenant_id,
                            task="chat_aux",
                        )
                        profile = self._truncate_text(profile_raw, cfg.profile_max_chars)
                        chat_store.upsert_user_profile_memory(
                            ctx.tenant_id,
                            ctx.actor_id,
                            profile,
                            last_profile_message_at=max_sample_created_at or last_sampled_at,
                        )

                        activity_raw = await llm_client.complete_text(
                            [
                                {
                                    "role": "system",
                                    "content": (
                                        "你是用户近期活动提取助手。"
                                        "从多会话样本中提取两类信息："
                                        "topics=用户最近关注的主题；events=用户最近发生的事件。"
                                        "输出严格 JSON：{\"topics\":[\"...\"],\"events\":[\"...\"]}。"
                                        "每项最多24字，每类最多3项。"
                                    ),
                                },
                                {"role": "user", "content": sample_text[:6000]},
                            ],
                            max_tokens=320,
                            temperature=0.1,
                            tenant_id=ctx.tenant_id,
                            task="chat_aux",
                        )
                        activity_data: Dict[str, Any] = {}
                        try:
                            activity_data = json.loads(str(activity_raw or "").strip())
                        except Exception:
                            m = re.search(r"\{[\s\S]*\}", str(activity_raw or ""))
                            if m:
                                activity_data = json.loads(m.group(0))
                        topics = activity_data.get("topics") if isinstance(activity_data.get("topics"), list) else []
                        events = activity_data.get("events") if isinstance(activity_data.get("events"), list) else []
                        topic_ids: List[str] = []
                        event_ids: List[str] = []
                        for one in topics[:3]:
                            text = self._truncate_text(str(one or "").strip(), 24)
                            if not text:
                                continue
                            rid = chat_store.upsert_recent_activity_signal(
                                tenant_id=ctx.tenant_id,
                                actor_id=ctx.actor_id,
                                session_id=session_id,
                                activity_type="topic_signal",
                                activity_text=text,
                                importance=0.72,
                                ttl_seconds=activity_ttl_seconds,
                                min_interval_seconds=900,
                            )
                            if rid:
                                topic_ids.append(rid)
                        for one in events[:3]:
                            text = self._truncate_text(str(one or "").strip(), 24)
                            if not text:
                                continue
                            rid = chat_store.upsert_recent_activity_signal(
                                tenant_id=ctx.tenant_id,
                                actor_id=ctx.actor_id,
                                session_id=session_id,
                                activity_type="event_signal",
                                activity_text=text,
                                importance=0.68,
                                ttl_seconds=activity_ttl_seconds,
                                min_interval_seconds=900,
                            )
                            if rid:
                                event_ids.append(rid)
                        await self._emit_event(
                            {
                                "event": "trace",
                                "run_id": run_id,
                                "title": "User Profile Memory",
                                "detail": (
                                    f"Updated profile ({len(profile)} chars), topics={len(topic_ids)}, events={len(event_ids)}."
                                ),
                                "info": {
                                    "profile_chars": len(profile),
                                    "samples": len(samples),
                                    "new_user_messages": int(new_user_messages),
                                    "threshold": int(threshold),
                                    "last_profile_message_at": max_sample_created_at or last_sampled_at,
                                    "topic_ids": topic_ids,
                                    "event_ids": event_ids,
                                },
                            }
                        )
                    except Exception as exc:
                        await self._emit_event(
                            {
                                "event": "trace",
                                "run_id": run_id,
                                "title": "User Profile Memory",
                                "detail": f"Skipped due to error: {exc}",
                            }
                        )
            else:
                await self._emit_event(
                    {
                        "event": "trace",
                        "run_id": run_id,
                        "title": "User Profile Memory",
                        "detail": (
                            f"Skipped: new_user_messages={new_user_messages} < threshold={threshold}."
                        ),
                        "info": {
                            "new_user_messages": int(new_user_messages),
                            "threshold": int(threshold),
                            "last_profile_message_at": last_sampled_at,
                        },
                    }
                )

    async def rerank_memory_items(self, session_id: str, limit: int = 200) -> Dict[str, Any]:
        cfg = get_memory_config()
        sid = str(session_id or "").strip()
        if not sid:
            return {"status": "error", "detail": "session_id required"}
        items = chat_store.list_memory_items_for_rerank(sid, limit=max(1, int(limit)))
        if not items:
            return {"status": "ok", "session_id": sid, "total": 0, "updated": 0}
        updated = 0
        batch = max(1, int(cfg.rerank_batch_size))
        if not cfg.rerank_enabled:
            # fallback: project to importance_v2 with a small recency/use-count tweak.
            total = len(items)
            for idx, item in enumerate(items):
                base = float(item.get("importance") or 0.5)
                use_count = float(item.get("use_count") or 0.0)
                recency = (idx + 1) / max(total, 1)
                score = max(0.0, min(1.0, base * 0.75 + min(0.15, use_count * 0.01) + recency * 0.10))
                if chat_store.update_memory_item_importance_v2(
                    sid,
                    str(item.get("item_id") or ""),
                    importance_v2=score,
                    confidence=0.55,
                ):
                    updated += 1
            return {
                "status": "ok",
                "session_id": sid,
                "total": len(items),
                "updated": updated,
                "mode": "heuristic",
            }

        for start in range(0, len(items), batch):
            part = items[start : start + batch]
            lines: List[str] = []
            for i, one in enumerate(part, start=1):
                lines.append(f"{i}. id={one.get('item_id')} kind={one.get('kind')} text={str(one.get('text') or '')[:320]}")
            prompt = "\n".join(lines)
            try:
                raw = await llm_client.complete_text(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是记忆重排评分助手。"
                                "请对每条记忆按“后续对话复用价值”打分(0~1)，并给出置信度(0~1)。"
                                "输出严格 JSON 数组：[{\"item_id\":\"...\",\"importance_v2\":0.0,\"confidence\":0.0}]。"
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=600,
                    temperature=0.0,
                    tenant_id="*",
                    task="chat_aux",
                )
                data: List[Dict[str, Any]] = []
                txt = str(raw or "").strip()
                try:
                    obj = json.loads(txt)
                    if isinstance(obj, list):
                        data = [x for x in obj if isinstance(x, dict)]
                except Exception:
                    m = re.search(r"\[[\s\S]*\]", txt)
                    if m:
                        obj = json.loads(m.group(0))
                        if isinstance(obj, list):
                            data = [x for x in obj if isinstance(x, dict)]
                by_id = {str(x.get("item_id") or ""): x for x in data}
                for one in part:
                    iid = str(one.get("item_id") or "")
                    rec = by_id.get(iid, {})
                    score = float(rec.get("importance_v2", one.get("importance", 0.5)) or 0.5)
                    conf = float(rec.get("confidence", 0.6) or 0.6)
                    if chat_store.update_memory_item_importance_v2(
                        sid,
                        iid,
                        importance_v2=score,
                        confidence=conf,
                    ):
                        updated += 1
            except Exception:
                for one in part:
                    iid = str(one.get("item_id") or "")
                    score = float(one.get("importance") or 0.5)
                    if chat_store.update_memory_item_importance_v2(
                        sid,
                        iid,
                        importance_v2=score,
                        confidence=0.5,
                    ):
                        updated += 1
        return {
            "status": "ok",
            "session_id": sid,
            "total": len(items),
            "updated": updated,
            "mode": "llm" if cfg.rerank_enabled else "heuristic",
        }

    def _build_graph(self):
        graph: StateGraph = StateGraph(ChatGraphState)  # type: ignore[arg-type]
        graph.add_node("normalize", self._node_normalize)
        graph.add_node("retrieval", self._node_retrieval)
        graph.add_node("prompt", self._node_prompt)
        graph.add_node("generation", self._node_generation)
        graph.add_node("persist", self._node_persist)
        graph.set_entry_point("normalize")
        graph.add_edge("normalize", "retrieval")
        graph.add_edge("retrieval", "prompt")
        graph.add_edge("prompt", "generation")
        graph.add_edge("generation", "persist")
        graph.add_edge("persist", END)
        return graph.compile()

    async def _emit_event(self, payload: Dict[str, Any]) -> None:
        emit = self._emit_var.get()
        if emit is not None:
            await emit(payload)

    async def _emit_cancelled_once(self, state: ChatGraphState) -> bool:
        if state.get("cancelled"):
            return True
        ctx = state["ctx"]
        if not chat_store.is_cancelled(ctx.run.run_id):
            return False
        chat_store.complete_run(ctx.run.run_id, status="CANCELLED")
        await self._emit_event(
            {
                "event": "cancelled",
                "run_id": ctx.run.run_id,
                "stage": "CANCELLED",
                "message": "run cancelled",
            }
        )
        return True

    async def _run_stage(self, state: ChatGraphState, node_id: str) -> Dict[str, Any]:
        if await self._emit_cancelled_once(state):
            return {"cancelled": True}
        ctx = state["ctx"]
        plugin = node_registry.get(node_id)
        result = await plugin.run(ctx)
        done = int(state.get("done", 0)) + 1
        total = int(state.get("total", len(self.nodes)))
        chat_store.update_run(
            ctx.run.run_id,
            stage=result.stage or node_id.upper(),
            progress_current=done,
            progress_total=total,
        )
        await self._emit_event(
            {
                "event": "stage",
                "run_id": ctx.run.run_id,
                "stage": result.stage or node_id.upper(),
                "progress_current": done,
                "progress_total": total,
                "info": result.info,
            }
        )
        return {"ctx": ctx, "done": done}

    async def _node_normalize(self, state: ChatGraphState) -> Dict[str, Any]:
        updates = await self._run_stage(state, "normalize")
        if updates.get("cancelled"):
            return updates
        ctx = updates["ctx"]
        cfg = get_memory_config()
        full_history = chat_store.list_messages(
            ctx.session.session_id,
            limit=max(cfg.recent_messages_limit * 4, cfg.compress_trigger_messages * 4, 60),
        )
        ratios = self._normalize_budget_ratios(
            {
                "l0": cfg.budget_l0_ratio,
                "l1": cfg.budget_l1_ratio,
                "l2": cfg.budget_l2_ratio,
                "l3": cfg.budget_l3_ratio,
                "l4": cfg.budget_l4_ratio,
                "l5": cfg.budget_l5_ratio,
            }
        )
        total_char_budget = min(
            max(800, cfg.context_char_budget),
            self._chars_by_tokens(max(200, int(cfg.budget_total_tokens))),
        )
        l0_char_budget = max(200, int(total_char_budget * ratios.get("l0", 0.0)))
        recent_window: List[Any] = []
        used_chars = 0
        for msg in reversed(full_history):
            txt = str(getattr(msg, "content", "") or "")
            cost = len(txt) + 16
            if recent_window and (used_chars + cost > l0_char_budget):
                break
            recent_window.append(msg)
            used_chars += cost
            if len(recent_window) >= max(cfg.recent_messages_limit, 1):
                break
        recent_window.reverse()
        ctx.messages = recent_window
        session_memory = chat_store.get_session_memory(ctx.session.session_id)
        ctx.memory_short_summary = str(session_memory.get("short_summary") or "")
        ctx.memory_long_summary = str(session_memory.get("long_summary") or "")
        if cfg.user_profile_enabled:
            user_profile = chat_store.get_user_profile_memory(ctx.tenant_id, ctx.actor_id)
            base_profile = str(user_profile.get("profile_summary") or "")
            recent_topics = chat_store.list_recent_activities(
                ctx.tenant_id,
                ctx.actor_id,
                activity_types=["topic_signal"],
                limit=max(1, int(cfg.recent_topics_limit)),
            )
            recent_events = chat_store.list_recent_activities(
                ctx.tenant_id,
                ctx.actor_id,
                activity_types=["event_signal"],
                limit=max(1, int(cfg.recent_events_limit)),
            )
            topic_lines: List[str] = []
            event_lines: List[str] = []
            for item in recent_topics:
                text = str(item.get("activity_text") or "").strip()
                if text:
                    topic_lines.append(f"- {text[:220]}")
            for item in recent_events:
                text = str(item.get("activity_text") or "").strip()
                if text:
                    event_lines.append(f"- {text[:220]}")
            if topic_lines:
                base_profile = (
                    base_profile
                    + "\n\n[Recent Topics]\n"
                    + "\n".join(topic_lines)
                ).strip()
            if event_lines:
                base_profile = (
                    base_profile
                    + "\n\n[Recent Events]\n"
                    + "\n".join(event_lines)
                ).strip()
            ctx.memory_user_profile = base_profile
        else:
            ctx.memory_user_profile = ""
        ctx.memory_items = chat_store.list_memory_items(ctx.session.session_id)
        ctx.keyword_context_items = []
        if cfg.eviction_enabled:
            chat_store.apply_memory_eviction(ctx.session.session_id, max_items=cfg.memory_item_max)
            ctx.memory_items = chat_store.list_memory_items(ctx.session.session_id)
        ctx.memory_text = self._build_memory_text(ctx, cfg)
        ctx.retrieval_query = await self._rewrite_retrieval_query(ctx)
        await self._emit_event(
            {
                "event": "trace",
                "run_id": ctx.run.run_id,
                "title": "Query Rewrite",
                "detail": (
                    f"Original chars={len(ctx.request.message or '')}, "
                    f"rewritten chars={len(ctx.retrieval_query or '')}, "
                    f"source={ctx.retrieval_query_meta.get('source', '-')}, "
                    f"passes={ctx.retrieval_query_meta.get('passes', True)}."
                ),
                "info": {
                    "original": str(ctx.request.message or "")[:240],
                    "rewritten": str(ctx.retrieval_query or "")[:240],
                    "meta": dict(ctx.retrieval_query_meta or {}),
                },
            }
        )
        await self._emit_event(
            {
                "event": "trace",
                "run_id": ctx.run.run_id,
                "title": "Memory Access",
                "detail": (
                    f"Loaded history={len(full_history)}, prompt_history={len(ctx.messages)}, "
                    f"memory_items={len(ctx.memory_items)}."
                ),
                "info": {
                    "memory_config": cfg.as_dict(),
                    "session_memory_short_chars": len(ctx.memory_short_summary),
                    "session_memory_long_chars": len(ctx.memory_long_summary),
                    "user_profile_chars": len(ctx.memory_user_profile),
                    "memory_context_chars": len(ctx.memory_text),
                },
            }
        )
        return updates

    async def _node_retrieval(self, state: ChatGraphState) -> Dict[str, Any]:
        updates = await self._run_stage(state, "retrieval")
        if updates.get("cancelled"):
            return updates
        ctx = updates["ctx"]
        for step in ctx.agent_tool_steps or []:
            tool = str(step.get("tool") or "-")
            status = str(step.get("status") or "-")
            detail_bits = []
            if step.get("query"):
                detail_bits.append(f"query='{str(step.get('query'))[:80]}'")
            if step.get("returned_count") is not None:
                detail_bits.append(f"returned={step.get('returned_count')}")
            if step.get("summary_chars") is not None:
                detail_bits.append(f"summary_chars={step.get('summary_chars')}")
            if step.get("reason"):
                detail_bits.append(f"reason={str(step.get('reason'))[:120]}")
            await self._emit_event(
                {
                    "event": "agent_action",
                    "run_id": ctx.run.run_id,
                    "title": f"Tool {tool}",
                    "detail": f"round={step.get('round', '-')}, status={status}" + (", " + ", ".join(detail_bits) if detail_bits else ""),
                    "tool": tool,
                    "status": status,
                    "info": step,
                }
            )
        preview = []
        preview_limit = max(1, min(int(ctx.request.top_k or 6), 20))
        if ctx.retrieval_items:
            for item in ctx.retrieval_items[:preview_limit]:
                preview.append(_retrieval_preview_item(item))
        session_preview = []
        if ctx.session_retrieval_items:
            for item in ctx.session_retrieval_items[:5]:
                session_preview.append(
                    {
                        "segment_id": str(item.get("segment_id") or ""),
                        "session_id": str(item.get("session_id") or ""),
                        "score": item.get("score"),
                        "text_preview": str(item.get("text_preview") or ""),
                    }
                )
        keyword_preview = []
        if ctx.keyword_context_items:
            for item in ctx.keyword_context_items[:5]:
                keyword_preview.append(
                    {
                        "segment_id": str(item.get("segment_id") or ""),
                        "session_id": str(item.get("session_id") or ""),
                        "score": item.get("score"),
                        "keyword_overlap_count": item.get("keyword_overlap_count"),
                        "text_preview": str(item.get("text_preview") or ""),
                    }
                )
        await self._emit_event(
            {
                "event": "trace",
                "run_id": ctx.run.run_id,
                "title": "KB Retrieval",
                "detail": (
                    f"Retrieved {len(ctx.retrieval_items)} chunks "
                    f"(kb={ctx.request.kb_id or ctx.session.kb_id or '-'}, top_k={ctx.request.top_k}, rank={ctx.request.rank_method}), "
                    f"session-rag={len(ctx.session_retrieval_items)}, keyword-comp={len(ctx.keyword_context_items)}, "
                    f"query='{(ctx.retrieval_query or ctx.request.message)[:80]}'."
                ),
                "retrieval_preview": preview,
                "retrieval_requested_top_k": ctx.request.top_k,
                "retrieval_returned_count": len(ctx.retrieval_items or []),
                "retrieval_preview_count": len(preview),
                "session_retrieval_preview": session_preview,
                "keyword_context_preview": keyword_preview,
                "agent_tool_steps": ctx.agent_tool_steps,
                "agent_tool_summary_count": len(ctx.agent_tool_summaries),
            }
        )
        return updates

    async def _node_prompt(self, state: ChatGraphState) -> Dict[str, Any]:
        updates = await self._run_stage(state, "prompt")
        if updates.get("cancelled"):
            return updates
        ctx = updates["ctx"]
        cfg = get_memory_config()
        ratios = self._normalize_budget_ratios(
            {
                "l0": cfg.budget_l0_ratio,
                "l1": cfg.budget_l1_ratio,
                "l2": cfg.budget_l2_ratio,
                "l3": cfg.budget_l3_ratio,
                "l4": cfg.budget_l4_ratio,
                "l5": cfg.budget_l5_ratio,
            }
        )
        total_char_budget = min(
            max(800, cfg.context_char_budget),
            self._chars_by_tokens(max(200, int(cfg.budget_total_tokens))),
        )
        l5_char_budget = max(80, int(total_char_budget * ratios.get("l5", 0.0)))
        if ctx.memory_text:
            insert_at = 1 if ctx.prompt_messages else 0
            ctx.prompt_messages.insert(
                insert_at,
                {
                    "role": "system",
                    "content": "以下是可复用记忆，请仅在相关时使用：\n" + ctx.memory_text,
                },
            )
            await self._emit_event(
                {
                    "event": "trace",
                    "run_id": ctx.run.run_id,
                    "title": "Memory Injection",
                    "detail": f"Injected memory context into prompt ({len(ctx.memory_text)} chars).",
                }
            )

        if ctx.keyword_context_items and l5_char_budget > 0:
            snippets: List[str] = []
            used = 0
            for idx, item in enumerate(ctx.keyword_context_items, start=1):
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                line = f"[K{idx}] {text}\n"
                if snippets and used + len(line) > l5_char_budget:
                    break
                snippets.append(line.rstrip())
                used += len(line)
            if snippets:
                insert_at = 1 if ctx.prompt_messages else 0
                ctx.prompt_messages.insert(
                    insert_at,
                    {
                        "role": "system",
                        "content": "以下是关键词上下文补偿片段：\n" + "\n\n".join(snippets),
                    },
                )

        model_context_tokens = max(4096, int(get_setting("chat_model_context_tokens", 1000000)))
        reserve_ratio = max(0.0, min(float(get_setting("chat_model_context_reserve_ratio", 0.20)), 0.8))
        response_max_tokens = max(256, int(get_setting("chat_stream_max_tokens", 4096)))
        usable_context_tokens = max(1024, int(model_context_tokens * (1.0 - reserve_ratio)) - response_max_tokens)
        hard_char_cap = max(20000, int(get_setting("chat_context_hard_char_cap", 2500000)))
        ctx.prompt_messages = PromptNode._trim_messages(
            ctx.prompt_messages,
            min(hard_char_cap, self._chars_by_tokens(usable_context_tokens)),
        )
        budget_total_tokens = usable_context_tokens
        prompt_chars = sum(len(str((m or {}).get("content") or "")) for m in (ctx.prompt_messages or []))
        budget_used_tokens = self._rough_tokens_by_chars(prompt_chars)
        layer_stats = {
            "l0_recent_window_messages": len(ctx.messages or []),
            "l1_short_summary_chars": len(ctx.memory_short_summary or ""),
            "l1_long_summary_chars": len(ctx.memory_long_summary or ""),
            "l2_memory_items": len(ctx.memory_items or []),
            "l3_user_profile_chars": len(ctx.memory_user_profile or ""),
            "l4_kb_retrieval_items": len(ctx.retrieval_items or []),
            "l4_session_rag_items": len(ctx.session_retrieval_items or []),
            "l5_keyword_hits": len(ctx.keyword_context_items or []),
            "memory_context_chars": len(ctx.memory_text or ""),
            "recent_messages_limit": cfg.recent_messages_limit,
            "retrieval_query_source": str((ctx.retrieval_query_meta or {}).get("source") or ""),
            "retrieval_query_passes": bool((ctx.retrieval_query_meta or {}).get("passes", True)),
            "retrieval_query_chars": len(ctx.retrieval_query or ""),
            "session_fusion_weights": {
                "keyword": float(get_setting("memory_session_fusion_weight_keyword", 0.55)),
                "vector": float(get_setting("memory_session_fusion_weight_vector", 0.35)),
                "recency": float(get_setting("memory_session_fusion_weight_recency", 0.10)),
                "hybrid_bonus": float(get_setting("memory_session_fusion_hybrid_bonus", 0.05)),
            },
            "budget": {
                "total_tokens": budget_total_tokens,
                "model_context_tokens": model_context_tokens,
                "reserve_ratio": reserve_ratio,
                "response_max_tokens": response_max_tokens,
                "ratios": ratios,
                "total_char_budget": total_char_budget,
                "l5_char_budget": l5_char_budget,
                "hard_char_cap": hard_char_cap,
            },
        }
        top_items: List[Dict[str, Any]] = []
        for item in (ctx.memory_items or [])[:8]:
            top_items.append(
                {
                    "source": "memory_item",
                    "kind": str(item.get("kind") or ""),
                    "importance": item.get("importance"),
                    "importance_v2": item.get("importance_v2"),
                    "text_preview": str(item.get("text") or "")[:180],
                }
            )
        for item in (ctx.retrieval_items or [])[:5]:
            top_items.append(
                {
                    "source": "kb_retrieval",
                "doc_id": str(item.get("doc_id") or ""),
                "chunk_id": str(item.get("chunk_id") or ""),
                "filename": _retrieval_doc_name(item),
                "document_name": _retrieval_doc_name(item),
                "score": item.get("score"),
                "text_preview": str(item.get("text") or item.get("chunk_text") or "")[:180],
            }
            )
        for item in (ctx.session_retrieval_items or [])[:5]:
            top_items.append(
                {
                    "source": "session_rag",
                    "segment_id": str(item.get("segment_id") or ""),
                    "session_id": str(item.get("session_id") or ""),
                    "score": item.get("score"),
                    "text_preview": str(item.get("text_preview") or "")[:180],
                }
            )
        for item in (ctx.keyword_context_items or [])[:5]:
            top_items.append(
                {
                    "source": "keyword_comp",
                    "segment_id": str(item.get("segment_id") or ""),
                    "session_id": str(item.get("session_id") or ""),
                    "score": item.get("score"),
                    "text_preview": str(item.get("text_preview") or "")[:180],
                }
            )
        chat_store.log_memory_fusion(
            run_id=ctx.run.run_id,
            session_id=ctx.session.session_id,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
            budget_total_tokens=budget_total_tokens,
            budget_used_tokens=budget_used_tokens,
            layer_stats=layer_stats,
            top_items=top_items,
        )
        return updates

    async def _node_generation(self, state: ChatGraphState) -> Dict[str, Any]:
        updates = await self._run_stage(state, "generation")
        if updates.get("cancelled"):
            return updates
        ctx = updates["ctx"]
        system_text = str((ctx.prompt_messages[0] if ctx.prompt_messages else {}).get("content") or "")
        citation_grounded = (
            "do not fabricate citations" in system_text.lower()
            or "only use retrieved" in system_text.lower()
            or "only use retrieved documents" in system_text.lower()
        )
        if citation_grounded and ctx.request.use_kb and (ctx.request.kb_id or ctx.session.kb_id) and not ctx.retrieval_items:
            response = "No sufficient supporting evidence found in current literature."
            ctx.response_text = response
            await self._emit_event(
                {
                    "event": "token",
                    "run_id": ctx.run.run_id,
                    "delta": response,
                }
            )
            await self._emit_event(
                {
                    "event": "trace",
                    "run_id": ctx.run.run_id,
                    "title": "Evidence Guard",
                    "detail": "Skipped LLM generation because citation-grounded mode requires retrieved KB evidence, but retrieval returned 0 chunks.",
                    "info": {"kb_retrieval_count": 0},
                }
            )
            return updates
        llm_meta = llm_client.runtime_meta()
        await self._emit_event(
            {
                "event": "trace",
                "run_id": ctx.run.run_id,
                "title": "LLM Pool Preview",
                "detail": (
                    f"Next available LLM candidate at {llm_meta.get('base_url')} "
                    f"with model={llm_meta.get('model')} "
                    f"(key_source={llm_meta.get('key_source')}, key={llm_meta.get('api_key_configured')})."
                ),
                "info": {
                    "base_url": llm_meta.get("base_url"),
                    "model": llm_meta.get("model"),
                    "key_source": llm_meta.get("key_source"),
                    "api_key_configured": llm_meta.get("api_key_configured"),
                    "prompt_messages_count": len(ctx.prompt_messages),
                    "prompt_messages": self._llm_prompt_preview(ctx.prompt_messages),
                },
            }
        )
        async def _emit_llm_attempt(meta: Dict[str, Any]) -> None:
            endpoint_id = str(meta.get("endpoint_id") or "-")
            base_url = str(meta.get("base_url") or "-")
            model = str(meta.get("model") or "-")
            path = str(meta.get("path") or "-")
            attempt = int(meta.get("attempt") or 0) + 1
            await self._emit_event(
                {
                    "event": "trace",
                    "run_id": ctx.run.run_id,
                    "title": "External API Request",
                    "detail": (
                        f"Calling LLM provider at {base_url} with model={model} "
                        f"(endpoint={endpoint_id}, attempt={attempt}, path={path})."
                    ),
                    "info": {
                        "base_url": base_url,
                        "model": model,
                        "endpoint_id": endpoint_id,
                        "attempt": attempt,
                        "path": path,
                        "stream": bool(meta.get("stream", False)),
                        "prompt_messages_count": len(ctx.prompt_messages),
                    },
                }
            )

        output: List[str] = []
        try:
            async for token in llm_client.stream_chat(
                ctx.prompt_messages,
                tenant_id=ctx.tenant_id,
                endpoint_id=(ctx.request.agent_config.model_endpoint_id if ctx.request.agent_config else None),
                temperature=(ctx.request.agent_config.temperature if ctx.request.agent_config else 0.2),
                top_p=(ctx.request.agent_config.top_p if ctx.request.agent_config else 1.0),
                max_tokens=(ctx.request.agent_config.max_tokens if ctx.request.agent_config else None),
                attempt_callback=_emit_llm_attempt,
            ):
                if chat_store.is_cancelled(ctx.run.run_id):
                    chat_store.complete_run(ctx.run.run_id, status="CANCELLED")
                    await self._emit_event(
                        {
                            "event": "cancelled",
                            "run_id": ctx.run.run_id,
                            "stage": "CANCELLED",
                            "message": "run cancelled",
                        }
                    )
                    return {"ctx": ctx, "cancelled": True, "done": updates.get("done", state.get("done", 0))}
                output.append(token)
                await self._emit_event(
                    {
                        "event": "token",
                        "run_id": ctx.run.run_id,
                        "delta": token,
                    }
                )
        except Exception as exc:
            if output:
                err_text = self._error_text(exc, 240)
                partial_text = "".join(output).strip()
                if isinstance(exc, LlmStreamTruncatedError):
                    await self._emit_event(
                        {
                            "event": "trace",
                            "run_id": ctx.run.run_id,
                            "title": "LLM Stream Continuation",
                            "detail": (
                                f"Stream stopped by token limit after {len(partial_text)} chars; "
                                "requesting continuation."
                            ),
                            "info": {
                                "level": "warning",
                                "finish_reason": getattr(exc, "finish_reason", ""),
                                "partial_chars": len(partial_text),
                                "continuation_max_rounds": self._continuation_max_rounds(),
                            },
                        }
                    )
                    continuation_base = partial_text
                    exclude_ids = set(getattr(exc, "attempted_endpoint_ids", set()) or set())
                    for round_no in range(1, self._continuation_max_rounds() + 1):
                        try:
                            continuation = await self._complete_continuation(
                                ctx,
                                continuation_base,
                                exclude_endpoint_ids=exclude_ids,
                                attempt_callback=_emit_llm_attempt,
                            )
                        except Exception as cont_exc:
                            cont_err = self._error_text(cont_exc, 300)
                            await self._emit_event(
                                {
                                    "event": "trace",
                                    "run_id": ctx.run.run_id,
                                    "title": "LLM Continuation Failed",
                                    "detail": f"Continuation round {round_no} failed. error={cont_err}",
                                    "info": {"level": "error", "error": cont_err, "round": round_no},
                                }
                            )
                            break
                        continuation = str(continuation or "").strip()
                        if not continuation:
                            break
                        output.append("\n" + continuation)
                        continuation_base = (continuation_base + "\n" + continuation).strip()
                        await self._emit_event(
                            {
                                "event": "token",
                                "run_id": ctx.run.run_id,
                                "delta": "\n" + continuation,
                            }
                        )
                        if len(continuation) < 80:
                            break
                    partial_text = "".join(output).strip()
                    err_text = ""
                if not err_text:
                    pass
                elif len(partial_text) < max(1, int(get_setting("chat_stream_partial_success_min_chars", 1200))):
                    partial_success_min = max(1, int(get_setting("chat_stream_partial_success_min_chars", 1200)))
                    await self._emit_event(
                        {
                            "event": "trace",
                            "run_id": ctx.run.run_id,
                            "title": "LLM Stream Fallback",
                            "detail": f"Stream ended early after {len(partial_text)} chars; replacing with non-stream completion. error={err_text}",
                            "info": {"level": "warning", "error": err_text, "fallback": "non_stream_completion"},
                        }
                    )
                    try:
                        fallback_text = await llm_client.complete_text(
                            ctx.prompt_messages,
                            max_tokens=self._fallback_completion_max_tokens(),
                            temperature=0.2,
                            tenant_id=ctx.tenant_id,
                            task="chat_llm",
                            exclude_endpoint_ids=set(getattr(exc, "attempted_endpoint_ids", set()) or set()),
                            attempt_callback=_emit_llm_attempt,
                        )
                    except Exception as fallback_exc:
                        fallback_err = self._error_text(fallback_exc, 500)
                        await self._emit_event(
                            {
                                "event": "trace",
                                "run_id": ctx.run.run_id,
                                "title": "LLM Fallback Failed",
                                "detail": f"Non-stream completion also failed. error={fallback_err}",
                                "info": {"level": "error", "error": fallback_err},
                            }
                        )
                        raise RuntimeError(
                            f"LLM stream and fallback completion failed: {fallback_err}"
                        ) from fallback_exc
                    output = [str(fallback_text or "").strip()]
                    await self._emit_event(
                        {
                            "event": "token",
                            "run_id": ctx.run.run_id,
                            "delta": output[0],
                            "replace": True,
                        }
                    )
                else:
                    await self._emit_event(
                        {
                            "event": "trace",
                            "run_id": ctx.run.run_id,
                            "title": "LLM Stream Fallback",
                            "detail": f"Stream ended with error after substantial output; using partial response. error={err_text}",
                            "info": {"level": "warning", "error": err_text, "partial_chars": len(partial_text)},
                        }
                    )
            else:
                err_text = self._error_text(exc, 240)
                await self._emit_event(
                    {
                        "event": "trace",
                        "run_id": ctx.run.run_id,
                        "title": "LLM Stream Fallback",
                        "detail": f"Stream failed before output; switching to non-stream completion. error={err_text}",
                        "info": {"level": "warning", "error": err_text, "fallback": "non_stream_completion"},
                    }
                )
                try:
                    fallback_text = await llm_client.complete_text(
                        ctx.prompt_messages,
                        max_tokens=self._fallback_completion_max_tokens(),
                        temperature=0.2,
                        tenant_id=ctx.tenant_id,
                        task="chat_llm",
                        exclude_endpoint_ids=set(getattr(exc, "attempted_endpoint_ids", set()) or set()),
                        attempt_callback=_emit_llm_attempt,
                    )
                except Exception as fallback_exc:
                    fallback_err = self._error_text(fallback_exc, 500)
                    await self._emit_event(
                        {
                            "event": "trace",
                            "run_id": ctx.run.run_id,
                            "title": "LLM Fallback Failed",
                            "detail": f"Non-stream completion also failed. error={fallback_err}",
                            "info": {"level": "error", "error": fallback_err},
                        }
                    )
                    raise RuntimeError(
                        f"LLM stream and fallback completion failed: {fallback_err}"
                    ) from fallback_exc
                if fallback_text:
                    output.append(fallback_text)
                    await self._emit_event(
                        {
                            "event": "token",
                            "run_id": ctx.run.run_id,
                            "delta": fallback_text,
                        }
                    )
        response_text = "".join(output).strip()
        min_chars = max(0, int(get_setting("chat_min_final_response_chars", 40)))
        if len(response_text) < min_chars:
            await self._emit_event(
                {
                    "event": "trace",
                    "run_id": ctx.run.run_id,
                    "title": "LLM Short Response Fallback",
                    "detail": f"Final response was too short ({len(response_text)} chars); retrying once with non-stream completion.",
                }
            )
            try:
                fallback_text = await llm_client.complete_text(
                    ctx.prompt_messages,
                    max_tokens=self._fallback_completion_max_tokens(),
                    temperature=0.2,
                    tenant_id=ctx.tenant_id,
                    task="chat_llm",
                    attempt_callback=_emit_llm_attempt,
                )
                if len(str(fallback_text or "").strip()) > len(response_text):
                    previous_text = response_text
                    response_text = str(fallback_text or "").strip()
                    await self._emit_event(
                        {
                            "event": "token",
                            "run_id": ctx.run.run_id,
                            "delta": response_text,
                            "replace": bool(previous_text),
                        }
                    )
            except Exception:
                pass
        ctx.response_text = response_text
        return updates

    async def _node_persist(self, state: ChatGraphState) -> Dict[str, Any]:
        return await self._run_stage(state, "persist")

    async def run_stream(self, ctx: ChatContext, *, cancel_on_disconnect: bool = False) -> AsyncGenerator[Dict, None]:
        queue: asyncio.Queue = asyncio.Queue()
        consumer_closed = False

        async def _emit(evt: Dict[str, Any]) -> None:
            if consumer_closed:
                return
            await queue.put(evt)

        async def _update_title_background(title_ctx: ChatContext) -> Dict[str, Any]:
            try:
                title_state = await self._update_session_title_after_turn(title_ctx)
                if title_state and not consumer_closed:
                    await queue.put(
                        {
                            "event": "session_title",
                            "run_id": title_ctx.run.run_id,
                            "session_id": title_ctx.session.session_id,
                            "session_title": str(title_ctx.session.title or ""),
                            "session_title_stable": bool(title_state.get("stable", False)),
                            "session_title_updated": bool(title_state.get("updated", False)),
                            "attempt_count": int(title_state.get("attempt_count", 0) or 0),
                            "max_attempts": int(title_state.get("max_attempts", 0) or 0),
                        }
                    )
                return title_state or {}
            except Exception as exc:
                if not consumer_closed:
                    await queue.put(
                        {
                            "event": "trace",
                            "run_id": title_ctx.run.run_id,
                            "title": "Session Title Inference",
                            "detail": f"Background title inference skipped after error: {str(exc)[:240]}",
                        }
                    )
                return {}

        async def _runner() -> None:
            emit_token = self._emit_var.set(_emit)
            try:
                # Persist user message up-front so disconnect/failure does not lose the turn.
                chat_store.add_message(ctx.session.session_id, "user", ctx.request.message)
                initial_state: ChatGraphState = {
                    "ctx": ctx,
                    "done": 0,
                    "total": len(self.nodes),
                    "cancelled": False,
                }
                final_state: Dict[str, Any] = await self._graph.ainvoke(initial_state)
                final_ctx: ChatContext = final_state.get("ctx", ctx)
                cancelled = bool(final_state.get("cancelled", False)) or chat_store.is_cancelled(final_ctx.run.run_id)
                if cancelled:
                    chat_store.complete_run(final_ctx.run.run_id, status="CANCELLED")
                    return
                chat_store.add_message(final_ctx.session.session_id, "assistant", final_ctx.response_text)
                title_state: Dict[str, Any] = {}
                with contextlib.suppress(Exception):
                    title_state = await _update_title_background(final_ctx) or {}
                await queue.put(
                    {
                        "event": "trace",
                        "run_id": final_ctx.run.run_id,
                        "title": "Memory Write",
                        "detail": "Persisted user and assistant messages into chat memory store.",
                    }
                )
                await self._update_memory_after_turn(final_ctx)
                chat_store.complete_run(final_ctx.run.run_id, status="COMPLETED")
                await queue.put(
                    {
                        "event": "done",
                        "run_id": final_ctx.run.run_id,
                        "message": "completed",
                        "response_chars": len(final_ctx.response_text or ""),
                        "session_title": str(final_ctx.session.title or ""),
                        "session_title_stable": bool(title_state.get("stable", False)),
                        "session_title_updated": bool(title_state.get("updated", False)),
                    }
                )
            except Exception as exc:
                if chat_store.is_cancelled(ctx.run.run_id):
                    chat_store.complete_run(ctx.run.run_id, status="CANCELLED")
                    await queue.put(
                        {
                            "event": "cancelled",
                            "run_id": ctx.run.run_id,
                            "stage": "CANCELLED",
                            "message": "run cancelled",
                        }
                    )
                else:
                    chat_store.complete_run(ctx.run.run_id, status="FAILED", error_message=str(exc))
                    await queue.put(
                        {
                            "event": "error",
                            "run_id": ctx.run.run_id,
                            "message": str(exc),
                        }
                    )
            except asyncio.CancelledError:
                requested = chat_store.is_cancelled(ctx.run.run_id)
                error_message = None if requested else "client disconnected"
                chat_store.complete_run(
                    ctx.run.run_id,
                    status="CANCELLED",
                    error_message=error_message,
                )
                if requested and not consumer_closed:
                    await queue.put(
                        {
                            "event": "cancelled",
                            "run_id": ctx.run.run_id,
                            "stage": "CANCELLED",
                            "message": "run cancelled",
                        }
                    )
                raise
            finally:
                self._emit_var.reset(emit_token)
                await queue.put({"event": "__end__"})

        task = asyncio.create_task(_runner())
        async with self._active_tasks_lock:
            self._active_tasks[ctx.run.run_id] = task
        try:
            while True:
                evt = await queue.get()
                if evt.get("event") == "__end__":
                    break
                yield evt
        finally:
            consumer_closed = True
            if not task.done() and cancel_on_disconnect:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
            async with self._active_tasks_lock:
                if self._active_tasks.get(ctx.run.run_id) is task:
                    self._active_tasks.pop(ctx.run.run_id, None)


chat_pipeline = ChatPipeline()


def canvas_schema() -> Dict:
    return {
        "canvas_id": "agent_canvas_v1",
        "pipeline_id": chat_pipeline.pipeline_id,
        "nodes": [
            {"id": "normalize", "type": "core", "label": "Normalize"},
            {"id": "retrieval", "type": "agent_tools", "label": "Agent Tool Loop", "optional": True},
            {"id": "prompt", "type": "core", "label": "Prompt Builder"},
            {"id": "generation", "type": "llm", "label": "LLM Stream"},
            {"id": "persist", "type": "core", "label": "Persist Message"},
        ],
        "edges": [
            ["normalize", "retrieval"],
            ["retrieval", "prompt"],
            ["prompt", "generation"],
            ["generation", "persist"],
        ],
        "extensibility": {
            "node_plugin_interface": "runtime.plugins.NodePlugin",
            "tool_plugin_interface": "runtime.plugins.ToolPlugin",
            "tool_registry": "runtime.plugins.tool_registry",
            "node_registry": "runtime.plugins.node_registry",
        },
    }
