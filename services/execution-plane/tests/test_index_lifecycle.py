from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from postgres_test_support import IsolatedPostgresCase
from retrieval.index_lifecycle import IndexLifecycleError, IndexQualityGateFailed, RagIndexLifecycle


class _MemoryQdrant:
    def __init__(self) -> None:
        self.collections: dict[str, dict] = {}

    def collection_exists(self, *, collection_name: str) -> bool:
        return collection_name in self.collections

    def create_collection(self, *, collection_name: str, vectors_config) -> None:
        self.collections[collection_name] = {"size": int(vectors_config.size), "points": []}

    def delete_collection(self, *, collection_name: str) -> None:
        self.collections.pop(collection_name, None)

    def upsert(self, *, collection_name: str, points: list) -> None:
        target = self.collections[collection_name]["points"]
        by_id = {str(point.id): point for point in target}
        by_id.update({str(point.id): point for point in points})
        self.collections[collection_name]["points"] = list(by_id.values())

    def delete(self, *, collection_name: str, points_selector) -> None:
        conditions = list(points_selector.filter.must or [])
        expected = {str(condition.key): getattr(condition.match, "value", None) for condition in conditions}
        self.collections[collection_name]["points"] = [
            point for point in self.collections[collection_name]["points"]
            if not all(point.payload.get(key) == value for key, value in expected.items())
        ]

    def search(self, *, collection_name: str, query_vector: list[float], query_filter, limit: int, with_payload: bool):
        points = list(self.collections.get(collection_name, {}).get("points", []))
        scored = []
        for point in points:
            numerator = sum(float(a) * float(b) for a, b in zip(point.vector, query_vector))
            left = math.sqrt(sum(float(a) ** 2 for a in point.vector))
            right = math.sqrt(sum(float(a) ** 2 for a in query_vector))
            scored.append(SimpleNamespace(score=numerator / max(left * right, 1e-12), payload=point.payload))
        return sorted(scored, key=lambda hit: -hit.score)[:limit]


class RagIndexLifecycleIntegrationTest(IsolatedPostgresCase):
    def setUp(self) -> None:
        self.qdrant = _MemoryQdrant()
        self.store.qdrant = self.qdrant
        self.lifecycle = RagIndexLifecycle(self.store)
        self.tenant_id = "tenant-shadow"
        self.kb_id = "kb-shadow"
        self.doc_id = "doc-shadow"
        self.store.create_kb(self.tenant_id, self.kb_id, "Shadow KB")
        self.store.save_document(
            self.tenant_id, self.kb_id, self.doc_id, "manual.md", "alpha policy\nbeta rollback"
        )
        self.store.replace_chunks_preview(
            self.tenant_id, self.kb_id, self.doc_id, ["alpha policy", "beta rollback"], "manual.md"
        )
        self.store.replace_chunk_structure(
            self.tenant_id,
            self.kb_id,
            self.doc_id,
            [
                {
                    "parent_id": "parent-shadow",
                    "parent_index": 0,
                    "parent_text": "# Operations\n\nalpha policy\n\nbeta rollback",
                    "section_path": ["Operations"],
                    "block_type": "paragraph",
                    "page_start": 1,
                    "page_end": 1,
                },
                {
                    "parent_id": "parent-shadow",
                    "parent_index": 0,
                    "parent_text": "# Operations\n\nalpha policy\n\nbeta rollback",
                    "section_path": ["Operations"],
                    "block_type": "paragraph",
                    "page_start": 1,
                    "page_end": 1,
                },
            ],
            "manual.md",
        )
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE chunks SET embedding_json=CASE WHEN chunk_index=0
                    THEN '[1,0]'::jsonb ELSE '[0,1]'::jsonb END
                WHERE tenant_id=%s AND kb_id=%s AND doc_id=%s""",
                (self.tenant_id, self.kb_id, self.doc_id),
            )
        self.store.set_document_status(self.tenant_id, self.kb_id, self.doc_id, "INDEXED")
        self.store.refresh_rag_index_manifest(self.tenant_id, self.kb_id)

    def test_build_activate_and_rollback_keep_revisions_isolated(self):
        baseline = self.lifecycle.bootstrap_active_snapshot(self.tenant_id, self.kb_id, "tester")
        repeated = self.lifecycle.bootstrap_active_snapshot(self.tenant_id, self.kb_id, "tester")
        self.assertEqual(baseline["collection_name"], repeated["collection_name"])
        baseline_revision = baseline["index_revision"]
        baseline_collection = baseline["collection_name"]
        self.assertEqual("PASSED", baseline["gate_status"])
        self.assertEqual(2, len(self.qdrant.collections[baseline_collection]["points"]))

        shadow = self.lifecycle.build_shadow(self.tenant_id, self.kb_id, "rag2-shadow-a")
        with self.assertRaises(IndexLifecycleError):
            self.lifecycle.build_shadow(self.tenant_id, self.kb_id, "rag2-shadow-a")
        self.assertEqual(baseline_revision, self.store.get_active_index_revision(self.tenant_id, self.kb_id))
        self.assertEqual("PASSED", shadow["gate_status"])
        lexical = self.store.search_lexical_chunks(
            self.tenant_id, self.kb_id, "rollback", 5, index_revision="rag2-shadow-a"
        )
        self.assertEqual("rag2-shadow-a", lexical[0]["index_revision"])
        expanded = self.store.expand_chunk_context(
            self.tenant_id, self.kb_id, lexical, window=1, index_revision="rag2-shadow-a"
        )
        self.assertEqual("parent", expanded[0]["context_strategy"])

        activated = self.lifecycle.activate(self.tenant_id, self.kb_id, "rag2-shadow-a", "tester")
        self.assertEqual(baseline_revision, activated["previous_revision"])
        self.assertEqual("rag2-shadow-a", self.store.get_active_index_revision(self.tenant_id, self.kb_id))
        self.lifecycle.rollback(self.tenant_id, self.kb_id, baseline_revision, "tester")
        self.assertEqual(baseline_revision, self.store.get_active_index_revision(self.tenant_id, self.kb_id))
        with self.assertRaises(IndexLifecycleError):
            self.lifecycle.evaluate(
                self.tenant_id,
                self.kb_id,
                baseline_revision,
                [{"query": "alpha", "rank_method": "keyword", "expected_doc_ids": [self.doc_id]}],
            )

    def test_activation_bootstraps_legacy_active_revision_before_cutover(self):
        baseline_revision = self.store.get_active_index_revision(self.tenant_id, self.kb_id)
        self.assertFalse(self.store.has_index_snapshot(self.tenant_id, self.kb_id, baseline_revision))
        self.lifecycle.build_shadow(self.tenant_id, self.kb_id, "rag2-shadow-auto-bootstrap")

        self.lifecycle.activate(self.tenant_id, self.kb_id, "rag2-shadow-auto-bootstrap", "tester")

        self.assertTrue(self.store.has_index_snapshot(self.tenant_id, self.kb_id, baseline_revision))
        self.lifecycle.rollback(self.tenant_id, self.kb_id, baseline_revision, "tester")
        self.assertEqual(baseline_revision, self.store.get_active_index_revision(self.tenant_id, self.kb_id))

    def test_rollback_recovers_unchanged_immediate_legacy_predecessor(self):
        baseline = self.lifecycle.bootstrap_active_snapshot(self.tenant_id, self.kb_id, "tester")
        baseline_revision = baseline["index_revision"]
        self.lifecycle.build_shadow(self.tenant_id, self.kb_id, "rag2-shadow-recovery")
        self.lifecycle.activate(self.tenant_id, self.kb_id, "rag2-shadow-recovery", "tester")
        self.qdrant.delete_collection(collection_name=baseline["collection_name"])
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                (self.tenant_id, self.kb_id, baseline_revision),
            )
            cur.execute(
                "DELETE FROM rag_index_parents WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                (self.tenant_id, self.kb_id, baseline_revision),
            )
            cur.execute(
                "UPDATE rag_index_manifests SET collection_name='' WHERE tenant_id=%s AND kb_id=%s AND index_revision=%s",
                (self.tenant_id, self.kb_id, baseline_revision),
            )

        self.lifecycle.rollback(self.tenant_id, self.kb_id, baseline_revision, "tester")

        self.assertEqual(baseline_revision, self.store.get_active_index_revision(self.tenant_id, self.kb_id))
        self.assertTrue(self.store.has_index_snapshot(self.tenant_id, self.kb_id, baseline_revision))

    def test_document_delete_removes_all_revision_snapshots_and_vectors(self):
        baseline = self.lifecycle.bootstrap_active_snapshot(self.tenant_id, self.kb_id, "tester")
        shadow = self.lifecycle.build_shadow(self.tenant_id, self.kb_id, "rag2-shadow-delete")
        self.assertTrue(self.store.delete_document(self.tenant_id, self.kb_id, self.doc_id))
        self.assertEqual([], self.qdrant.collections[baseline["collection_name"]]["points"])
        self.assertEqual([], self.qdrant.collections[shadow["collection_name"]]["points"])
        with self.store._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*)::int AS count FROM rag_index_chunks WHERE tenant_id=%s AND kb_id=%s",
                (self.tenant_id, self.kb_id),
            )
            self.assertEqual(0, int(cur.fetchone()["count"]))

    def test_failed_build_never_switches_active_and_can_be_cleaned(self):
        active = self.store.get_active_index_revision(self.tenant_id, self.kb_id)
        self.store.save_document(self.tenant_id, self.kb_id, "doc-bad", "bad.md", "missing vector")
        self.store.replace_chunks_preview(
            self.tenant_id, self.kb_id, "doc-bad", ["missing vector"], "bad.md"
        )
        self.store.set_document_status(self.tenant_id, self.kb_id, "doc-bad", "INDEXED")
        with self.assertRaises(IndexQualityGateFailed):
            self.lifecycle.build_shadow(self.tenant_id, self.kb_id, "rag2-shadow-failed")
        self.assertEqual(active, self.store.get_active_index_revision(self.tenant_id, self.kb_id))
        failed = self.store.get_rag_index_manifest(self.tenant_id, self.kb_id, "rag2-shadow-failed")
        self.assertEqual("FAILED", failed["status"])
        cleaned = self.lifecycle.cleanup_failed(self.tenant_id, self.kb_id, "rag2-shadow-failed")
        self.assertTrue(cleaned["deleted"])
        self.assertTrue(self.store.delete_document(self.tenant_id, self.kb_id, "doc-bad"))

    def test_required_retrieval_evaluation_blocks_activation_until_it_passes(self):
        self.lifecycle.build_shadow(
            self.tenant_id,
            self.kb_id,
            "rag2-shadow-gated",
            config={"require_evaluation": True},
        )
        with self.assertRaises(IndexQualityGateFailed):
            self.lifecycle.activate(self.tenant_id, self.kb_id, "rag2-shadow-gated", "tester")
        result = self.lifecycle.evaluate(
            self.tenant_id,
            self.kb_id,
            "rag2-shadow-gated",
            [{
                "case_id": "rollback-policy",
                "query": "rollback",
                "rank_method": "keyword",
                "expected_doc_ids": [self.doc_id],
            }],
            top_k=1,
            min_recall=1.0,
            min_mrr=1.0,
            min_ndcg=1.0,
        )
        self.assertTrue(result["passed"])
        activated = self.lifecycle.activate(
            self.tenant_id, self.kb_id, "rag2-shadow-gated", "tester"
        )
        self.assertTrue(activated["changed"])

    def test_active_snapshot_receives_incremental_document_publication(self):
        baseline = self.lifecycle.bootstrap_active_snapshot(self.tenant_id, self.kb_id, "tester")
        result = self.lifecycle.sync_document_to_active(self.tenant_id, self.kb_id, self.doc_id)
        self.assertTrue(result["synced"])
        self.assertEqual(2, result["chunk_count"])
        self.assertEqual(2, len(self.qdrant.collections[baseline["collection_name"]]["points"]))
