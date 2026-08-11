from __future__ import annotations

import hashlib
import io
import os
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import patch


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from runtime.object_storage import ObjectStorage, ObjectStorageError
from runtime.object_storage_governance import (
    MANAGED_RULE_ID,
    DocumentBucketPolicy,
    configure_document_bucket,
)
from minio import Minio
from minio.commonconfig import ENABLED, Filter
from minio.lifecycleconfig import Expiration, Rule
import urllib3
from workers.app import tasks as worker_tasks


def _real_minio_client() -> Minio:
    secure = os.getenv("EXECUTION_TEST_MINIO_SECURE", "false").strip().lower() in {"1", "true", "yes", "on"}
    ca_path = os.getenv("EXECUTION_TEST_MINIO_CA_CERT_PATH", "").strip()
    http_client = None
    if secure and ca_path:
        if not os.path.isfile(ca_path):
            raise AssertionError("EXECUTION_TEST_MINIO_CA_CERT_PATH is not a readable file")
        http_client = urllib3.PoolManager(cert_reqs="CERT_REQUIRED", ca_certs=ca_path, retries=False)
    return Minio(
        os.environ["EXECUTION_TEST_MINIO_ENDPOINT"].strip(),
        access_key=os.getenv("EXECUTION_TEST_MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("EXECUTION_TEST_MINIO_SECRET_KEY", "minioadmin"),
        secure=secure,
        http_client=http_client,
    )


class _Response:
    def __init__(self, data: bytes) -> None:
        self.stream = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class InMemoryMinio:
    def __init__(self) -> None:
        self.buckets: set[str] = set()
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict] = {}
        self.fail_remove = False
        self.fail_put_after: int | None = None
        self.put_calls = 0
        self.versioning_status: str | None = None
        self.lifecycle_rules: list = []

    def bucket_exists(self, bucket: str) -> bool:
        return bucket in self.buckets

    def make_bucket(self, bucket: str) -> None:
        self.buckets.add(bucket)

    def put_object(self, bucket: str, key: str, data, length: int, **kwargs) -> None:
        self.put_calls += 1
        if self.fail_put_after is not None and self.put_calls > self.fail_put_after:
            raise RuntimeError("injected MinIO put failure")
        raw = data.read()
        if len(raw) != length:
            raise AssertionError("declared length differs from object data")
        self.objects[(bucket, key)] = raw
        self.metadata[(bucket, key)] = kwargs

    def get_object(self, bucket: str, key: str):
        return _Response(self.objects[(bucket, key)])

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = False, include_version: bool = False):
        del recursive, include_version
        return [
            SimpleNamespace(object_name=key, version_id=None, is_delete_marker=False)
            for stored_bucket, key in self.objects
            if stored_bucket == bucket and key.startswith(prefix)
        ]

    def remove_object(self, bucket: str, key: str, version_id=None) -> None:
        del version_id
        if self.fail_remove:
            raise RuntimeError("injected MinIO delete failure")
        self.objects.pop((bucket, key), None)

    def get_bucket_versioning(self, bucket: str):
        return type("Versioning", (), {"status": self.versioning_status})()

    def set_bucket_versioning(self, bucket: str, config) -> None:
        self.versioning_status = config.status

    def get_bucket_lifecycle(self, bucket: str):
        if not self.lifecycle_rules:
            return None
        return type("Lifecycle", (), {"rules": self.lifecycle_rules})()

    def set_bucket_lifecycle(self, bucket: str, config) -> None:
        self.lifecycle_rules = list(config.rules)


class ObjectStorageTest(TestCase):
    def setUp(self) -> None:
        self.client = InMemoryMinio()
        self.storage = ObjectStorage(client=self.client, bucket="agent-powflow-tests")

    def test_document_keys_are_tenant_scoped_and_filename_safe(self):
        source_a, extracted_a = self.storage.document_keys("tenant/a", "kb-a", "doc-a", "../report 1.txt")
        source_b, _ = self.storage.document_keys("tenant/b", "kb-a", "doc-a", "../report 1.txt")
        self.assertNotEqual(source_a, source_b)
        self.assertTrue(source_a.endswith("/source/report%201.txt"))
        self.assertTrue(extracted_a.endswith("/extracted/text.txt"))
        self.assertNotIn("tenant/a", source_a)

    def test_long_unicode_filename_uses_bounded_collision_safe_segment(self):
        prefix = "中国人工智能系列白皮书——大模型技术（2023版）"
        first = f"{prefix}-" + "作者甲乙丙丁" * 30 + ".pdf"
        second = f"{prefix}-" + "作者甲乙丙戊" * 30 + ".pdf"

        source_first, _ = self.storage.document_keys("tenant", "kb", "doc-1", first)
        source_repeat, _ = self.storage.document_keys("tenant", "kb", "doc-1", first)
        source_second, _ = self.storage.document_keys("tenant", "kb", "doc-2", second)
        first_segment = source_first.rsplit("/", 1)[-1]

        self.assertEqual(source_first, source_repeat)
        self.assertNotEqual(first_segment, source_second.rsplit("/", 1)[-1])
        self.assertLessEqual(len(first_segment), 200)
        self.assertTrue(first_segment.endswith(".pdf"))
        self.assertRegex(first_segment, r"-[0-9a-f]{16}\.pdf$")

    def test_put_get_checksum_and_delete(self):
        stored = self.storage.put_bytes("v1/item.txt", b"evidence", "text/plain")
        self.assertEqual(hashlib.sha256(b"evidence").hexdigest(), stored.sha256)
        self.assertEqual(b"evidence", self.storage.get_bytes(stored.bucket, stored.key, stored.sha256))
        with self.assertRaisesRegex(ObjectStorageError, "checksum mismatch"):
            self.storage.get_bytes(stored.bucket, stored.key, "0" * 64)
        self.storage.delete_object(stored.bucket, stored.key)
        self.assertNotIn((stored.bucket, stored.key), self.client.objects)

    def test_download_limit_fails_closed(self):
        stored = self.storage.put_bytes("v1/large.bin", b"12345")
        with patch.dict(os.environ, {"OBJECT_STORAGE_MAX_READ_BYTES": "4"}):
            with self.assertRaisesRegex(ObjectStorageError, "read limit"):
                self.storage.get_bytes(stored.bucket, stored.key)

    def test_worker_resolves_checksum_verified_text_reference(self):
        stored = self.storage.put_bytes("v1/extracted.txt", "中文 evidence".encode("utf-8"), "text/plain")
        payload = {
            "content": "",
            "object_bucket": stored.bucket,
            "content_object_key": stored.key,
            "content_object_sha256": stored.sha256,
        }
        with patch.object(worker_tasks, "object_storage", self.storage):
            self.assertEqual("中文 evidence", worker_tasks._payload_content(payload))
            payload["content_object_sha256"] = "0" * 64
            with self.assertRaisesRegex(ObjectStorageError, "checksum mismatch"):
                worker_tasks._payload_content(payload)

    def test_governance_status_reports_missing_and_configured_controls(self):
        missing = self.storage.governance_status()
        self.assertEqual("CRITICAL", missing["health"])
        self.assertFalse(missing["bucket_exists"])

        self.client.make_bucket(self.storage.bucket)
        unconfigured = self.storage.governance_status()
        self.assertEqual("WARN", unconfigured["health"])
        self.assertIn("bucket versioning is not enabled", unconfigured["reasons"])
        configure_document_bucket(self.storage, DocumentBucketPolicy(90, 2))
        self.storage.secure = True
        configured = self.storage.governance_status()
        self.assertEqual("OK", configured["health"])
        self.assertEqual(1, configured["lifecycle_rule_count"])

    def test_document_bucket_governance_is_idempotent(self):
        policy = DocumentBucketPolicy(90, 2)
        self.client.lifecycle_rules = [
            Rule(
                ENABLED,
                expiration=Expiration(days=365),
                rule_filter=Filter(prefix="archive/"),
                rule_id="operator-owned-rule",
            )
        ]
        first = configure_document_bucket(self.storage, policy)
        second = configure_document_bucket(self.storage, policy)
        self.assertEqual("ENABLED", first["versioning_status"])
        self.assertEqual(1, first["managed_rule_count"])
        self.assertEqual(1, second["managed_rule_count"])
        self.assertEqual(2, len(self.client.lifecycle_rules))
        self.assertEqual(
            {"operator-owned-rule", MANAGED_RULE_ID},
            {getattr(rule, "_rule_id", "") for rule in self.client.lifecycle_rules},
        )

    def test_document_bucket_policy_rejects_unsafe_retention_values(self):
        with patch.dict(os.environ, {"MINIO_DOCUMENT_NONCURRENT_EXPIRY_DAYS": "0"}):
            with self.assertRaises(ObjectStorageError):
                DocumentBucketPolicy.from_env()


class RealMinioIntegrationTest(TestCase):
    @skipUnless(os.getenv("EXECUTION_TEST_MINIO_ENDPOINT", "").strip(), "EXECUTION_TEST_MINIO_ENDPOINT is not configured")
    def test_real_minio_put_get_checksum_and_cleanup(self):
        client = _real_minio_client()
        bucket = f"apf-it-{uuid.uuid4().hex}"
        self.assertRegex(bucket, r"^apf-it-[0-9a-f]{32}$")
        storage = ObjectStorage(client=client, bucket=bucket)
        stored = None
        try:
            stored = storage.put_bytes("probe/evidence.txt", b"real minio evidence", "text/plain")
            self.assertEqual(b"real minio evidence", storage.get_bytes(bucket, stored.key, stored.sha256))
            storage.delete_object(bucket, stored.key)
            stored = None
        finally:
            if not re.fullmatch(r"apf-it-[0-9a-f]{32}", bucket):
                self.fail("refusing unsafe MinIO test bucket cleanup")
            if stored is not None:
                storage.delete_object(bucket, stored.key)
            if client.bucket_exists(bucket):
                client.remove_bucket(bucket)

    @skipUnless(os.getenv("EXECUTION_TEST_MINIO_ENDPOINT", "").strip(), "EXECUTION_TEST_MINIO_ENDPOINT is not configured")
    def test_real_minio_governance_enables_versioning_and_is_idempotent(self):
        client = _real_minio_client()
        bucket = f"apf-it-{uuid.uuid4().hex}"
        self.assertRegex(bucket, r"^apf-it-[0-9a-f]{32}$")
        storage = ObjectStorage(client=client, bucket=bucket)
        key = "v1/governance/versioned.txt"
        try:
            first = configure_document_bucket(storage, DocumentBucketPolicy(90, 2))
            second = configure_document_bucket(storage, DocumentBucketPolicy(90, 2))
            self.assertEqual("ENABLED", first["versioning_status"])
            self.assertEqual(1, second["managed_rule_count"])
            storage.put_bytes(key, b"version-one")
            storage.put_bytes(key, b"version-two")
            versions = [item for item in client.list_objects(bucket, recursive=True, include_version=True) if item.object_name == key]
            self.assertGreaterEqual(len(versions), 2)
            self.assertGreaterEqual(len({item.version_id for item in versions}), 2)
            storage.delete_object(bucket, key)
            remaining = [item for item in client.list_objects(bucket, recursive=True, include_version=True) if item.object_name == key]
            self.assertEqual([], remaining)
        finally:
            if not re.fullmatch(r"apf-it-[0-9a-f]{32}", bucket):
                self.fail("refusing unsafe MinIO governance test bucket cleanup")
            if client.bucket_exists(bucket):
                for item in client.list_objects(bucket, recursive=True, include_version=True):
                    client.remove_object(bucket, item.object_name, version_id=item.version_id)
                client.remove_bucket(bucket)
