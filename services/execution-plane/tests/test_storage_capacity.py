from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from postgres_test_support import IsolatedPostgresCase


class StorageCapacityIntegrationTest(IsolatedPostgresCase):
    def test_concurrent_reservations_cannot_oversubscribe_tenant_quota(self):
        barrier = Barrier(2)

        def reserve(reservation_id: str) -> dict:
            barrier.wait(timeout=5)
            return self.store.reserve_storage_quota(
                tenant_id="tenant-concurrent",
                requested_bytes=60,
                quota_bytes=100,
                ttl_seconds=300,
                reservation_id=reservation_id,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(reserve, ("reservation-a", "reservation-b")))

        self.assertEqual(1, sum(1 for result in results if result["allowed"]))
        self.assertEqual(1, sum(1 for result in results if not result["allowed"]))
        denied = next(result for result in results if not result["allowed"])
        self.assertEqual(120, denied["projected_bytes"])
        self.assertEqual(100, denied["quota_bytes"])

        allowed = next(result for result in results if result["allowed"])
        self.assertTrue(self.store.release_storage_quota(allowed["reservation_id"]))
        self.assertFalse(self.store.release_storage_quota(allowed["reservation_id"]))

    def test_capacity_stats_include_committed_and_reserved_bytes(self):
        tenant_id = "tenant-capacity-stats"
        kb_id = "kb-capacity"
        doc_id = "doc-capacity"
        self.store.save_document(tenant_id, kb_id, doc_id, "capacity.txt", "payload")
        self.store.set_document_object_metadata(
            tenant_id,
            kb_id,
            doc_id,
            source_bucket="documents",
            source_key="source-key",
            source_sha256="a" * 64,
            source_size_bytes=40,
            source_content_type="text/plain",
            extracted_key="extracted-key",
            extracted_sha256="b" * 64,
            extracted_size_bytes=20,
        )
        reservation = self.store.reserve_storage_quota(
            tenant_id=tenant_id,
            requested_bytes=10,
            quota_bytes=100,
            reservation_id="reservation-stats",
        )
        self.assertTrue(reservation["allowed"])
        self.assertEqual(70, reservation["projected_bytes"])

        stats = self.store.storage_capacity_stats()
        item = next(row for row in stats["items"] if row["tenant_id"] == tenant_id)
        self.assertEqual(40, item["source_bytes"])
        self.assertEqual(20, item["extracted_bytes"])
        self.assertEqual(60, item["used_bytes"])
        self.assertEqual(10, item["reserved_bytes"])
        self.assertEqual(70, item["projected_bytes"])
        self.assertEqual(1, item["document_count"])
        self.assertEqual(1, item["reservation_count"])

    def test_disabled_quota_does_not_create_reservation(self):
        result = self.store.reserve_storage_quota(
            tenant_id="tenant-unlimited",
            requested_bytes=123,
            quota_bytes=0,
            reservation_id="must-not-be-inserted",
        )
        self.assertTrue(result["allowed"])
        self.assertEqual("", result["reservation_id"])
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*)::bigint AS count FROM storage_quota_reservations WHERE tenant_id=%s",
                ("tenant-unlimited",),
            )
            self.assertEqual(0, int(cur.fetchone()["count"]))

    def test_document_replacement_reserves_only_positive_capacity_delta(self):
        tenant_id = "tenant-replacement"
        kb_id = "kb-replacement"
        doc_id = "doc-replacement"
        self.store.save_document(tenant_id, kb_id, doc_id, "replacement.txt", "old")
        self.store.set_document_object_metadata(
            tenant_id,
            kb_id,
            doc_id,
            source_bucket="documents",
            source_key="replacement-source",
            source_sha256="e" * 64,
            source_size_bytes=40,
            source_content_type="text/plain",
            extracted_key="replacement-extracted",
            extracted_sha256="f" * 64,
            extracted_size_bytes=20,
        )
        larger = self.store.reserve_storage_quota(
            tenant_id=tenant_id,
            kb_id=kb_id,
            doc_id=doc_id,
            requested_bytes=70,
            quota_bytes=100,
            reservation_id="replacement-larger",
        )
        self.assertTrue(larger["allowed"])
        self.assertEqual(60, larger["existing_bytes"])
        self.assertEqual(10, larger["capacity_delta_bytes"])
        self.assertEqual(70, larger["projected_bytes"])
        self.store.release_storage_quota(larger["reservation_id"])

        smaller = self.store.reserve_storage_quota(
            tenant_id=tenant_id,
            kb_id=kb_id,
            doc_id=doc_id,
            requested_bytes=50,
            quota_bytes=60,
            reservation_id="replacement-smaller",
        )
        self.assertTrue(smaller["allowed"])
        self.assertEqual(0, smaller["capacity_delta_bytes"])
        self.assertEqual("", smaller["reservation_id"])
        self.assertEqual(60, smaller["projected_bytes"])

    def test_expired_reservations_are_reclaimed_before_admission(self):
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO storage_quota_reservations
                (reservation_id,tenant_id,reserved_bytes,expires_at,created_at)
                VALUES ('expired-reservation','tenant-expired',90,NOW() - INTERVAL '1 second',NOW() - INTERVAL '1 hour')"""
            )
        result = self.store.reserve_storage_quota(
            tenant_id="tenant-expired",
            requested_bytes=80,
            quota_bytes=100,
            reservation_id="fresh-reservation",
        )
        self.assertTrue(result["allowed"])
        self.assertEqual(0, result["reserved_bytes"])
        self.assertEqual(80, result["projected_bytes"])
