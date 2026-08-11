from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from retrieval.schemas import SearchRequest
from retrieval.service import search_with_meta


class _Embedding:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, *args, **kwargs):
        self.calls += 1
        return [0.1, 0.2]

    def local_embed(self, *args, **kwargs):
        return [0.1, 0.2]


class _Store:
    def __init__(self, lexical: list[dict]) -> None:
        self.lexical = lexical

    def get_kb_embedding_dimension(self, *args):
        return 2

    def search_chunks(self, **kwargs):
        return [
            {"doc_id": "dense-doc", "chunk_id": "dense", "text": "semantic", "score": 0.9},
            {"doc_id": "both-doc", "chunk_id": "both", "text": "both", "score": 0.8},
        ]

    def search_lexical_chunks(self, **kwargs):
        return list(self.lexical)

    def get_rag_index_manifest(self, *args):
        return {"index_revision": "rag2-v1", "chunk_count": 10, "lexical_chunk_count": 8}

    def expand_chunk_context(self, tenant_id, kb_id, items, window=1, index_revision=None):
        return [{**item, "matched_text": item["text"], "text": f"before\n\n{item['text']}\n\nafter", "context_window": window}
                for item in items]


class _Reranker:
    def rerank(self, **kwargs):
        items = list(reversed(kwargs["items"]))[:kwargs["top_n"]]
        for rank, item in enumerate(items, start=1):
            item = dict(item)
            item["score"] = 1.0 - rank / 10
            item["scores"] = {**dict(item.get("scores") or {}), "rerank": item["score"]}
            items[rank - 1] = item
        return {"items": items, "meta": {"status": "succeeded", "model": "test-reranker"}}


class Rag2ServiceTest(unittest.TestCase):
    def test_hybrid_really_fuses_two_independent_rankings(self):
        embedding = _Embedding()
        store = _Store([
            {"doc_id": "lex-doc", "chunk_id": "lex", "text": "TS-999", "score": 4.2},
            {"doc_id": "both-doc", "chunk_id": "both", "text": "both", "score": 2.1},
        ])
        with patch("retrieval.service.kb_store", store), patch("retrieval.service.embedding_provider", embedding):
            result = search_with_meta(SearchRequest(
                tenant_id="tenant", kb_id="kb", query="TS-999", rank_method="hybrid", top_k=3,
            ))
        self.assertEqual("hybrid", result["meta"]["requested_rank_method"])
        self.assertEqual("hybrid", result["meta"]["effective_rank_method"])
        self.assertEqual(2, result["meta"]["dense_candidates"])
        self.assertEqual(2, result["meta"]["lexical_candidates"])
        self.assertEqual("both", result["evidence"][0]["chunk_id"])
        self.assertEqual(1, embedding.calls)
        self.assertEqual(0.8, result["meta"]["lexical_coverage"])
        self.assertEqual(1, result["meta"]["context_window"])
        self.assertIn("matched_text", result["evidence"][0])

    def test_hybrid_reports_vector_fallback_when_lexical_index_is_empty(self):
        with patch("retrieval.service.kb_store", _Store([])), patch("retrieval.service.embedding_provider", _Embedding()):
            result = search_with_meta(SearchRequest(
                tenant_id="tenant", kb_id="kb", query="semantic", rank_method="hybrid", top_k=2,
            ))
        self.assertEqual("vector_fallback", result["meta"]["effective_rank_method"])
        self.assertEqual(["dense", "both"], [item["chunk_id"] for item in result["evidence"]])

    def test_keyword_does_not_call_embedding_provider(self):
        embedding = _Embedding()
        store = _Store([{"doc_id": "lex-doc", "chunk_id": "lex", "text": "TS-999", "score": 4.2}])
        with patch("retrieval.service.kb_store", store), patch("retrieval.service.embedding_provider", embedding):
            result = search_with_meta(SearchRequest(
                tenant_id="tenant", kb_id="kb", query="TS-999", rank_method="keyword", top_k=1,
            ))
        self.assertEqual(0, embedding.calls)
        self.assertEqual("lex", result["evidence"][0]["chunk_id"])
        self.assertEqual("keyword", result["meta"]["effective_rank_method"])

    def test_reranker_runs_between_candidate_recall_and_final_cut(self):
        with (
            patch("retrieval.service.kb_store", _Store([])),
            patch("retrieval.service.embedding_provider", _Embedding()),
            patch("retrieval.service.reranker_provider", _Reranker()),
        ):
            result = search_with_meta(SearchRequest(
                tenant_id="tenant", kb_id="kb", query="semantic", rank_method="vector",
                top_k=1, final_k=1, candidate_k=2, rerank_enabled=True, rerank_k=2, context_window=0,
            ))
        self.assertEqual("both", result["evidence"][0]["chunk_id"])
        self.assertEqual("succeeded", result["meta"]["rerank"]["status"])
        self.assertEqual(2, result["meta"]["rerank_k"])

    def test_optional_reranker_failure_is_visible_and_preserves_retrieval_order(self):
        class FailingReranker:
            def rerank(self, **kwargs):
                raise RuntimeError("provider offline")

        with (
            patch("retrieval.service.kb_store", _Store([])),
            patch("retrieval.service.embedding_provider", _Embedding()),
            patch("retrieval.service.reranker_provider", FailingReranker()),
        ):
            result = search_with_meta(SearchRequest(
                tenant_id="tenant", kb_id="kb", query="semantic", rank_method="vector",
                top_k=1, final_k=1, candidate_k=2, rerank_enabled=True, rerank_k=2, context_window=0,
            ))
        self.assertEqual("dense", result["evidence"][0]["chunk_id"])
        self.assertEqual("fallback", result["meta"]["rerank"]["status"])
        self.assertIn("provider offline", result["meta"]["rerank"]["reason"])


if __name__ == "__main__":
    unittest.main()
