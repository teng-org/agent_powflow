from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
import uvicorn


CHAT_ROOT = Path(__file__).resolve().parents[1]
if str(CHAT_ROOT) not in sys.path:
    sys.path.insert(0, str(CHAT_ROOT))

from api.app import main as chat_api
from runtime.pipeline import ChatPipeline
from runtime.schema_migrations import migrate_database
from runtime.store import ChatStore
from test_stream_lifecycle import _ControlledLlm


class ChatApiStreamLifecycleIntegrationTest(unittest.TestCase):
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
            return "postgres_only" if key == "chat_history_store_backend" else default

        with patch.dict(os.environ, {"CHAT_HISTORY_POSTGRES_DSN": self.dsn}), patch(
            "runtime.store.get_setting", side_effect=store_setting
        ):
            self.store = ChatStore()
        self.pipeline = ChatPipeline()
        self.llm = _ControlledLlm(delay=0.03)
        self.patchers = [
            patch.object(chat_api, "chat_store", self.store),
            patch.object(chat_api, "chat_pipeline", self.pipeline),
            patch("runtime.pipeline.chat_store", self.store),
            patch("runtime.pipeline.llm_client", self.llm),
        ]
        for patcher in self.patchers:
            patcher.start()
        chat_api.app.dependency_overrides[chat_api.require_auth] = lambda: {
            "tenant_id": "tenant-a",
            "actor_id": "actor-a",
            "roles": ["TENANT_USER"],
        }

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        self.port = int(self.socket.getsockname()[1])
        config = uvicorn.Config(chat_api.app, log_level="error", lifespan="on")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(
            target=self.server.run,
            kwargs={"sockets": [self.socket]},
            daemon=True,
        )
        self.thread.start()
        deadline = time.time() + 5
        while not self.server.started and self.thread.is_alive() and time.time() < deadline:
            time.sleep(0.01)
        if not self.server.started:
            self.fail("test Uvicorn server did not start")
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=8)

    def tearDown(self) -> None:
        self.client.close()
        self.server.should_exit = True
        self.thread.join(timeout=5)
        self.socket.close()
        chat_api.app.dependency_overrides.clear()
        for patcher in reversed(self.patchers):
            patcher.stop()
        conn = getattr(self.store, "_pg_conn", None)
        if conn is not None and not getattr(conn, "closed", True):
            conn.close()
        if not re.fullmatch(r"apf_chat_it_[0-9a-f]{32}", self.schema_name):
            self.fail("refusing to drop an unsafe test schema")
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema_name)))

    def _create_session(self) -> str:
        response = self.client.post("/api/v1/chat/sessions", json={"title": "Lifecycle test"})
        self.assertEqual(200, response.status_code, response.text)
        return str(response.json()["item"]["session_id"])

    @staticmethod
    def _payload(line: str) -> dict | None:
        if not line.startswith("data: "):
            return None
        return json.loads(line[6:])

    def _get_run(self, run_id: str) -> dict:
        response = self.client.get(f"/api/v1/chat/runs/{run_id}")
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["item"]

    def test_active_cancel_endpoint_terminates_sse_run(self) -> None:
        session_id = self._create_session()
        run_id = ""
        events: list[str] = []
        with self.client.stream(
            "POST",
            f"/api/v1/chat/sessions/{session_id}/messages/stream",
            json={"message": "cancel through API", "use_kb": False, "use_agent": False},
        ) as response:
            self.assertEqual(200, response.status_code)
            for line in response.iter_lines():
                payload = self._payload(line)
                if payload is None:
                    continue
                run_id = run_id or str(payload.get("run_id") or "")
                event = str(payload.get("event") or "")
                events.append(event)
                if event == "stage" and payload.get("stage") == "GENERATION_STREAMING":
                    cancelled = self.client.post(f"/api/v1/chat/runs/{run_id}/cancel")
                    self.assertEqual(200, cancelled.status_code, cancelled.text)
                    self.assertTrue(cancelled.json()["cancel_requested"])
                    self.assertTrue(cancelled.json()["execution_interrupted"])
                if event == "cancelled":
                    break
        self.assertTrue(run_id)
        self.assertIn("cancelled", events)
        self.assertEqual("CANCELLED", self._get_run(run_id)["status"])

    def test_provider_failure_is_reported_as_failed_sse_run(self) -> None:
        self.llm.fail = True
        session_id = self._create_session()
        payloads: list[dict] = []
        with self.client.stream(
            "POST",
            f"/api/v1/chat/sessions/{session_id}/messages/stream",
            json={"message": "fail through API", "use_kb": False, "use_agent": False},
        ) as response:
            self.assertEqual(200, response.status_code)
            for line in response.iter_lines():
                payload = self._payload(line)
                if payload is not None:
                    payloads.append(payload)
        run_id = str(payloads[0]["run_id"])
        self.assertTrue(any(payload.get("event") == "error" for payload in payloads))
        run = self._get_run(run_id)
        self.assertEqual("FAILED", run["status"])
        self.assertIn("controlled fallback failure", run["error_message"])

    def test_network_disconnect_cancels_server_side_run(self) -> None:
        session_id = self._create_session()
        run_id = ""
        with self.client.stream(
            "POST",
            f"/api/v1/chat/sessions/{session_id}/messages/stream",
            json={"message": "disconnect through API", "use_kb": False, "use_agent": False},
        ) as response:
            self.assertEqual(200, response.status_code)
            for line in response.iter_lines():
                payload = self._payload(line)
                if payload is None:
                    continue
                run_id = run_id or str(payload.get("run_id") or "")
                if payload.get("event") == "stage":
                    break
        self.assertTrue(run_id)
        deadline = time.time() + 3
        run = self._get_run(run_id)
        while run["status"] == "RUNNING" and time.time() < deadline:
            time.sleep(0.03)
            run = self._get_run(run_id)
        self.assertEqual("CANCELLED", run["status"])
        self.assertEqual("client disconnected", run["error_message"])


if __name__ == "__main__":
    unittest.main()
