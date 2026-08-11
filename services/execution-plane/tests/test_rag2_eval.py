from __future__ import annotations

import sys
from pathlib import Path
import unittest


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from retrieval.rag2_eval import evaluate_cases


class Rag2EvaluationTest(unittest.TestCase):
    def test_evaluation_supports_document_and_chunk_judgements(self):
        def search(req):
            return {
                "evidence": [
                    {"doc_id": "doc-a", "chunk_id": "chunk-miss"},
                    {"doc_id": "doc-b", "chunk_id": "chunk-hit"},
                ],
                "meta": {"effective_rank_method": req.rank_method},
            }

        result = evaluate_cases(
            [
                {"case_id": "doc", "tenant_id": "t", "kb_id": "k", "query": "q1", "expected_doc_ids": ["doc-a"]},
                {"case_id": "chunk", "tenant_id": "t", "kb_id": "k", "query": "q2", "expected_chunk_ids": ["chunk-hit"]},
            ],
            rank_method="hybrid",
            top_k=2,
            search_fn=search,
        )
        self.assertEqual(2, result["case_count"])
        self.assertEqual(1.0, result["aggregate"]["recall_at_k"])
        self.assertEqual(0.75, result["aggregate"]["mrr_at_k"])
        self.assertGreater(result["aggregate"]["ndcg_at_k"], 0.8)


if __name__ == "__main__":
    unittest.main()
