from __future__ import annotations

import os
import re
import unittest
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

from retrieval.store import KnowledgeBaseStore
from retrieval.schema_migrations import migrate_database


class _UnusedQdrant:
    def __getattr__(self, name):
        raise AssertionError(f"integration test unexpectedly accessed Qdrant: {name}")


class IsolatedPostgresCase(unittest.TestCase):
    admin_dsn = ""
    schema_name = ""
    store: KnowledgeBaseStore

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin_dsn = os.getenv("EXECUTION_TEST_POSTGRES_DSN", "").strip()
        if not cls.admin_dsn:
            raise unittest.SkipTest("EXECUTION_TEST_POSTGRES_DSN is not configured")
        cls.schema_name = f"apf_it_{uuid.uuid4().hex}"
        cls._assert_safe_schema_name()
        with psycopg.connect(cls.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema_name)))
        try:
            isolated_dsn = make_conninfo(cls.admin_dsn, options=f"-csearch_path={cls.schema_name}")
            cls.store = KnowledgeBaseStore(dsn=isolated_dsn, qdrant_client=_UnusedQdrant())
            migrate_database(isolated_dsn)
            cls.store._ensure_indexes()
        except Exception:
            cls._drop_schema()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            if cls.admin_dsn and cls.schema_name:
                cls._drop_schema()
        finally:
            super().tearDownClass()

    @classmethod
    def _assert_safe_schema_name(cls) -> None:
        if not re.fullmatch(r"apf_it_[0-9a-f]{32}", cls.schema_name):
            raise RuntimeError("refusing to manage an unsafe integration-test schema")

    @classmethod
    def _drop_schema(cls) -> None:
        cls._assert_safe_schema_name()
        with psycopg.connect(cls.admin_dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema_name)))
