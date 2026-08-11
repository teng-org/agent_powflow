from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Iterable

from retrieval.rag2 import retrieval_metrics
from retrieval.schemas import SearchRequest
from retrieval.service import search_with_meta


def evaluate_cases(
    cases: Iterable[dict],
    *,
    rank_method: str = "hybrid",
    top_k: int = 10,
    search_fn: Callable[[SearchRequest], dict] = search_with_meta,
    index_revision: str | None = None,
) -> dict:
    rows: list[dict] = []
    for index, case in enumerate(cases, start=1):
        expected_chunks = {str(item) for item in case.get("expected_chunk_ids") or [] if str(item)}
        expected_docs = {str(item) for item in case.get("expected_doc_ids") or [] if str(item)}
        if not expected_chunks and not expected_docs:
            raise ValueError(f"evaluation case {index} needs expected_chunk_ids or expected_doc_ids")
        request = SearchRequest(
            tenant_id=str(case.get("tenant_id") or ""),
            kb_id=str(case.get("kb_id") or ""),
            query=str(case.get("query") or ""),
            rank_method=str(case.get("rank_method") or rank_method),
            top_k=top_k,
            final_k=top_k,
            candidate_k=min(max(top_k * 5, 20), 200),
            context_window=0,
            index_revision=index_revision,
        )
        result = search_fn(request)
        evidence = list(result.get("evidence") or [])
        if expected_chunks:
            expected = expected_chunks
            ranked = [str(item.get("chunk_id") or "") for item in evidence]
            target_kind = "chunk"
        else:
            expected = expected_docs
            ranked = [str(item.get("doc_id") or "") for item in evidence]
            target_kind = "document"
        metrics = retrieval_metrics(expected, ranked, k=top_k)
        rows.append({
            "case_id": str(case.get("case_id") or index),
            "query": request.query,
            "target_kind": target_kind,
            "metrics": metrics,
            "effective_rank_method": str((result.get("meta") or {}).get("effective_rank_method") or ""),
            "returned_count": len(evidence),
        })
    count = len(rows)
    aggregate = {
        key: round(sum(float(row["metrics"][key]) for row in rows) / count, 6) if count else 0.0
        for key in ("recall_at_k", "mrr_at_k", "ndcg_at_k")
    }
    return {"case_count": count, "rank_method": rank_method, "top_k": top_k, "aggregate": aggregate, "cases": rows}


def _read_jsonl(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"line {line_no} must be a JSON object")
        cases.append(value)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an offline RAG retrieval quality gate")
    parser.add_argument("dataset", type=Path, help="UTF-8 JSONL evaluation cases")
    parser.add_argument("--rank-method", default="hybrid", choices=("vector", "keyword", "hybrid", "rrf"))
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--min-recall", type=float, default=0.0)
    parser.add_argument("--min-mrr", type=float, default=0.0)
    parser.add_argument("--min-ndcg", type=float, default=0.0)
    args = parser.parse_args()
    result = evaluate_cases(_read_jsonl(args.dataset), rank_method=args.rank_method, top_k=args.top_k)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    aggregate = result["aggregate"]
    passed = (
        aggregate["recall_at_k"] >= args.min_recall
        and aggregate["mrr_at_k"] >= args.min_mrr
        and aggregate["ndcg_at_k"] >= args.min_ndcg
    )
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
