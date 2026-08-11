from __future__ import annotations

import sys
from pathlib import Path
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from postgres_test_support import IsolatedPostgresCase, _UnusedQdrant
from models.api_pool_store import ApiPoolMongoStore
from retrieval.schema_migrations import (
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
from retrieval.store import KnowledgeBaseStore


class SchemaMigrationIntegrationTest(IsolatedPostgresCase):
    def test_migration_is_current_and_idempotent(self):
        require_database_current(self.store.dsn)
        self.assertEqual([], migrate_database(self.store.dsn))
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT version,name,checksum,success FROM {MIGRATION_TABLE} ORDER BY version"
            )
            rows = cur.fetchall()
        self.assertEqual(12, len(rows))
        self.assertEqual(1, rows[0]["version"])
        self.assertEqual("execution_store_baseline", rows[0]["name"])
        self.assertTrue(rows[0]["success"])
        self.assertRegex(rows[0]["checksum"], r"^[0-9a-f]{64}$")

    def test_successful_migration_checksum_drift_fails_closed(self):
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT checksum FROM {MIGRATION_TABLE} WHERE version=1")
            checksum = cur.fetchone()["checksum"]
            cur.execute(f"UPDATE {MIGRATION_TABLE} SET checksum=%s WHERE version=1", ("0" * 64,))
        try:
            with self.assertRaises(SchemaMigrationDrift):
                require_database_current(self.store.dsn)
            with self.assertRaises(SchemaMigrationDrift):
                migrate_database(self.store.dsn)
        finally:
            with self.store._conn() as conn, conn.cursor() as cur:
                cur.execute(f"UPDATE {MIGRATION_TABLE} SET checksum=%s WHERE version=1", (checksum,))

    def test_failed_migration_is_recorded_and_can_be_retried(self):
        declared = migrations()

        def fail(_dsn: str) -> None:
            raise RuntimeError("synthetic migration failure")

        failing_version = len(declared) + 1
        failing = Migration(failing_version, "synthetic_failure", "f" * 64, fail)
        try:
            with patch("retrieval.schema_migrations.migrations", return_value=declared + (failing,)):
                with self.assertRaises(SchemaMigrationError):
                    migrate_database(self.store.dsn)
            with self.store._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"SELECT success,error_message FROM {MIGRATION_TABLE} WHERE version=%s",
                    (failing_version,),
                )
                row = cur.fetchone()
            self.assertFalse(row["success"])
            self.assertIn("synthetic migration failure", row["error_message"])
        finally:
            with self.store._conn() as conn, conn.cursor() as cur:
                cur.execute(f"DELETE FROM {MIGRATION_TABLE} WHERE version=%s", (failing_version,))

    def test_invalid_migration_registry_is_rejected_before_database_changes(self):
        declared = migrations()
        invalid_version = len(declared) + 2
        invalid = Migration(invalid_version, "gap", "a" * 64, lambda _dsn: None)
        with patch("retrieval.schema_migrations.migrations", return_value=declared + (invalid,)):
            with self.assertRaisesRegex(SchemaMigrationError, "contiguous"):
                migrate_database(self.store.dsn)
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS count FROM {MIGRATION_TABLE} WHERE version=%s", (invalid_version,))
            self.assertEqual(0, cur.fetchone()["count"])

    def test_database_newer_than_runtime_is_rejected(self):
        future_version = len(migrations()) + 1
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""INSERT INTO {MIGRATION_TABLE}
                (version,name,checksum,success,started_at,applied_at,execution_ms,error_message)
                VALUES (%s,'future_version',%s,TRUE,NOW(),NOW(),0,'')""",
                (future_version, "b" * 64),
            )
        try:
            with self.assertRaises(SchemaVersionTooNew):
                require_database_current(self.store.dsn)
            with self.assertRaises(SchemaVersionTooNew):
                migrate_database(self.store.dsn)
        finally:
            with self.store._conn() as conn, conn.cursor() as cur:
                cur.execute(f"DELETE FROM {MIGRATION_TABLE} WHERE version=%s", (future_version,))

    def test_existing_v1_schema_upgrades_to_v2(self):
        schema_name = f"apf_it_{uuid.uuid4().hex}"
        if not re.fullmatch(r"apf_it_[0-9a-f]{32}", schema_name):
            self.fail("unsafe generated schema name")
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        isolated_dsn = make_conninfo(self.admin_dsn, options=f"-csearch_path={schema_name}")
        declared = migrations()
        try:
            with patch("retrieval.schema_migrations.migrations", return_value=declared[:1]):
                self.assertEqual([1], migrate_database(isolated_dsn))
                require_database_current(isolated_dsn)
            self.assertEqual([2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], migrate_database(isolated_dsn))
            require_database_current(isolated_dsn)
            with psycopg.connect(isolated_dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass('api_pool_settings'),to_regclass('aux_api_pool_endpoints')")
                row = cur.fetchone()
                self.assertIsNotNone(row[0])
                self.assertIsNotNone(row[1])
                cur.execute(
                    """SELECT
                    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='source_object_key'),
                    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='cross_store_repairs' AND column_name='payload_json')"""
                )
                object_columns = cur.fetchone()
                self.assertTrue(object_columns[0])
                self.assertTrue(object_columns[1])
                cur.execute(
                    """SELECT
                    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='extracted_object_size_bytes'),
                    to_regclass('storage_quota_reservations'),
                    to_regclass('idx_storage_quota_reservations_tenant_expiry')"""
                )
                capacity_schema = cur.fetchone()
                self.assertTrue(capacity_schema[0])
                self.assertIsNotNone(capacity_schema[1])
                self.assertIsNotNone(capacity_schema[2])
                cur.execute(
                    """SELECT to_regclass('alertmanager_alerts'),
                    to_regclass('idx_alertmanager_alerts_status_severity_received')"""
                )
                alert_schema = cur.fetchone()
                self.assertIsNotNone(alert_schema[0])
                self.assertIsNotNone(alert_schema[1])
                cur.execute(
                    """SELECT column_default,is_nullable
                    FROM information_schema.columns
                    WHERE table_schema=current_schema() AND table_name='tasks' AND column_name='state_owner'"""
                )
                owner_column = cur.fetchone()
                self.assertIsNotNone(owner_column)
                self.assertEqual("'EXECUTION'::text", owner_column[0])
                self.assertEqual("NO", owner_column[1])
                cur.execute(
                    """SELECT column_default,is_nullable
                    FROM information_schema.columns
                    WHERE table_schema=current_schema() AND table_name='task_batches'
                    AND column_name='cancelled_items'"""
                )
                cancelled_column = cur.fetchone()
                self.assertIsNotNone(cancelled_column)
                self.assertEqual("0", cancelled_column[0])
                self.assertEqual("NO", cancelled_column[1])
                cur.execute("SELECT to_regclass('idx_task_batches_parent_task')")
                self.assertIsNotNone(cur.fetchone()[0])
                cur.execute(
                    """SELECT
                    to_regclass('rag_index_manifests'),
                    to_regclass('idx_chunks_lexical_terms'),
                    EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name='knowledge_bases' AND column_name='active_index_revision'),
                    EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name='chunks' AND column_name='lexical_tf')"""
                )
                rag2_schema = cur.fetchone()
                self.assertIsNotNone(rag2_schema[0])
                self.assertIsNotNone(rag2_schema[1])
                self.assertTrue(rag2_schema[2])
                self.assertTrue(rag2_schema[3])
                cur.execute(
                    """SELECT to_regclass('chunk_parents'),to_regclass('idx_chunks_parent'),
                    EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name='chunks' AND column_name='section_path')"""
                )
                parent_schema = cur.fetchone()
                self.assertIsNotNone(parent_schema[0])
                self.assertIsNotNone(parent_schema[1])
                self.assertTrue(parent_schema[2])
                cur.execute(
                    """SELECT to_regclass('rag_index_chunks'),to_regclass('rag_index_parents'),
                    to_regclass('uq_rag_index_manifests_one_active'),
                    EXISTS (SELECT 1 FROM information_schema.columns
                        WHERE table_name='rag_index_manifests' AND column_name='quality_report')"""
                )
                shadow_schema = cur.fetchone()
                self.assertIsNotNone(shadow_schema[0])
                self.assertIsNotNone(shadow_schema[1])
                self.assertIsNotNone(shadow_schema[2])
                self.assertTrue(shadow_schema[3])
        finally:
            with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))

    def test_unmigrated_schema_is_rejected_without_creating_business_tables(self):
        schema_name = f"apf_it_{uuid.uuid4().hex}"
        if not re.fullmatch(r"apf_it_[0-9a-f]{32}", schema_name):
            self.fail("unsafe generated schema name")
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        isolated_dsn = make_conninfo(self.admin_dsn, options=f"-csearch_path={schema_name}")
        try:
            store = KnowledgeBaseStore(dsn=isolated_dsn, qdrant_client=_UnusedQdrant())
            with self.assertRaises(SchemaMigrationRequired):
                store._ensure_indexes()
            api_pool_store = ApiPoolMongoStore(dsn=isolated_dsn)
            with self.assertRaises(SchemaMigrationRequired):
                api_pool_store._ensure_schema()
            with psycopg.connect(isolated_dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass('knowledge_bases') AS table_name")
                self.assertIsNone(cur.fetchone()[0])
                cur.execute("SELECT to_regclass('api_pool_settings') AS table_name")
                self.assertIsNone(cur.fetchone()[0])
        finally:
            with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))

    def test_v6_backfills_historical_extracted_object_bytes(self):
        schema_name = f"apf_it_{uuid.uuid4().hex}"
        if not re.fullmatch(r"apf_it_[0-9a-f]{32}", schema_name):
            self.fail("unsafe generated schema name")
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        isolated_dsn = make_conninfo(self.admin_dsn, options=f"-csearch_path={schema_name}")
        declared = migrations()
        try:
            with patch("retrieval.schema_migrations.migrations", return_value=declared[:5]):
                self.assertEqual([1, 2, 3, 4, 5], migrate_database(isolated_dsn))
            store = KnowledgeBaseStore(dsn=isolated_dsn, qdrant_client=_UnusedQdrant())
            store._initialized = True
            store.save_document("tenant-backfill", "kb-backfill", "doc-backfill", "历史.txt", "容量数据")
            with store._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE documents SET extracted_object_key='historical/extracted.txt',
                    extracted_object_size_bytes=0
                    WHERE tenant_id='tenant-backfill' AND kb_id='kb-backfill' AND doc_id='doc-backfill'"""
                )
            self.assertEqual([6, 7, 8, 9, 10, 11, 12], migrate_database(isolated_dsn))
            with store._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """SELECT extracted_object_size_bytes FROM documents
                    WHERE tenant_id='tenant-backfill' AND kb_id='kb-backfill' AND doc_id='doc-backfill'"""
                )
                self.assertEqual(len("容量数据".encode("utf-8")), int(cur.fetchone()["extracted_object_size_bytes"]))
        finally:
            with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))

    def test_concurrent_migrators_serialize_and_apply_each_version_once(self):
        schema_name = f"apf_it_{uuid.uuid4().hex}"
        if not re.fullmatch(r"apf_it_[0-9a-f]{32}", schema_name):
            self.fail("unsafe generated schema name")
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema_name)))
        isolated_dsn = make_conninfo(self.admin_dsn, options=f"-csearch_path={schema_name}")
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(migrate_database, (isolated_dsn, isolated_dsn)))
            self.assertEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], sorted(version for result in results for version in result))
            require_database_current(isolated_dsn)
            with psycopg.connect(isolated_dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(f"SELECT version,count(*) FROM {MIGRATION_TABLE} GROUP BY version ORDER BY version")
                self.assertEqual(
                    [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1), (6, 1), (7, 1), (8, 1), (9, 1), (10, 1), (11, 1), (12, 1)],
                    cur.fetchall(),
                )
        finally:
            with psycopg.connect(self.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
                cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
