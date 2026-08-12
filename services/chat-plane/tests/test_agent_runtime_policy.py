from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import unittest


CHAT_ROOT = Path(__file__).resolve().parents[1]
if str(CHAT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHAT_ROOT))

from runtime.models import ChatContext, ChatRequest, ChatRunState, ChatSession
from runtime.pipeline import PromptNode, _agent_memory_mode, _memory_config_for


class AgentRuntimePolicyTest(unittest.TestCase):
    def _context(self, agent_config: dict) -> ChatContext:
        return ChatContext(
            tenant_id="tenant-a",
            actor_id="actor-a",
            trace_id="trace-a",
            source_service="test",
            session=ChatSession(
                session_id="session-a", tenant_id="tenant-a", actor_id="actor-a",
                title="test", created_at="2026-08-12T00:00:00Z", updated_at="2026-08-12T00:00:00Z",
            ),
            run=ChatRunState(
                run_id="run-a", session_id="session-a", tenant_id="tenant-a", actor_id="actor-a",
                mode="AGENT_API", started_at="2026-08-12T00:00:00Z", updated_at="2026-08-12T00:00:00Z",
            ),
            request=ChatRequest(message="hello", use_kb=False, agent_config=agent_config),
        )

    def test_prompt_injects_server_variable_skill_boundary_and_prohibited_rules(self) -> None:
        ctx = self._context({
            "instructions": "Today is {{current_datetime}}.",
            "prohibited_rules": "Never reveal secrets at {{current_datetime}}.",
            "prompt_variables": [{"key": "current_datetime", "calendar": "both", "timezone": "Asia/Shanghai"}],
            "skills": [{"skill_id": "s1", "name": "Tone", "version": 2, "instructions": "Reply briefly."}],
        })
        asyncio.run(PromptNode().run(ctx))
        system = ctx.prompt_messages[0]["content"]
        self.assertEqual(1, system.count("{{current_datetime}}"))
        self.assertIn("公历", system)
        self.assertIn("农历", system)
        self.assertIn("可用 Skills", system)
        self.assertNotIn("Reply briefly.", system)
        self.assertIn("受控工具执行", system)
        self.assertIn("禁止规则｜最高优先级", system)

    def test_agent_memory_overrides_platform_defaults(self) -> None:
        ctx = self._context({
            "memory": {
                "mode": "short_term", "preset": "customer_service",
                "recent_messages_limit": 7, "context_token_budget": 900,
                "summary_enabled": False, "user_profile_enabled": False,
                "episodic_enabled": False, "session_recall_enabled": False,
            }
        })
        cfg = _memory_config_for(ctx)
        self.assertEqual("short_term", _agent_memory_mode(ctx))
        self.assertEqual(7, cfg.recent_messages_limit)
        self.assertEqual(900, cfg.budget_total_tokens)
        self.assertFalse(cfg.hierarchical_enabled)
        self.assertFalse(cfg.user_profile_enabled)


if __name__ == "__main__":
    unittest.main()
