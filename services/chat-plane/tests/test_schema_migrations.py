from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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

from runtime.schema_migrations import (
    MIGRATION_TABLE,
    Migration,
    SchemaMigrationDrift,
    SchemaMigrationError,
    SchemaMigrationRequired,
    SchemaVersionTooNew,
    migrate_database,
    migrations,
    require_database_current,
)
from runtime.api_pool import ChatApiPoolManager
from runtime.store import ChatStore


class ChatSchemaMigrationIntegrationTest(unittest.TestCase):
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

    def tearDown(self) -> None:
        if not re.fullmatch(r"apf_chat_it_[0-9a-f]{32}", self.schema_name):
            self.fail("refusing to drop an unsafe test schema")
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema_name)))

    def test_empty_schema_migrates_to_current_and_is_idempotent(self) -> None:
        self.assertEqual([1, 2], migrate_database(self.dsn))
        self.assertEqual([], migrate_database(self.dsn))
        require_database_current(self.dsn)
        with psycopg.connect(self.dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT version,name,success FROM {MIGRATION_TABLE} ORDER BY version")
            self.assertEqual([(1, "chat_core_store", True), (2, "chat_memory_runtime", True)], cur.fetchall())
            cur.execute("SELECT to_regclass('chat_sessions'),to_regclass('chat_api_pool_state'),to_regclass('chat_runs')")
            self.assertTrue(all(cur.fetchone()))

    def test_v1_schema_upgrades_to_v2_without_losing_data(self) -> None:
        declared = migrations()
        with patch("runtime.schema_migrations.migrations", return_value=declared[:1]):
            self.assertEqual([1], migrate_database(self.dsn))
            with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
                cur.execute("INSERT INTO chat_sessions VALUES ('s1','t1','u1','title',NULL,NOW(),NOW())")
        self.assertEqual([2], migrate_database(self.dsn))
        require_database_current(self.dsn)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT title FROM chat_sessions WHERE session_id='s1'")
            self.assertEqual("title", cur.fetchone()[0])
            cur.execute("SELECT to_regclass('chat_memory_rerank_jobs')")
            self.assertIsNotNone(cur.fetchone()[0])

    def test_legacy_schema_is_adopted_idempotently(self) -> None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE chat_sessions (session_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, actor_id TEXT NOT NULL, title TEXT NOT NULL, kb_id TEXT NULL, created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL)")
            cur.execute("INSERT INTO chat_sessions VALUES ('legacy','tenant','actor','kept',NULL,NOW(),NOW())")
        self.assertEqual([1, 2], migrate_database(self.dsn))
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT title FROM chat_sessions WHERE session_id='legacy'")
            self.assertEqual("kept", cur.fetchone()[0])

    def test_unmigrated_schema_is_rejected_without_business_ddl(self) -> None:
        with self.assertRaises(SchemaMigrationRequired):
            require_database_current(self.dsn)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('chat_sessions')")
            self.assertIsNone(cur.fetchone()[0])

    def test_business_stores_validate_and_do_not_bootstrap_schema(self) -> None:
        history = ChatStore.__new__(ChatStore)
        history._postgres_dsn = self.dsn
        history._pg_schema_ready = False
        history._pg_schema_last_try_at = 0.0
        history._pg_schema_retry_seconds = 0.0
        history._pg_conn = None
        pool = ChatApiPoolManager.__new__(ChatApiPoolManager)
        pool._postgres_dsn = self.dsn
        pool._pg_schema_ready = False
        pool._pg_conn = None
        with self.assertRaises(SchemaMigrationRequired):
            history._ensure_pg_table()
        with self.assertRaises(SchemaMigrationRequired):
            pool._pg_ensure_schema_locked()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('chat_sessions'),to_regclass('chat_api_pool_state')")
            self.assertEqual((None, None), cur.fetchone())

    def test_checksum_drift_and_newer_database_fail_closed(self) -> None:
        migrate_database(self.dsn)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {MIGRATION_TABLE} SET checksum=%s WHERE version=1", ("0" * 64,))
        with self.assertRaises(SchemaMigrationDrift):
            require_database_current(self.dsn)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE {MIGRATION_TABLE} SET checksum=%s WHERE version=1", (migrations()[0].checksum,))
            cur.execute(f"INSERT INTO {MIGRATION_TABLE}(version,name,checksum,success,started_at,applied_at,execution_ms,error_message) VALUES(3,'future',%s,TRUE,NOW(),NOW(),0,'')", ("f" * 64,))
        with self.assertRaises(SchemaVersionTooNew):
            require_database_current(self.dsn)

    def test_failed_migration_is_recorded_and_retryable(self) -> None:
        declared = migrations()

        def fail(_dsn: str) -> None:
            raise RuntimeError("synthetic chat migration failure")

        failing = Migration(3, "synthetic_failure", "f" * 64, fail)
        with patch("runtime.schema_migrations.migrations", return_value=declared + (failing,)):
            with self.assertRaises(SchemaMigrationError):
                migrate_database(self.dsn)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT success,error_message FROM {MIGRATION_TABLE} WHERE version=3")
            row = cur.fetchone()
        self.assertFalse(row[0])
        self.assertIn("synthetic chat migration failure", row[1])

        recovered = Migration(3, "synthetic_failure", "f" * 64, lambda _dsn: None)
        with patch("runtime.schema_migrations.migrations", return_value=declared + (recovered,)):
            self.assertEqual([3], migrate_database(self.dsn))
            require_database_current(self.dsn)

    def test_invalid_registry_is_rejected_before_database_changes(self) -> None:
        invalid = migrations() + (Migration(4, "gap", "a" * 64, lambda _dsn: None),)
        with patch("runtime.schema_migrations.migrations", return_value=invalid):
            with self.assertRaisesRegex(SchemaMigrationError, "contiguous"):
                migrate_database(self.dsn)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (MIGRATION_TABLE,))
            self.assertIsNone(cur.fetchone()[0])

    def test_concurrent_migrators_apply_each_version_once(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(migrate_database, (self.dsn, self.dsn)))
        self.assertEqual([1, 2], sorted(version for result in results for version in result))
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(f"SELECT version,count(*) FROM {MIGRATION_TABLE} GROUP BY version ORDER BY version")
            self.assertEqual([(1, 1), (2, 1)], cur.fetchall())


if __name__ == "__main__":
    unittest.main()
