from __future__ import annotations

import sys
from pathlib import Path
import unittest


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from retrieval.rag2 import bm25_score, fuse_rankings, lexical_term_frequency, retrieval_metrics


class Rag2RankingTest(unittest.TestCase):
    def test_lexical_features_keep_chinese_terms_and_identifiers(self):
        terms = lexical_term_frequency("权限模型支持错误码 TS-999，权限校验失败。")

        self.assertIn("权限", terms)
        self.assertIn("权限模型", terms)
        self.assertIn("ts-999", terms)
        self.assertGreaterEqual(terms["权限"], 2)

    def test_bm25_prefers_exact_rare_term(self):
        query = ["ts-999"]
        common = bm25_score(query, {"错误码": 3}, 20, 20, {"ts-999": 1}, 100)
        exact = bm25_score(query, {"ts-999": 1}, 20, 20, {"ts-999": 1}, 100)

        self.assertEqual(0.0, common)
        self.assertGreater(exact, 0.0)

    def test_rrf_rewards_cross_channel_hits_and_applies_document_cap(self):
        dense = [
            {"doc_id": "d1", "chunk_id": "c1", "text": "dense one", "score": 0.9},
            {"doc_id": "d1", "chunk_id": "c2", "text": "dense two", "score": 0.8},
            {"doc_id": "d2", "chunk_id": "c3", "text": "dense three", "score": 0.7},
        ]
        lexical = [
            {"doc_id": "d2", "chunk_id": "c3", "text": "lexical exact", "score": 4.0},
            {"doc_id": "d1", "chunk_id": "c2", "text": "lexical second", "score": 3.0},
        ]

        result = fuse_rankings(dense, lexical, final_k=3, max_chunks_per_doc=1)

        self.assertEqual("c3", result[0]["chunk_id"])
        self.assertEqual({"d1", "d2"}, {item["doc_id"] for item in result})
        self.assertTrue(all(item["rank_method"] == "rrf" for item in result))

    def test_quality_metrics_are_deterministic(self):
        metrics = retrieval_metrics({"c2", "c4"}, ["c1", "c2", "c3", "c4"], k=4)

        self.assertEqual(1.0, metrics["recall_at_k"])
        self.assertEqual(0.5, metrics["mrr_at_k"])
        self.assertGreater(metrics["ndcg_at_k"], 0.0)

    def test_quality_metrics_count_duplicate_relevant_identifier_once(self):
        metrics = retrieval_metrics({"doc-a"}, ["doc-a", "doc-a", "doc-a"], k=3)

        self.assertEqual(1.0, metrics["recall_at_k"])
        self.assertEqual(1.0, metrics["mrr_at_k"])
        self.assertEqual(1.0, metrics["ndcg_at_k"])


if __name__ == "__main__":
    unittest.main()
