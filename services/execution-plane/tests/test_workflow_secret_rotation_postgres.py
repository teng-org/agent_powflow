from __future__ import annotations

import hashlib
import os
import pathlib
import re
import sys
import unittest
import uuid
from datetime import datetime, timezone
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg import sql
from psycopg.rows import dict_row

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.workflow_secret_rotation import WorkflowSecretMasterKeyRotator, WorkflowSecretRotationError, main


class WorkflowSecretRotationPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.dsn = os.getenv("EXECUTION_TEST_POSTGRES_DSN", "").strip()
        if not cls.dsn:
            raise unittest.SkipTest("EXECUTION_TEST_POSTGRES_DSN is not configured")
        cls.schema = f"apf_rotation_it_{uuid.uuid4().hex}"
        cls._assert_safe_schema()
        with psycopg.connect(cls.dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
            cur.execute(
                sql.SQL(
                    "CREATE TABLE {}.workflow_secrets ("
                    "secret_id VARCHAR(64) PRIMARY KEY,ciphertext TEXT NOT NULL,fingerprint VARCHAR(16) NOT NULL,"
                    "version INTEGER NOT NULL,updated_by VARCHAR(128) NOT NULL,updated_at TIMESTAMPTZ NOT NULL)"
                ).format(sql.Identifier(cls.schema))
            )

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            if getattr(cls, "dsn", "") and getattr(cls, "schema", ""):
                cls._assert_safe_schema()
                with psycopg.connect(cls.dsn, autocommit=True) as conn, conn.cursor() as cur:
                    cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))
        finally:
            super().tearDownClass()

    @classmethod
    def _assert_safe_schema(cls) -> None:
        if not re.fullmatch(r"apf_rotation_it_[0-9a-f]{32}", cls.schema):
            raise RuntimeError("refusing to manage an unsafe integration-test schema")

    def setUp(self) -> None:
        self.primary = Fernet.generate_key().decode()
        self.previous = Fernet.generate_key().decode()
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("TRUNCATE TABLE {}.workflow_secrets").format(sql.Identifier(self.schema)))

    def _insert(self, secret_id: str, plaintext: str, key: str, *, fingerprint: str | None = None) -> str:
        ciphertext = Fernet(key.encode()).encrypt(plaintext.encode()).decode()
        expected_fingerprint = fingerprint or hashlib.sha256(plaintext.encode()).hexdigest()[:12]
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL(
                    "INSERT INTO {}.workflow_secrets(secret_id,ciphertext,fingerprint,version,updated_by,updated_at) "
                    "VALUES (%s,%s,%s,7,'fixture',%s)"
                ).format(sql.Identifier(self.schema)),
                (secret_id, ciphertext, expected_fingerprint, datetime.now(timezone.utc)),
            )
        return ciphertext

    def _rotator(self) -> WorkflowSecretMasterKeyRotator:
        return WorkflowSecretMasterKeyRotator(
            dsn=self.dsn,
            primary_key=self.primary,
            previous_keys=[self.previous],
            schema=self.schema,
            actor="integration:rotation",
        )

    def test_dry_run_apply_verify_and_idempotent_rerun(self) -> None:
        old_one = self._insert("old-1", "value-one", self.previous)
        old_two = self._insert("old-2", "value-two", self.previous)
        self._insert("new-1", "value-three", self.primary)

        dry_run = self._rotator().execute()
        self.assertEqual((3, 1, 2, 0), (dry_run.total_rows, dry_run.already_primary, dry_run.needs_rotation, dry_run.applied))
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT secret_id,ciphertext FROM {}.workflow_secrets ORDER BY secret_id").format(sql.Identifier(self.schema)))
            before_apply = {row["secret_id"]: row["ciphertext"] for row in cur.fetchall()}
        self.assertEqual(old_one, before_apply["old-1"])
        self.assertEqual(old_two, before_apply["old-2"])

        applied = self._rotator().execute(apply=True)
        self.assertEqual((2, 3), (applied.applied, applied.verified_primary))
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT secret_id,ciphertext,fingerprint,version,updated_by FROM {}.workflow_secrets ORDER BY secret_id").format(
                    sql.Identifier(self.schema)
                )
            )
            rows = list(cur.fetchall())
        self.assertTrue(all(row["version"] == 7 for row in rows))
        self.assertEqual("fixture", next(row for row in rows if row["secret_id"] == "new-1")["updated_by"])
        for row in rows:
            plaintext = Fernet(self.primary.encode()).decrypt(row["ciphertext"].encode()).decode()
            self.assertEqual(hashlib.sha256(plaintext.encode()).hexdigest()[:12], row["fingerprint"])
            with self.assertRaises(InvalidToken):
                Fernet(self.previous.encode()).decrypt(row["ciphertext"].encode())

        rerun = self._rotator().execute(apply=True)
        self.assertEqual((0, 3), (rerun.applied, rerun.verified_primary))

    def test_unknown_ciphertext_aborts_without_partial_update(self) -> None:
        original = self._insert("a-old", "value-one", self.previous)
        self._insert("z-unknown", "value-two", Fernet.generate_key().decode())
        with self.assertRaisesRegex(WorkflowSecretRotationError, "cannot be decrypted"):
            self._rotator().execute(apply=True)
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT ciphertext FROM {}.workflow_secrets WHERE secret_id='a-old'").format(sql.Identifier(self.schema))
            )
            self.assertEqual(original, cur.fetchone()["ciphertext"])

    def test_cli_defaults_to_dry_run_and_requires_explicit_apply_confirmation(self) -> None:
        original = self._insert("cli-old", "cli-value", self.previous)
        environment = {
            "POSTGRES_DSN": self.dsn,
            "WORKFLOW_SECRET_MASTER_KEY": self.primary,
            "WORKFLOW_SECRET_PREVIOUS_MASTER_KEYS": self.previous,
        }
        output = StringIO()
        with patch.dict(os.environ, environment, clear=False), patch.object(
            sys, "argv", ["workflow_secret_rotation", "--schema", self.schema]
        ), redirect_stdout(output):
            self.assertEqual(0, main())
        self.assertIn('"mode":"dry-run"', output.getvalue())
        self.assertIn('"needs_rotation":1', output.getvalue())
        with psycopg.connect(self.dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                sql.SQL("SELECT ciphertext FROM {}.workflow_secrets WHERE secret_id='cli-old'").format(sql.Identifier(self.schema))
            )
            self.assertEqual(original, cur.fetchone()["ciphertext"])

        with patch.dict(os.environ, environment, clear=False), patch.object(
            sys, "argv", ["workflow_secret_rotation", "--schema", self.schema, "--apply"]
        ):
            with self.assertRaisesRegex(WorkflowSecretRotationError, "requires --confirm"):
                main()


if __name__ == "__main__":
    unittest.main()
