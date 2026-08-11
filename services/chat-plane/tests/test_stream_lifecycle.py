from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import sys
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


CHAT_ROOT = Path(__file__).resolve().parents[1]
if str(CHAT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHAT_ROOT))

from runtime.models import ChatContext, ChatRequest
from runtime.pipeline import ChatPipeline
from runtime.schema_migrations import migrate_database
from runtime.store import ChatStore


class _ControlledLlm:
    def __init__(self, *, fail: bool = False, delay: float = 0.01) -> None:
        self.fail = fail
        self.delay = delay

    def runtime_meta(self) -> dict:
        return {
            "base_url": "controlled://provider",
            "model": "controlled-model",
            "key_source": "test",
            "api_key_configured": True,
        }

    async def stream_chat(self, messages, **_kwargs):
        if self.fail:
            raise RuntimeError("controlled stream failure")
        marker = str((messages[-1] if messages else {}).get("content") or "response")[-8:]
        for index in range(12):
            await asyncio.sleep(self.delay)
            yield f"{marker}:{index:02d};"

    async def complete_text(self, _messages, **_kwargs) -> str:
        if self.fail:
            raise RuntimeError("controlled fallback failure")
        return "Controlled completion response with enough content for lifecycle verification."

    async def embed_text(self, _text: str, **_kwargs) -> list[float]:
        return [1.0, 0.0]


class ChatStreamLifecycleIntegrationTest(unittest.TestCase):
    admin_dsn = os.getenv("CHAT_TEST_POSTGRES_DSN", "").strip()

    @classmethod
    def setUpClass(cls) -> None:
        if not cls.admin_dsn:
            raise unittest.SkipTest("CHAT_TEST_POSTGRES_DSN is not configured")

    def setUp(self) -> None:
        self.schema_name = f"apf_chat_it_{uuid.uuid4().hex}"
        self.assertRegex(self.schema_name, r"^apf_chat_it_[0-9a-f]{32}$")
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema_name)))
        self.dsn = make_conninfo(self.admin_dsn, options=f"-csearch_path={self.schema_name}")
        migrate_database(self.dsn)

        def store_setting(key: str, default=None):
            if key == "chat_history_store_backend":
                return "postgres_only"
            return default

        with patch.dict(os.environ, {"CHAT_HISTORY_POSTGRES_DSN": self.dsn}), patch(
            "runtime.store.get_setting", side_effect=store_setting
        ):
            self.store = ChatStore()

    def tearDown(self) -> None:
        conn = getattr(self.store, "_pg_conn", None)
        if conn is not None and not getattr(conn, "closed", True):
            conn.close()
        if not re.fullmatch(r"apf_chat_it_[0-9a-f]{32}", self.schema_name):
            self.fail("refusing to drop an unsafe test schema")
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema_name)))

    def _context(self, message: str) -> ChatContext:
        session = self.store.create_session("tenant-a", "actor-a", title="New conversation", kb_id=None)
        run = self.store.create_run(session.session_id, session.tenant_id, session.actor_id, mode="CHAT")
        return ChatContext(
            tenant_id=session.tenant_id,
            actor_id=session.actor_id,
            trace_id=str(uuid.uuid4()),
            source_service="chat-plane-test",
            session=session,
            run=run,
            request=ChatRequest(message=message, use_kb=False, use_agent=False),
        )

    def test_active_cancel_reaches_terminal_cancelled_without_assistant_message(self) -> None:
        ctx = self._context("cancel-me")
        pipeline = ChatPipeline()

        async def scenario() -> list[dict]:
            events: list[dict] = []
            with patch("runtime.pipeline.chat_store", self.store), patch(
                "runtime.pipeline.llm_client", _ControlledLlm(delay=0.02)
            ):
                async for event in pipeline.run_stream(ctx, cancel_on_disconnect=True):
                    events.append(event)
                    if event.get("event") == "token":
                        self.assertTrue(self.store.cancel_run(ctx.run.run_id))
            return events

        events = asyncio.run(scenario())
        run = self.store.get_run(ctx.run.run_id)
        self.assertEqual("CANCELLED", run.status)
        self.assertEqual("CANCELLED", run.stage)
        self.assertTrue(any(event.get("event") == "cancelled" for event in events))
        self.assertEqual(["user"], [item.role for item in self.store.list_messages(ctx.session.session_id)])

    def test_provider_and_fallback_failure_mark_run_failed(self) -> None:
        ctx = self._context("fail-me")
        pipeline = ChatPipeline()

        async def scenario() -> list[dict]:
            with patch("runtime.pipeline.chat_store", self.store), patch(
                "runtime.pipeline.llm_client", _ControlledLlm(fail=True)
            ):
                return [event async for event in pipeline.run_stream(ctx, cancel_on_disconnect=True)]

        events = asyncio.run(scenario())
        run = self.store.get_run(ctx.run.run_id)
        self.assertEqual("FAILED", run.status)
        self.assertEqual("FAILED", run.stage)
        self.assertIn("controlled fallback failure", run.error_message or "")
        self.assertTrue(any(event.get("event") == "error" for event in events))
        self.assertFalse(any(event.get("event") == "done" for event in events))
        self.assertEqual(["user"], [item.role for item in self.store.list_messages(ctx.session.session_id)])

    def test_consumer_disconnect_cancels_background_pipeline(self) -> None:
        ctx = self._context("disconnect-me")
        pipeline = ChatPipeline()

        async def scenario() -> dict:
            with patch("runtime.pipeline.chat_store", self.store), patch(
                "runtime.pipeline.llm_client", _ControlledLlm(delay=0.05)
            ):
                stream = pipeline.run_stream(ctx, cancel_on_disconnect=True)
                first = await anext(stream)
                await stream.aclose()
                await asyncio.sleep(0)
                return first

        first = asyncio.run(scenario())
        self.assertEqual(ctx.run.run_id, first.get("run_id"))
        run = self.store.get_run(ctx.run.run_id)
        self.assertEqual("CANCELLED", run.status)
        self.assertEqual("client disconnected", run.error_message)
        self.assertEqual(["user"], [item.role for item in self.store.list_messages(ctx.session.session_id)])

    def test_concurrent_streams_keep_events_isolated(self) -> None:
        first_ctx = self._context("first-run")
        second_ctx = self._context("second-run")
        pipeline = ChatPipeline()

        async def collect(ctx: ChatContext) -> list[dict]:
            return [event async for event in pipeline.run_stream(ctx, cancel_on_disconnect=True)]

        async def scenario() -> tuple[list[dict], list[dict]]:
            with patch("runtime.pipeline.chat_store", self.store), patch(
                "runtime.pipeline.llm_client", _ControlledLlm(delay=0.002)
            ):
                return tuple(await asyncio.gather(collect(first_ctx), collect(second_ctx)))

        first_events, second_events = asyncio.run(scenario())
        self.assertTrue(first_events and second_events)
        self.assertEqual({first_ctx.run.run_id}, {event.get("run_id") for event in first_events})
        self.assertEqual({second_ctx.run.run_id}, {event.get("run_id") for event in second_events})
        self.assertEqual("COMPLETED", self.store.get_run(first_ctx.run.run_id).status)
        self.assertEqual("COMPLETED", self.store.get_run(second_ctx.run.run_id).status)

    def test_terminal_run_cannot_be_cancelled_or_overwritten(self) -> None:
        ctx = self._context("terminal-run")
        completed = self.store.complete_run(ctx.run.run_id, "COMPLETED")
        self.assertEqual("COMPLETED", completed.status)
        self.assertFalse(self.store.cancel_run(ctx.run.run_id))
        unchanged = self.store.complete_run(ctx.run.run_id, "FAILED", "late failure")
        self.assertEqual("COMPLETED", unchanged.status)
        self.assertIsNone(unchanged.error_message)


if __name__ == "__main__":
    unittest.main()
