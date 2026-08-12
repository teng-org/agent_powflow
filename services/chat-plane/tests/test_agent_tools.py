from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


CHAT_ROOT = Path(__file__).resolve().parents[1]
if str(CHAT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHAT_ROOT))

from runtime.agent_tools import build_tool_catalog, execute_mcp, execute_skill
from runtime.models import ChatContext, ChatRequest, ChatRunState, ChatSession
from runtime.pipeline import RetrievalNode


class AgentToolsTest(unittest.TestCase):
    def _context(self, agent_config: dict) -> ChatContext:
        return ChatContext(
            tenant_id="tenant-a", actor_id="actor-a", trace_id="trace-a", source_service="test",
            session=ChatSession(session_id="session-a", tenant_id="tenant-a", actor_id="actor-a", title="test", created_at="now", updated_at="now"),
            run=ChatRunState(run_id="run-a", session_id="session-a", tenant_id="tenant-a", actor_id="actor-a", mode="AGENT_API", started_at="now", updated_at="now"),
            request=ChatRequest(message="hello", use_kb=False, agent_config=agent_config),
        )

    def test_catalog_uses_strict_provider_function_schema(self) -> None:
        ctx = self._context({
            "skills": [{"skill_id": "skill-a", "name": "摘要", "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}],
            "mcp_tools": [{"connection_id": "mcp-a", "tool_name": "get_weather", "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}}}],
            "javascript_tools": [{"tool_id":"js-a","name":"计算","version":1,"definition":{"input_schema":{"type":"object","properties":{"value":{"type":"number"}}}}}],
        })
        tools, lookup = build_tool_catalog(ctx.request.agent_config)
        self.assertEqual(3, len(tools))
        self.assertTrue(all(item["function"]["strict"] for item in tools))
        self.assertEqual(set(lookup), {item["function"]["name"] for item in tools})

    def test_skill_validates_schema_and_runs_as_bounded_subtask(self) -> None:
        ctx = self._context({"skills": [{
            "skill_id": "skill-a", "name": "摘要", "instructions": "只做摘要",
            "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        }]})
        skill = ctx.request.agent_config.skills[0]
        with patch("runtime.agent_tools.llm_client.complete_text", new=AsyncMock(return_value="结果")) as complete:
            self.assertEqual("结果", asyncio.run(execute_skill(skill, {"text": "内容"}, ctx)))
            complete.assert_awaited_once()
        with self.assertRaises(ValueError):
            asyncio.run(execute_skill(skill, {}, ctx))

    def test_unannotated_or_destructive_mcp_is_blocked_by_default(self) -> None:
        ctx = self._context({
            "mcp_tools": [{"connection_id": "mcp-a", "endpoint_url": "https://example.com/mcp", "tool_name": "send_email"}],
            "tool_policy": {"allow_write_tools": False},
        })
        with self.assertRaises(PermissionError):
            asyncio.run(execute_mcp(ctx.request.agent_config.mcp_tools[0], {}, ctx))

    def test_explicit_read_only_mcp_runs_initialize_notification_and_call(self) -> None:
        ctx = self._context({
            "mcp_tools": [{
                "connection_id": "mcp-a", "endpoint_url": "https://example.com/mcp", "tool_name": "lookup_weather",
                "annotations": {"readOnlyHint": True}, "input_schema": {"type": "object"},
            }],
        })
        responses = [({"result": {"protocolVersion": "2025-11-25"}}, "session-a"), ({}, ""), ({"result": {"content": [{"type": "text", "text": "sunny"}]}}, "")]
        with patch("runtime.agent_tools._post_mcp", new=AsyncMock(side_effect=responses)) as post:
            result = asyncio.run(execute_mcp(ctx.request.agent_config.mcp_tools[0], {}, ctx))
        self.assertIn("sunny", result)
        self.assertEqual(["initialize", "notifications/initialized", "tools/call"], [call.args[1]["method"] for call in post.await_args_list])

    def test_javascript_only_agent_replans_after_each_tool_result(self) -> None:
        ctx = self._context({
            "memory": {"mode": "none"},
            "javascript_tools": [{
                "tool_id": "js-a", "name": "calculate", "version": 1,
                "definition": {"input_schema": {"type": "object"}},
            }],
            "tool_policy": {"max_rounds": 3},
        })
        ctx.request.use_kb = False
        tool = ctx.request.agent_config.javascript_tools[0]
        lookup = {"js_tool_js_a_v1": ("javascript", tool)}
        plans = [
            {"tool": "js_tool_js_a_v1", "arguments": {"value": 1}, "external_lookup": lookup},
            {"tool": "js_tool_js_a_v1", "arguments": {"value": 2}, "external_lookup": lookup},
            {"tool": "final", "reason": "enough evidence", "external_lookup": lookup},
        ]
        observed_steps = []

        async def plan(_node, _ctx, *, round_no, previous_steps):
            observed_steps.append([dict(item) for item in previous_steps])
            return plans[round_no - 1]

        with (
            patch.object(RetrievalNode, "_plan_tools", new=plan),
            patch("runtime.pipeline.execute_tool", new=AsyncMock(side_effect=["first result", "second result"])) as execute,
        ):
            result = asyncio.run(RetrievalNode().run(ctx))

        self.assertEqual("RETRIEVAL_DONE", result.stage)
        self.assertEqual(2, execute.await_count)
        self.assertEqual([], observed_steps[0])
        self.assertEqual("first result", observed_steps[1][0]["result"])
        self.assertEqual("second result", observed_steps[2][1]["result"])
        self.assertEqual(["completed", "completed", "skipped"], [step["status"] for step in ctx.agent_tool_steps])
        self.assertEqual(["first result", "second result"], [item["summary"] for item in ctx.agent_tool_summaries])


if __name__ == "__main__":
    unittest.main()
