from __future__ import annotations

import asyncio
import contextlib
from typing import Any, AsyncGenerator, Dict, List, TypedDict

from langgraph.graph import END, StateGraph

from .llm_client import llm_client
from .config import get_setting
from .memory import MemoryConfig, get_memory_config
from .models import ChatContext, NodeResult
from .plugins import node_registry
from .retrieval_client import search_kb
from .store import chat_store


class ChatGraphState(TypedDict):
    ctx: ChatContext
    done: int
    total: int
    cancelled: bool


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

    async def run(self, ctx: ChatContext) -> NodeResult:
        if not ctx.request.use_kb:
            ctx.retrieval_items = []
            return NodeResult(stage="RETRIEVAL_SKIPPED")
        kb_id = ctx.request.kb_id or ctx.session.kb_id
        if not kb_id:
            ctx.retrieval_items = []
            return NodeResult(stage="RETRIEVAL_SKIPPED")
        items = await search_kb(
            tenant_id=ctx.tenant_id,
            actor_id=ctx.actor_id,
            trace_id=ctx.trace_id,
            kb_id=kb_id,
            query=ctx.request.message,
            top_k=ctx.request.top_k,
        )
        ctx.retrieval_items = items
        preview = []
        for item in items[:5]:
            text = str(item.get("text") or item.get("chunk_text") or "")
            preview.append(
                {
                    "doc_id": str(item.get("doc_id") or ""),
                    "chunk_id": str(item.get("chunk_id") or ""),
                    "score": item.get("score"),
                    "text_preview": text[:180],
                }
            )
        return NodeResult(
            stage="RETRIEVAL_DONE",
            info={
                "count": str(len(items)),
                "rank_method": ctx.request.rank_method,
                "kb_id": kb_id,
                "top_k": str(ctx.request.top_k),
                "preview": preview,  # type: ignore[dict-item]
            },
        )


class PromptNode:
    plugin_id = "prompt"

    async def run(self, ctx: ChatContext) -> NodeResult:
        prompt_messages: List[Dict[str, str]] = []
        system_prompt = str(
            get_setting(
                "chat_system_prompt",
                "你是企业知识库问答助手。优先依据给定证据回答；若证据不足，明确说明并给出下一步建议。",
            )
        )
        prompt_messages.append(
            {
                "role": "system",
                "content": system_prompt,
            }
        )
        for msg in ctx.messages[-10:]:
            if msg.role in {"user", "assistant"}:
                prompt_messages.append({"role": msg.role, "content": msg.content})
        if ctx.retrieval_items:
            snippets = []
            for idx, item in enumerate(ctx.retrieval_items, start=1):
                text = str(item.get("text") or item.get("chunk_text") or "")
                if text:
                    snippets.append(f"[{idx}] {text}")
            if snippets:
                prompt_messages.append(
                    {
                        "role": "system",
                        "content": "以下是检索证据，请优先引用：\n" + "\n\n".join(snippets[:8]),
                    }
                )
        prompt_messages.append({"role": "user", "content": ctx.request.message})
        ctx.prompt_messages = prompt_messages
        return NodeResult(stage="PROMPT_READY")


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
        self._emit: Any = None

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
    def _truncate_text(text: str, max_chars: int) -> str:
        txt = str(text or "").strip()
        if max_chars <= 0:
            return ""
        if len(txt) <= max_chars:
            return txt
        return txt[:max_chars]

    @staticmethod
    def _importance_score(text: str) -> float:
        t = str(text or "")
        base = 0.45 + min(len(t) / 3000.0, 0.35)
        if any(x in t for x in ["必须", "不要", "优先", "结论", "需求", "约束", "TODO", "风险"]):
            base += 0.15
        return max(0.0, min(1.0, base))

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
        budget = cfg.context_char_budget
        if budget <= 0:
            return ""
        sections: List[str] = []
        used = 0

        def try_add(title: str, body: str) -> None:
            nonlocal used
            txt = str(body or "").strip()
            if not txt:
                return
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
            try_add("Short Memory Summary", ctx.memory_short_summary)
            try_add("Long Memory Summary", ctx.memory_long_summary)

        if cfg.user_profile_enabled:
            try_add("User Profile", ctx.memory_user_profile)

        items = list(ctx.memory_items or [])
        if items:
            scored: List[tuple[float, Dict[str, Any]]] = []
            total = len(items)
            for idx, item in enumerate(items):
                importance = float(item.get("importance", 0.5))
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
                try_add("Important Episodic Memory", "\n".join(lines))

        return "".join(sections).strip()

    async def _update_memory_after_turn(self, ctx: ChatContext) -> None:
        cfg = get_memory_config()
        run_id = ctx.run.run_id
        session_id = ctx.session.session_id
        history_all = chat_store.list_messages(session_id, limit=400)

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
                )
                short_summary = self._truncate_text(short_raw, cfg.short_summary_max_chars)
                long_summary = self._truncate_text(long_raw, cfg.long_summary_max_chars)
                chat_store.upsert_session_memory(
                    session_id=session_id,
                    short_summary=short_summary,
                    long_summary=long_summary,
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

        if cfg.user_profile_enabled and history_all:
            user_only = [m for m in history_all if getattr(m, "role", "") == "user"]
            if user_only:
                user_transcript = self._messages_to_text(user_only, max_chars=max(1800, cfg.context_char_budget))
                prev = chat_store.get_user_profile_memory(ctx.tenant_id, ctx.actor_id).get("profile_summary", "")
                try:
                    profile_raw = await llm_client.complete_text(
                        [
                            {
                                "role": "system",
                                "content": (
                                    "你是用户画像记忆提取助手。基于用户历史发言提炼稳定偏好和使用习惯，"
                                    "仅输出事实、偏好、禁忌、风格，不写过程，不编造。"
                                ),
                            },
                            {"role": "user", "content": f"已有画像：\n{prev}\n\n最新用户发言：\n{user_transcript}"},
                        ],
                        max_tokens=650,
                        temperature=0.1,
                    )
                    profile = self._truncate_text(profile_raw, cfg.profile_max_chars)
                    chat_store.upsert_user_profile_memory(ctx.tenant_id, ctx.actor_id, profile)
                    await self._emit_event(
                        {
                            "event": "trace",
                            "run_id": run_id,
                            "title": "User Profile Memory",
                            "detail": f"Updated user profile memory ({len(profile)} chars).",
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
        if self._emit is not None:
            await self._emit(payload)

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
        ctx.messages = full_history[-max(cfg.recent_messages_limit, 1) :]
        session_memory = chat_store.get_session_memory(ctx.session.session_id)
        ctx.memory_short_summary = str(session_memory.get("short_summary") or "")
        ctx.memory_long_summary = str(session_memory.get("long_summary") or "")
        if cfg.user_profile_enabled:
            user_profile = chat_store.get_user_profile_memory(ctx.tenant_id, ctx.actor_id)
            ctx.memory_user_profile = str(user_profile.get("profile_summary") or "")
        else:
            ctx.memory_user_profile = ""
        ctx.memory_items = chat_store.list_memory_items(ctx.session.session_id)
        if cfg.eviction_enabled:
            chat_store.apply_memory_eviction(ctx.session.session_id, max_items=cfg.memory_item_max)
            ctx.memory_items = chat_store.list_memory_items(ctx.session.session_id)
        ctx.memory_text = self._build_memory_text(ctx, cfg)
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
        preview = []
        if ctx.retrieval_items:
            for item in ctx.retrieval_items[:5]:
                text = str(item.get("text") or item.get("chunk_text") or "")
                preview.append(
                    {
                        "doc_id": str(item.get("doc_id") or ""),
                        "chunk_id": str(item.get("chunk_id") or ""),
                        "score": item.get("score"),
                        "text_preview": text[:180],
                    }
                )
        await self._emit_event(
            {
                "event": "trace",
                "run_id": ctx.run.run_id,
                "title": "KB Retrieval",
                "detail": (
                    f"Retrieved {len(ctx.retrieval_items)} chunks "
                    f"(kb={ctx.request.kb_id or ctx.session.kb_id or '-'}, top_k={ctx.request.top_k}, rank={ctx.request.rank_method})."
                ),
                "retrieval_preview": preview,
            }
        )
        return updates

    async def _node_prompt(self, state: ChatGraphState) -> Dict[str, Any]:
        updates = await self._run_stage(state, "prompt")
        if updates.get("cancelled"):
            return updates
        ctx = updates["ctx"]
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
        return updates

    async def _node_generation(self, state: ChatGraphState) -> Dict[str, Any]:
        updates = await self._run_stage(state, "generation")
        if updates.get("cancelled"):
            return updates
        ctx = updates["ctx"]
        llm_meta = llm_client.runtime_meta()
        await self._emit_event(
            {
                "event": "trace",
                "run_id": ctx.run.run_id,
                "title": "External API Request",
                "detail": (
                    f"Calling LLM provider at {llm_meta.get('base_url')} "
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
        output: List[str] = []
        async for token in llm_client.stream_chat(ctx.prompt_messages):
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
        ctx.response_text = "".join(output).strip()
        return updates

    async def _node_persist(self, state: ChatGraphState) -> Dict[str, Any]:
        return await self._run_stage(state, "persist")

    async def run_stream(self, ctx: ChatContext) -> AsyncGenerator[Dict, None]:
        queue: asyncio.Queue = asyncio.Queue()

        async def _emit(evt: Dict[str, Any]) -> None:
            await queue.put(evt)

        self._emit = _emit

        async def _runner() -> None:
            try:
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
                chat_store.add_message(final_ctx.session.session_id, "user", final_ctx.request.message)
                chat_store.add_message(final_ctx.session.session_id, "assistant", final_ctx.response_text)
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
                        "response": final_ctx.response_text,
                    }
                )
            except Exception as exc:
                chat_store.complete_run(ctx.run.run_id, status="FAILED", error_message=str(exc))
                await queue.put(
                    {
                        "event": "error",
                        "run_id": ctx.run.run_id,
                        "message": str(exc),
                    }
                )
            finally:
                await queue.put({"event": "__end__"})

        task = asyncio.create_task(_runner())
        try:
            while True:
                evt = await queue.get()
                if evt.get("event") == "__end__":
                    break
                yield evt
        finally:
            self._emit = None
            if not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task


chat_pipeline = ChatPipeline()


def canvas_schema() -> Dict:
    return {
        "canvas_id": "agent_canvas_v1",
        "pipeline_id": chat_pipeline.pipeline_id,
        "nodes": [
            {"id": "normalize", "type": "core", "label": "Normalize"},
            {"id": "retrieval", "type": "tool", "label": "KB Retrieval", "optional": True},
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
