from __future__ import annotations

import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from postgres_test_support import IsolatedPostgresCase
from retrieval.store import ChunkRecord


class FaultInjectingQdrant:
    """Small deterministic Qdrant double that can fail after partial mutation."""

    def __init__(self) -> None:
        self.collections: dict[str, dict] = {}
        self.vector_sizes: dict[str, int] = {}
        self.fail_upsert_after: int | None = None
        self.upsert_batch_sizes: list[int] = []
        self.fail_delete = False
        self.fail_delete_collection = False

    def collection_exists(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def create_collection(self, collection_name: str, vectors_config) -> None:
        self.collections.setdefault(collection_name, {})
        self.vector_sizes[collection_name] = int(vectors_config.size)

    def get_collection(self, collection_name: str):
        vectors = SimpleNamespace(size=self.vector_sizes[collection_name])
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)))

    def delete_collection(self, collection_name: str) -> None:
        if self.fail_delete_collection:
            raise RuntimeError("injected Qdrant collection delete failure")
        self.collections.pop(collection_name, None)
        self.vector_sizes.pop(collection_name, None)

    @staticmethod
    def _filter_values(points_selector) -> dict[str, object]:
        conditions = points_selector.filter.must
        return {condition.key: condition.match.value for condition in conditions}

    def delete(self, collection_name: str, points_selector) -> None:
        if self.fail_delete:
            raise RuntimeError("injected qdrant delete failure")
        expected = self._filter_values(points_selector)
        points = self.collections.setdefault(collection_name, {})
        for point_id, point in list(points.items()):
            payload = point.payload or {}
            if all(payload.get(key) == value for key, value in expected.items()):
                del points[point_id]

    def upsert(self, collection_name: str, points: list) -> None:
        self.upsert_batch_sizes.append(len(points))
        target = self.collections.setdefault(collection_name, {})
        for index, point in enumerate(points, start=1):
            target[str(point.id)] = point
            if self.fail_upsert_after is not None and index >= self.fail_upsert_after:
                raise RuntimeError("injected qdrant partial upsert failure")

    def search(self, collection_name: str, **_) -> list:
        return [SimpleNamespace(payload=point.payload, score=1.0) for point in self.collections.get(collection_name, {}).values()]


class FaultInjectingObjectStore:
    def __init__(self) -> None:
        self.fail_delete = False
        self.objects: set[tuple[str, str]] = set()

    def delete_object(self, bucket: str, key: str) -> None:
        if self.fail_delete:
            raise RuntimeError("injected MinIO delete failure")
        self.objects.discard((bucket, key))


class CrossStoreConsistencyIntegrationTest(IsolatedPostgresCase):
    def setUp(self) -> None:
        self.qdrant = FaultInjectingQdrant()
        self.store.qdrant = self.qdrant
        self.store.object_storage = None

    def _prepare_document(self, suffix: str, texts: list[str]) -> tuple[str, str, str]:
        tenant_id = f"tenant-cross-{suffix}"
        kb_id = f"kb-cross-{suffix}"
        doc_id = f"doc-cross-{suffix}"
        self.store.create_kb(tenant_id, kb_id, f"Cross-store {suffix}")
        self.store.save_document(tenant_id, kb_id, doc_id, "evidence.txt", "\n".join(texts))
        self.store.set_document_chunking_preview(tenant_id, kb_id, doc_id, len(texts))
        self.store.replace_chunks_preview(tenant_id, kb_id, doc_id, texts, "evidence.txt")
        for row in self.store.list_document_chunks_for_embedding(tenant_id, kb_id, doc_id):
            self.store.persist_chunk_embedding(
                ChunkRecord(
                    tenant_id=tenant_id,
                    kb_id=kb_id,
                    doc_id=doc_id,
                    chunk_id=row["chunk_id"],
                    chunk_index=row["chunk_index"],
                    text=row["text"],
                    embedding=[float(row["chunk_index"] + 1), 0.5, 0.25],
                    metadata=row.get("metadata") or {},
                )
            )
        self.store.set_document_status(tenant_id, kb_id, doc_id, "INDEXING")
        return tenant_id, kb_id, doc_id

    def test_partial_qdrant_publish_is_hidden_and_retry_is_idempotent(self):
        tenant_id, kb_id, doc_id = self._prepare_document("partial", ["alpha", "beta"])
        self.qdrant.fail_upsert_after = 1

        with self.assertRaisesRegex(RuntimeError, "partial upsert"):
            self.store.publish_document_embeddings(tenant_id, kb_id, doc_id)

        self.assertEqual("INDEXING", self.store.get_document(tenant_id, kb_id, doc_id)["status"])
        self.assertEqual([], self.store.search_chunks(tenant_id, kb_id, [1.0, 0.0, 0.0], 10))

        self.store.mark_document_failed(tenant_id, kb_id, doc_id)
        self.qdrant.fail_upsert_after = None
        counters = self.store.publish_document_embeddings(tenant_id, kb_id, doc_id)
        self.assertEqual({"chunk_count": 2, "embedded_count": 2}, counters)
        self.assertEqual([], self.store.search_chunks(tenant_id, kb_id, [1.0, 0.0, 0.0], 10))

        self.store.set_document_status(tenant_id, kb_id, doc_id, "INDEXED")
        hits = self.store.search_chunks(tenant_id, kb_id, [1.0, 0.0, 0.0], 10)
        self.assertEqual({"alpha", "beta"}, {hit["text"] for hit in hits})
        collection = self.qdrant.collections[self.store._collection_name(tenant_id, kb_id)]
        self.assertEqual(2, len(collection))

    def test_document_generation_publish_removes_previous_points(self):
        tenant_id, kb_id, doc_id = self._prepare_document("replace", ["old alpha", "old beta"])
        self.store.publish_document_embeddings(tenant_id, kb_id, doc_id)
        self.store.set_document_status(tenant_id, kb_id, doc_id, "INDEXED")

        self.store.save_document(tenant_id, kb_id, doc_id, "evidence.txt", "new gamma")
        self.store.set_document_chunking_preview(tenant_id, kb_id, doc_id, 1)
        self.store.replace_chunks_preview(tenant_id, kb_id, doc_id, ["new gamma"], "evidence.txt")
        row = self.store.list_document_chunks_for_embedding(tenant_id, kb_id, doc_id)[0]
        self.store.persist_chunk_embedding(
            ChunkRecord(tenant_id, kb_id, doc_id, row["chunk_id"], 0, row["text"], [1.0, 0.5, 0.25], row.get("metadata") or {})
        )
        self.store.set_document_status(tenant_id, kb_id, doc_id, "INDEXING")
        self.store.publish_document_embeddings(tenant_id, kb_id, doc_id)
        self.store.set_document_status(tenant_id, kb_id, doc_id, "INDEXED")

        hits = self.store.search_chunks(tenant_id, kb_id, [1.0, 0.0, 0.0], 10)
        self.assertEqual(["new gamma"], [hit["text"] for hit in hits])

    def test_large_document_publish_is_split_into_bounded_qdrant_batches(self):
        texts = [f"chunk {index}" for index in range(35)]
        tenant_id, kb_id, doc_id = self._prepare_document("batched", texts)

        with patch.dict(os.environ, {"QDRANT_UPSERT_BATCH_SIZE": "16"}):
            counters = self.store.publish_document_embeddings(tenant_id, kb_id, doc_id)

        self.assertEqual({"chunk_count": 35, "embedded_count": 35}, counters)
        self.assertEqual([16, 16, 3], self.qdrant.upsert_batch_sizes)
        collection = self.qdrant.collections[self.store._collection_name(tenant_id, kb_id)]
        self.assertEqual(35, len(collection))

    def test_delete_failure_can_leave_physical_orphan_but_never_visible_data(self):
        tenant_id, kb_id, doc_id = self._prepare_document("delete", ["orphan candidate"])
        self.store.publish_document_embeddings(tenant_id, kb_id, doc_id)
        self.store.set_document_status(tenant_id, kb_id, doc_id, "INDEXED")
        self.qdrant.fail_delete = True

        self.assertTrue(self.store.delete_document(tenant_id, kb_id, doc_id))

        self.assertIsNone(self.store.get_document(tenant_id, kb_id, doc_id))
        self.assertEqual([], self.store.search_chunks(tenant_id, kb_id, [1.0, 0.0, 0.0], 10))
        pending = self.store.list_cross_store_repairs(status="PENDING")
        self.assertEqual(1, len(pending))
        self.assertEqual("DELETE_DOCUMENT_POINTS", pending[0]["operation"])

        failed = self.store.repair_cross_store_operations()
        self.assertEqual(1, failed["failed"])
        self.assertEqual(1, failed["remaining"])
        self.qdrant.fail_delete = False
        repaired = self.store.repair_cross_store_operations()
        self.assertEqual(1, repaired["succeeded"])
        self.assertEqual(0, repaired["remaining"])
        self.assertEqual(0, len(self.qdrant.collections[self.store._collection_name(tenant_id, kb_id)]))

    def test_dimension_mismatch_fails_without_deleting_existing_collection(self):
        tenant_id, kb_id, doc_id = self._prepare_document("dimension", ["new dimension"])
        collection_name = self.store._collection_name(tenant_id, kb_id)
        sentinel = SimpleNamespace(id="existing", payload={"doc_id": "another-document"})
        self.qdrant.collections[collection_name] = {"existing": sentinel}
        self.qdrant.vector_sizes[collection_name] = 2

        with self.assertRaisesRegex(RuntimeError, "KB-wide reindex"):
            self.store.publish_document_embeddings(tenant_id, kb_id, doc_id)

        self.assertIs(sentinel, self.qdrant.collections[collection_name]["existing"])
        self.assertEqual(2, self.qdrant.vector_sizes[collection_name])

    def test_minio_delete_failure_is_durable_and_repairable(self):
        tenant_id, kb_id, doc_id = self._prepare_document("minio-delete", ["stored source"])
        objects = FaultInjectingObjectStore()
        objects.objects = {("docs", "source.bin"), ("docs", "text.txt")}
        objects.fail_delete = True
        self.store.object_storage = objects
        self.store.set_document_object_metadata(
            tenant_id, kb_id, doc_id,
            source_bucket="docs", source_key="source.bin", source_sha256="a" * 64,
            source_size_bytes=12, source_content_type="application/octet-stream",
            extracted_key="text.txt", extracted_sha256="b" * 64,
        )

        self.assertTrue(self.store.delete_document(tenant_id, kb_id, doc_id))
        pending = [row for row in self.store.list_cross_store_repairs(status="PENDING") if row["store_name"] == "MINIO"]
        self.assertEqual(2, len(pending))
        self.assertEqual({"source.bin", "text.txt"}, {row["payload_json"]["key"] for row in pending})

        objects.fail_delete = False
        result = self.store.repair_cross_store_operations(limit=10)
        self.assertGreaterEqual(result["succeeded"], 2)
        self.assertEqual(set(), objects.objects)

    def test_kb_delete_persists_qdrant_and_minio_cleanup_obligations(self):
        tenant_id, kb_id, doc_id = self._prepare_document("kb-delete", ["whole KB cleanup"])
        collection_name = self.store._collection_name(tenant_id, kb_id)
        self.qdrant.collections[collection_name] = {}
        self.qdrant.vector_sizes[collection_name] = 3
        self.qdrant.fail_delete_collection = True
        objects = FaultInjectingObjectStore()
        objects.objects = {("docs", "source.bin"), ("docs", "text.txt")}
        objects.fail_delete = True
        self.store.object_storage = objects
        self.store.set_document_object_metadata(
            tenant_id, kb_id, doc_id,
            source_bucket="docs", source_key="source.bin", source_sha256="a" * 64,
            source_size_bytes=12, source_content_type="application/octet-stream",
            extracted_key="text.txt", extracted_sha256="b" * 64,
        )

        self.assertTrue(self.store.delete_kb(tenant_id, kb_id))
        pending = self.store.list_cross_store_repairs(status="PENDING")
        scoped = [row for row in pending if row["tenant_id"] == tenant_id and row["kb_id"] == kb_id]
        self.assertEqual(3, len(scoped))
        self.assertEqual({"DELETE_COLLECTION", "DELETE_OBJECT"}, {row["operation"] for row in scoped})

        self.qdrant.fail_delete_collection = False
        objects.fail_delete = False
        result = self.store.repair_cross_store_operations(limit=20)
        self.assertGreaterEqual(result["succeeded"], 3)
        self.assertNotIn(collection_name, self.qdrant.collections)
        self.assertEqual(set(), objects.objects)

    def test_move_partial_qdrant_failure_is_hidden_durable_and_idempotently_repairable(self):
        tenant_id, source_kb_id, doc_id = self._prepare_document("move-repair", ["move alpha", "move beta"])
        target_kb_id = "kb-cross-move-target"
        self.store.create_kb(tenant_id, target_kb_id, "Move target")
        self.store.publish_document_embeddings(tenant_id, source_kb_id, doc_id)
        self.store.set_document_status(tenant_id, source_kb_id, doc_id, "INDEXED")
        self.store.set_document_object_metadata(
            tenant_id,
            source_kb_id,
            doc_id,
            source_bucket="docs",
            source_key="v1/source.bin",
            source_sha256="a" * 64,
            source_size_bytes=12,
            source_content_type="application/octet-stream",
            extracted_key="v1/text.txt",
            extracted_sha256="b" * 64,
        )
        self.qdrant.fail_upsert_after = 1

        with self.assertRaisesRegex(RuntimeError, "document move repair pending"):
            self.store.move_document(tenant_id, source_kb_id, target_kb_id, doc_id)

        self.assertIsNone(self.store.get_document(tenant_id, source_kb_id, doc_id))
        target = self.store.get_document(tenant_id, target_kb_id, doc_id)
        self.assertEqual("MOVING", target["status"])
        self.assertEqual("v1/source.bin", target["source_object_key"])
        self.assertEqual("v1/text.txt", target["extracted_object_key"])
        self.assertEqual([], self.store.search_chunks(tenant_id, source_kb_id, [1.0, 0.0, 0.0], 10))
        self.assertEqual([], self.store.search_chunks(tenant_id, target_kb_id, [1.0, 0.0, 0.0], 10))
        failed = [
            row for row in self.store.list_cross_store_repairs(status="FAILED")
            if row["operation"] == "MOVE_DOCUMENT_POINTS" and row["doc_id"] == doc_id
        ]
        self.assertEqual(1, len(failed))
        self.assertEqual(source_kb_id, failed[0]["payload_json"]["source_kb_id"])
        self.assertEqual(target_kb_id, failed[0]["payload_json"]["target_kb_id"])

        self.qdrant.fail_upsert_after = None
        self.assertTrue(self.store.move_document(tenant_id, source_kb_id, target_kb_id, doc_id))

        repaired = [
            row for row in self.store.list_cross_store_repairs(status="SUCCEEDED")
            if row["repair_id"] == failed[0]["repair_id"]
        ]
        self.assertEqual(1, len(repaired))
        self.assertEqual("INDEXED", self.store.get_document(tenant_id, target_kb_id, doc_id)["status"])
        self.assertEqual(
            {"move alpha", "move beta"},
            {hit["text"] for hit in self.store.search_chunks(tenant_id, target_kb_id, [1.0, 0.0, 0.0], 10)},
        )
        source_collection = self.store._collection_name(tenant_id, source_kb_id)
        self.assertEqual(0, len(self.qdrant.collections[source_collection]))

    def test_collection_writes_are_serialized_across_concurrent_documents(self):
        tenant_id, kb_id = "tenant-collection-lock", "kb-collection-lock"
        self.store.create_kb(tenant_id, kb_id, "Collection lock")
        active = 0
        maximum_active = 0
        counter_lock = threading.Lock()
        original_upsert = self.qdrant.upsert

        def slow_upsert(collection_name: str, points: list) -> None:
            nonlocal active, maximum_active
            with counter_lock:
                active += 1
                maximum_active = max(maximum_active, active)
            try:
                time.sleep(0.1)
                original_upsert(collection_name, points)
            finally:
                with counter_lock:
                    active -= 1

        self.qdrant.upsert = slow_upsert

        def replace(doc_id: str) -> None:
            self.store.save_document(tenant_id, kb_id, doc_id, f"{doc_id}.txt", doc_id)
            self.store.replace_chunks(
                tenant_id,
                kb_id,
                doc_id,
                [ChunkRecord(tenant_id, kb_id, doc_id, f"{doc_id}-0", 0, doc_id, [0.1, 0.2, 0.3], {})],
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(replace, ("doc-a", "doc-b")))

        self.assertEqual(1, maximum_active)
        collection = self.qdrant.collections[self.store._collection_name(tenant_id, kb_id)]
        self.assertEqual(2, len(collection))

    def test_stale_in_progress_claim_is_recovered_and_retried(self):
        objects = FaultInjectingObjectStore()
        objects.objects = {("docs", "stale.txt")}
        self.store.object_storage = objects
        repair_id = self.store.enqueue_cross_store_repair(
            store_name="MINIO",
            operation="DELETE_OBJECT",
            tenant_id="tenant-stale",
            kb_id="kb-stale",
            payload={"bucket": "docs", "key": "stale.txt"},
        )
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE cross_store_repairs SET status='IN_PROGRESS',updated_at=NOW() - INTERVAL '10 minutes'
                WHERE repair_id=%s""",
                (repair_id,),
            )

        result = self.store.repair_cross_store_operations(limit=10, stale_after_seconds=60)

        self.assertEqual(1, result["stale_recovered"])
        self.assertGreaterEqual(result["succeeded"], 1)
        self.assertEqual(set(), objects.objects)
        item = [row for row in self.store.list_cross_store_repairs() if row["repair_id"] == repair_id][0]
        self.assertEqual("SUCCEEDED", item["status"])

    def test_repair_stats_report_counts_age_attempts_and_store_breakdown(self):
        before = self.store.get_cross_store_repair_stats()
        self.store.enqueue_cross_store_repair("MINIO", "DELETE_OBJECT", "tenant-stats", "kb-stats")
        failed_id = self.store.enqueue_cross_store_repair("QDRANT", "DELETE_COLLECTION", "tenant-stats", "kb-stats")
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE cross_store_repairs SET status='FAILED',attempts=2,
                updated_at=NOW() - INTERVAL '2 minutes',created_at=NOW() - INTERVAL '2 minutes'
                WHERE repair_id=%s""",
                (failed_id,),
            )

        stats = self.store.get_cross_store_repair_stats()

        self.assertEqual(before["total"] + 2, stats["total"])
        self.assertEqual(before["open_count"] + 2, stats["open_count"])
        self.assertEqual(before["actionable_count"] + 2, stats["actionable_count"])
        self.assertEqual(before["status_counts"]["PENDING"] + 1, stats["status_counts"]["PENDING"])
        self.assertEqual(before["status_counts"]["FAILED"] + 1, stats["status_counts"]["FAILED"])
        self.assertEqual(before["total_attempts"] + 2, stats["total_attempts"])
        self.assertGreaterEqual(stats["oldest_open_age_seconds"], 119)
        self.assertEqual({"MINIO", "QDRANT"}, {row["store_name"] for row in stats["by_store_status"]})


if __name__ == "__main__":
    import unittest

    unittest.main()
