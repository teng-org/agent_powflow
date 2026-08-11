from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from postgres_test_support import IsolatedPostgresCase
from retrieval.store import InvalidTaskStateTransition, TaskIdentityConflict
from workers.app import tasks as worker_tasks


class KnowledgeBaseStoreIntegrationTest(IsolatedPostgresCase):
    def test_parent_child_structure_is_persistent_and_used_for_context(self):
        tenant_id, kb_id, doc_id = "tenant-parent", "kb-parent", "doc-parent"
        self.store.create_kb(tenant_id, kb_id, "Parent KB")
        self.store.save_document(tenant_id, kb_id, doc_id, "guide.md", "alpha child\nbeta child")
        self.store.replace_chunks_preview(
            tenant_id, kb_id, doc_id, ["alpha child", "beta child"], "guide.md"
        )
        structure = [
            {
                "parent_id": "parent-1", "parent_index": 0,
                "parent_text": "# Security\n\nalpha child\n\nbeta child",
                "section_path": ["Security"], "block_type": "paragraph",
                "page_start": 2, "page_end": 2,
            },
            {
                "parent_id": "parent-1", "parent_index": 0,
                "parent_text": "# Security\n\nalpha child\n\nbeta child",
                "section_path": ["Security"], "block_type": "paragraph",
                "page_start": 2, "page_end": 2,
            },
        ]
        self.store.replace_chunk_structure(tenant_id, kb_id, doc_id, structure, "guide.md")
        self.store.set_document_status(tenant_id, kb_id, doc_id, "INDEXED")

        items = self.store.search_lexical_chunks(tenant_id, kb_id, "alpha", top_k=1)
        self.assertEqual("parent-1", items[0]["parent_id"])
        self.assertEqual(["Security"], items[0]["section_path"])
        self.assertEqual(2, items[0]["page_start"])
        expanded = self.store.expand_chunk_context(tenant_id, kb_id, items, window=1)
        self.assertTrue(expanded[0]["parent_context"])
        self.assertEqual("parent", expanded[0]["context_strategy"])
        self.assertIn("beta child", expanded[0]["text"])
        self.assertEqual("alpha child", expanded[0]["matched_text"])

    def test_rag2_lexical_search_manifest_and_tenant_isolation(self):
        self.store.create_kb("tenant-rag2", "kb-rag2", "RAG 2 KB")
        self.store.save_document(
            "tenant-rag2", "kb-rag2", "doc-target", "manual.md",
            "设备告警代码 TS-999 表示冷却系统压力异常。",
        )
        self.store.replace_chunks_preview(
            "tenant-rag2", "kb-rag2", "doc-target",
            ["设备告警代码 TS-999 表示冷却系统压力异常。", "普通维护说明。"],
            "manual.md",
        )
        self.store.set_document_status("tenant-rag2", "kb-rag2", "doc-target", "INDEXED")

        self.store.create_kb("tenant-other", "kb-rag2", "Other tenant KB")
        self.store.save_document("tenant-other", "kb-rag2", "doc-other", "secret.md", "TS-999 private")
        self.store.replace_chunks_preview(
            "tenant-other", "kb-rag2", "doc-other", ["TS-999 private"], "secret.md"
        )
        self.store.set_document_status("tenant-other", "kb-rag2", "doc-other", "INDEXED")

        items = self.store.search_lexical_chunks(
            "tenant-rag2", "kb-rag2", "TS-999 冷却系统", top_k=5
        )
        self.assertTrue(items)
        self.assertEqual("doc-target", items[0]["doc_id"])
        self.assertEqual("keyword", items[0]["rank_method"])
        self.assertTrue(all(item["doc_id"] != "doc-other" for item in items))

        filtered = self.store.search_lexical_chunks(
            "tenant-rag2", "kb-rag2", "TS-999", top_k=5, doc_ids=["missing"]
        )
        self.assertEqual([], filtered)

        manifest = self.store.refresh_rag_index_manifest("tenant-rag2", "kb-rag2")
        self.assertEqual(
            self.store.get_active_index_revision("tenant-rag2", "kb-rag2"),
            manifest["index_revision"],
        )
        self.assertEqual(2, manifest["chunk_count"])
        self.assertEqual(2, manifest["lexical_chunk_count"])

        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE chunks SET lexical_terms=ARRAY[]::text[],lexical_tf='{}'::jsonb,lexical_token_count=0,index_revision='legacy-v1' "
                "WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s",
                ("tenant-rag2", "kb-rag2", "doc-target"),
            )
        backfill = self.store.backfill_rag2_lexical_features(batch_size=10)
        self.assertEqual(2, backfill["processed"])
        self.assertEqual(0, self.store.backfill_rag2_lexical_features(batch_size=10)["processed"])

        expanded = self.store.expand_chunk_context(
            "tenant-rag2", "kb-rag2", [items[0]], window=1
        )
        self.assertEqual(items[0]["text"], expanded[0]["matched_text"])
        self.assertEqual(2, len(expanded[0]["context_chunk_ids"]))
        self.assertEqual([0, 1], expanded[0]["context_range"])

        parent_backfill = self.store.backfill_parent_child_structure(batch_documents=10)
        self.assertGreaterEqual(parent_backfill["documents_processed"], 1)
        backfilled_items = self.store.search_lexical_chunks(
            "tenant-rag2", "kb-rag2", "TS-999", top_k=1
        )
        parent_expanded = self.store.expand_chunk_context(
            "tenant-rag2", "kb-rag2", backfilled_items, window=1
        )
        self.assertTrue(parent_expanded[0]["parent_context"])
        self.assertIn("context_strategy", parent_expanded[0])

    def test_task_identity_is_immutable_and_duplicate_create_is_idempotent(self):
        self.assertTrue(self.store.create_task_record("task-identity", "tenant-a", "kb-a", "doc-a", "DOC_INGEST", "PARSING"))
        self.assertFalse(self.store.create_task_record("task-identity", "tenant-a", "kb-a", "doc-a", "DOC_INGEST", "PARSING"))

        with self.assertRaises(TaskIdentityConflict):
            self.store.create_task_record("task-identity", "tenant-b", "kb-b", "doc-b", "DOC_INGEST", "PARSING")
        with self.assertRaises(TaskIdentityConflict):
            self.store.append_task_event(
                "task-identity", "tenant-b", "kb-a", "doc-a", "TASK_PROGRESS", "PARSING", {"progress_pct": 5}
            )

        record = self.store.get_task("task-identity", tenant_id="tenant-a")
        self.assertEqual("tenant-a", record["tenant_id"])
        self.assertIsNone(self.store.get_task("task-identity", tenant_id="tenant-b"))
        self.assertEqual(1, len(self.store.list_task_events("task-identity", tenant_id="tenant-a")))
        self.assertEqual([], self.store.list_task_events("task-identity", tenant_id="tenant-b"))

    def test_terminal_task_and_progress_are_irreversible(self):
        self.store.create_task_record("task-terminal", "tenant-a", "kb-a", "doc-a", "DOC_INGEST", "PARSING")
        self.store.update_task_record("task-terminal", "RUNNING", "EMBEDDING")
        self.store.update_task_progress("task-terminal", "EMBEDDING", 4, 10, 40)
        self.store.update_task_record("task-terminal", "SUCCEEDED", "INDEXED")
        self.store.update_task_record("task-terminal", "SUCCEEDED", "INDEXED")

        with self.assertRaises(InvalidTaskStateTransition):
            self.store.update_task_record("task-terminal", "RUNNING", "PARSING")
        with self.assertRaises(InvalidTaskStateTransition):
            self.store.update_task_record("task-terminal", "SUCCEEDED", "MOVED")
        self.store.update_task_progress("task-terminal", "PARSING", 0, 1, 0)

        record = self.store.get_task("task-terminal", tenant_id="tenant-a")
        self.assertEqual("SUCCEEDED", record["status"])
        self.assertEqual("INDEXED", record["stage"])
        self.assertEqual(100, record["progress_pct"])
        self.assertEqual(3, len(self.store.list_task_events("task-terminal", tenant_id="tenant-a")))

    def test_task_cancel_and_dedup_locks_are_tenant_scoped(self):
        self.store.create_task_record("task-cancel", "tenant-a", "kb-a", "doc-a", "DOC_INGEST", "PARSING")
        self.assertFalse(self.store.request_cancel_task("task-cancel", tenant_id="tenant-b"))
        self.assertTrue(self.store.request_cancel_task("task-cancel", tenant_id="tenant-a"))

        self.store.create_task_record("lock-a", "tenant-a", "kb-a", "doc-x", "DOC_INGEST", "PARSING")
        self.store.create_task_record("lock-b", "tenant-a", "kb-a", "doc-x", "DOC_INGEST", "PARSING")
        self.store.create_task_record("lock-c", "tenant-b", "kb-a", "doc-x", "DOC_INGEST", "PARSING")

        first = self.store.acquire_task_dedup_lock(
            tenant_id="tenant-a", kb_id="kb-a", doc_id="doc-x", lock_scope="INGEST", task_id="lock-a"
        )
        duplicate = self.store.acquire_task_dedup_lock(
            tenant_id="tenant-a", kb_id="kb-a", doc_id="doc-x", lock_scope="INGEST", task_id="lock-b"
        )
        other_tenant = self.store.acquire_task_dedup_lock(
            tenant_id="tenant-b", kb_id="kb-a", doc_id="doc-x", lock_scope="INGEST", task_id="lock-c"
        )
        self.assertTrue(first["acquired"])
        self.assertFalse(duplicate["acquired"])
        self.assertEqual("lock-a", duplicate["task_id"])
        self.assertTrue(other_tenant["acquired"])

    def test_kb_visibility_binding_and_mutation_are_tenant_scoped(self):
        self.store.create_kb("tenant-a", "shared-id", "Tenant A KB")
        self.store.create_kb("tenant-b", "shared-id", "Tenant B KB")
        self.assertEqual("Tenant A KB", self.store.get_kb("tenant-a", "shared-id")["name"])
        self.assertEqual("Tenant B KB", self.store.get_kb("tenant-b", "shared-id")["name"])
        self.assertEqual("tenant-a", self.store.resolve_kb_access_tenant("tenant-a", "shared-id"))

        self.store.set_kb_visibility("tenant-a", "shared-id", "SHARED_ALL")
        self.assertEqual("tenant-a", self.store.resolve_kb_access_tenant("tenant-c", "shared-id"))
        self.store.set_kb_visibility("tenant-a", "shared-id", "PRIVATE")
        self.assertIsNone(self.store.resolve_kb_access_tenant("tenant-c", "shared-id"))
        self.store.upsert_kb_binding("tenant-a", "shared-id", "tenant-c", "tester")
        self.assertEqual("tenant-a", self.store.resolve_kb_access_tenant("tenant-c", "shared-id"))

        self.assertIsNone(self.store.rename_kb("tenant-c", "shared-id", "hijacked"))
        self.assertEqual("Tenant A KB", self.store.get_kb("tenant-a", "shared-id")["name"])

    def test_describe_documents_returns_only_exact_requested_tenant_kb_set(self):
        self.store.create_kb("tenant-a", "kb-describe", "Describe KB")
        self.store.create_kb("tenant-a", "kb-other", "Other KB")
        self.store.create_kb("tenant-b", "kb-describe", "Other tenant KB")
        for doc_id in ("doc-a", "doc-b", "doc-unrequested"):
            self.store.save_document(
                "tenant-a", "kb-describe", doc_id, f"{doc_id}.txt", f"content for {doc_id}"
            )
        self.store.save_document("tenant-a", "kb-other", "doc-a", "other.txt", "other kb")
        self.store.save_document("tenant-b", "kb-describe", "doc-a", "tenant-b.txt", "other tenant")

        items = self.store.describe_documents(
            "tenant-a", "kb-describe", ["doc-b", "doc-missing", "doc-a", "doc-b"]
        )

        self.assertEqual({"doc-a", "doc-b"}, {item["doc_id"] for item in items})
        self.assertEqual({"doc-a.txt", "doc-b.txt"}, {item["filename"] for item in items})
        self.assertTrue(all(set(item) == {"doc_id", "filename", "status", "chunk_count"} for item in items))

    def test_kb_delete_removes_batch_lock_and_summary_cache_rows(self):
        tenant_id, kb_id, doc_id = "tenant-cleanup", "kb-cleanup", "doc-cleanup"
        self.store.create_kb(tenant_id, kb_id, "Cleanup KB")
        self.store.save_document(tenant_id, kb_id, doc_id, "cleanup.txt", "cleanup content")
        self.store.create_task_record(
            "task-cleanup", tenant_id, kb_id, doc_id, "DOC_TAG", "QUEUED",
            batch_id="batch-cleanup", batch_name="cleanup batch",
        )
        self.store.acquire_task_dedup_lock(
            tenant_id=tenant_id, kb_id=kb_id, doc_id=doc_id,
            lock_scope="DOC_TAG", task_id="task-cleanup",
        )
        self.store.create_task_batch(
            batch_id="batch-cleanup", tenant_id=tenant_id, kb_id=kb_id,
            operation="tag", batch_name="cleanup batch", params={"tags": ["cleanup"]},
            total_items=1, created_by="tester", parent_task_id="parent-cleanup",
        )
        self.store.upsert_summary_segment_cache(
            tenant_id=tenant_id, kb_id=kb_id, doc_id=doc_id,
            doc_signature="signature", segment_index=0, segment_hash="hash",
            overview="overview", summary="summary",
        )

        self.assertTrue(self.store.delete_kb(tenant_id, kb_id))

        with self.store._conn() as conn, conn.cursor() as cur:
            for table in ("task_batches", "task_dedup_locks", "summary_segment_cache", "tasks", "documents"):
                cur.execute(
                    f"SELECT count(*) AS count FROM {table} WHERE tenant_id=%s AND kb_id=%s",
                    (tenant_id, kb_id),
                )
                self.assertEqual(0, cur.fetchone()["count"], table)

    def test_zero_chunk_overlap_survives_save_update_and_move(self):
        self.store.create_kb("tenant-a", "kb-zero-source", "Zero source")
        self.store.create_kb("tenant-a", "kb-zero-target", "Zero target")
        self.store.save_document(
            "tenant-a",
            "kb-zero-source",
            "doc-zero",
            "zero.txt",
            "zero-overlap content",
            chunk_method="sentence",
            chunk_size=128,
            chunk_overlap=0,
        )

        saved = self.store.get_document("tenant-a", "kb-zero-source", "doc-zero")
        self.assertEqual(0, saved["chunk_overlap"])

        self.store.set_document_chunk_config(
            "tenant-a", "kb-zero-source", "doc-zero", "paragraph", 256, 0
        )
        self.store.set_document_object_metadata(
            "tenant-a", "kb-zero-source", "doc-zero",
            source_bucket="documents", source_key="source/object.txt", source_sha256="a" * 64,
            source_size_bytes=20, source_content_type="text/plain",
            extracted_key="extracted/object.txt", extracted_sha256="b" * 64,
            extracted_size_bytes=19,
        )
        updated = self.store.get_document("tenant-a", "kb-zero-source", "doc-zero")
        self.assertEqual(0, updated["chunk_overlap"])

        qdrant = MagicMock()
        qdrant.collection_exists.return_value = False
        original_qdrant = self.store.qdrant
        self.store.qdrant = qdrant
        try:
            self.assertTrue(
                self.store.move_document("tenant-a", "kb-zero-source", "kb-zero-target", "doc-zero")
            )
        finally:
            self.store.qdrant = original_qdrant
        moved = self.store.get_document("tenant-a", "kb-zero-target", "doc-zero")
        self.assertEqual(0, moved["chunk_overlap"])
        self.assertEqual("documents", moved["source_object_bucket"])
        self.assertEqual("source/object.txt", moved["source_object_key"])
        self.assertEqual("extracted/object.txt", moved["extracted_object_key"])
        self.assertEqual(20, moved["source_object_size_bytes"])
        self.assertEqual(19, moved["extracted_object_size_bytes"])

    def test_worker_redelivery_does_not_reopen_terminal_or_cancelled_task(self):
        payload = {"tenant_id": "tenant-a", "actor_id": "worker", "trace_id": "trace-a"}
        self.store.create_task_record("task-redelivered", "tenant-a", "kb-a", "doc-a", "DOC_INGEST", "PARSING")
        self.store.update_task_record("task-redelivered", "RUNNING", "PARSING")
        self.store.update_task_record("task-redelivered", "SUCCEEDED", "INDEXED")
        self.store.create_task_record("task-cancel-race", "tenant-a", "kb-a", "doc-b", "DOC_INGEST", "PARSING")
        self.store.request_cancel_task("task-cancel-race", tenant_id="tenant-a")

        with patch.object(worker_tasks, "kb_store", self.store), patch.object(
            worker_tasks, "task_authority", worker_tasks.TaskAuthorityBridge(self.store)
        ):
            terminal = worker_tasks._begin_persistent_task(
                task_id="task-redelivered", tenant_id="tenant-a", kb_id="kb-a", doc_id="doc-a",
                task_type="DOC_INGEST", stage="PARSING", payload=payload,
            )
            cancelled = worker_tasks._begin_persistent_task(
                task_id="task-cancel-race", tenant_id="tenant-a", kb_id="kb-a", doc_id="doc-b",
                task_type="DOC_INGEST", stage="PARSING", payload=payload,
            )

        self.assertEqual("already_terminal", terminal["status"])
        self.assertEqual("SUCCEEDED", terminal["state"])
        self.assertEqual("cancelled", cancelled["status"])
        self.assertEqual("SUCCEEDED", self.store.get_task("task-redelivered", tenant_id="tenant-a")["status"])
        self.assertEqual("CANCELLED", self.store.get_task("task-cancel-race", tenant_id="tenant-a")["status"])
