from __future__ import annotations

from collections import Counter
import math
import re
from typing import Iterable


_ASCII_TOKEN = re.compile(r"[a-z0-9]+(?:[._:/#-][a-z0-9]+)*", re.IGNORECASE)
_CJK_SPAN = re.compile(r"[\u3400-\u9fff]+")
SUPPORTED_RANK_METHODS = {"vector", "hybrid", "rrf", "keyword"}


def normalize_rank_method(value: str | None) -> str:
    method = str(value or "vector").strip().lower()
    return method if method in SUPPORTED_RANK_METHODS else "vector"


def lexical_term_frequency(text: str, max_terms: int = 512) -> dict[str, int]:
    """Build deterministic multilingual lexical features.

    ASCII identifiers are kept intact. Chinese text contributes overlapping
    bi/tri-grams so exact terminology remains searchable without a language-
    specific PostgreSQL tokenizer. The representation is intentionally model
    independent and can later coexist with learned sparse vectors.
    """
    value = str(text or "").lower()
    tokens: list[str] = []
    tokens.extend(match.group(0)[:96] for match in _ASCII_TOKEN.finditer(value))
    for match in _CJK_SPAN.finditer(value):
        span = match.group(0)
        if len(span) <= 16:
            tokens.append(span)
        if len(span) == 1:
            tokens.append(span)
            continue
        for width in (2, 3, 4):
            if len(span) < width:
                continue
            tokens.extend(span[index : index + width] for index in range(len(span) - width + 1))
    counter: Counter[str] = Counter(token for token in tokens if token)
    if len(counter) <= max_terms:
        return dict(counter)
    # Prefer repeated and longer discriminative terms while keeping output deterministic.
    selected = sorted(counter.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))[:max_terms]
    return dict(selected)


def bm25_score(
    query_terms: Iterable[str],
    document_tf: dict[str, int],
    document_length: int,
    average_document_length: float,
    document_frequency: dict[str, int],
    document_count: int,
    *,
    k1: float = 1.2,
    b: float = 0.75,
) -> float:
    if document_count <= 0 or not document_tf:
        return 0.0
    avg_len = max(1.0, float(average_document_length or 0.0))
    doc_len = max(1, int(document_length or 0))
    score = 0.0
    for term in dict.fromkeys(str(item) for item in query_terms if str(item)):
        tf = max(0, int(document_tf.get(term, 0)))
        if tf <= 0:
            continue
        df = max(0, min(int(document_frequency.get(term, 0)), document_count))
        idf = math.log(1.0 + (document_count - df + 0.5) / (df + 0.5))
        denominator = tf + k1 * (1.0 - b + b * doc_len / avg_len)
        score += idf * (tf * (k1 + 1.0)) / max(denominator, 1e-9)
    return float(score)


def fuse_rankings(
    dense_items: list[dict],
    lexical_items: list[dict],
    *,
    final_k: int,
    max_chunks_per_doc: int = 3,
    rrf_k: int = 60,
) -> list[dict]:
    """Fuse independent rankings by chunk identity using Reciprocal Rank Fusion."""
    merged: dict[tuple[str, str], dict] = {}
    for channel, items in (("dense", dense_items), ("lexical", lexical_items)):
        for rank, raw in enumerate(items, start=1):
            doc_id = str(raw.get("doc_id") or "")
            chunk_id = str(raw.get("chunk_id") or "")
            if not doc_id or not chunk_id:
                continue
            key = (doc_id, chunk_id)
            item = merged.setdefault(key, dict(raw))
            item.setdefault("scores", {})
            item["scores"][channel] = float(raw.get("score") or 0.0)
            item["scores"][f"{channel}_rank"] = rank
            item["scores"]["rrf"] = float(item["scores"].get("rrf") or 0.0) + 1.0 / (rrf_k + rank)
    ranked = sorted(
        merged.values(),
        key=lambda item: (
            -float((item.get("scores") or {}).get("rrf") or 0.0),
            int((item.get("scores") or {}).get("dense_rank") or 10**9),
            int((item.get("scores") or {}).get("lexical_rank") or 10**9),
            str(item.get("chunk_id") or ""),
        ),
    )
    selected: list[dict] = []
    per_doc: Counter[str] = Counter()
    cap = max(1, int(max_chunks_per_doc or 1))
    for item in ranked:
        doc_id = str(item.get("doc_id") or "")
        if per_doc[doc_id] >= cap:
            continue
        item["score"] = round(float((item.get("scores") or {}).get("rrf") or 0.0), 6)
        item["rank_method"] = "rrf"
        selected.append(item)
        per_doc[doc_id] += 1
        if len(selected) >= max(1, int(final_k or 1)):
            break
    return selected


def limit_per_document(items: list[dict], final_k: int, max_chunks_per_doc: int = 3) -> list[dict]:
    selected: list[dict] = []
    per_doc: Counter[str] = Counter()
    cap = max(1, int(max_chunks_per_doc or 1))
    for item in items:
        doc_id = str(item.get("doc_id") or "")
        if per_doc[doc_id] >= cap:
            continue
        selected.append(item)
        per_doc[doc_id] += 1
        if len(selected) >= max(1, int(final_k or 1)):
            break
    return selected


def retrieval_metrics(expected_chunk_ids: set[str], ranked_chunk_ids: list[str], k: int = 10) -> dict[str, float]:
    """Small dependency-free evaluation primitive used by the RAG quality gate."""
    expected = {str(item) for item in expected_chunk_ids if str(item)}
    ranked = [str(item) for item in ranked_chunk_ids[: max(1, int(k))] if str(item)]
    if not expected:
        return {"recall_at_k": 1.0 if not ranked else 0.0, "mrr_at_k": 0.0, "ndcg_at_k": 0.0}
    # A document-level judgement may surface several chunks from the same
    # relevant document. Binary relevance awards that document once; counting
    # every duplicate as another hit can make DCG exceed the ideal DCG and
    # produce an impossible NDCG value above 1.
    seen_relevant: set[str] = set()
    hits: list[int] = []
    for item in ranked:
        relevant = item in expected and item not in seen_relevant
        hits.append(1 if relevant else 0)
        if relevant:
            seen_relevant.add(item)
    recall = len({item for item in ranked if item in expected}) / len(expected)
    reciprocal_rank = next((1.0 / (index + 1) for index, hit in enumerate(hits) if hit), 0.0)
    dcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(hits))
    ideal_hits = min(len(expected), len(ranked))
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return {
        "recall_at_k": round(recall, 6),
        "mrr_at_k": round(reciprocal_rank, 6),
        "ndcg_at_k": round(min(dcg / idcg, 1.0) if idcg else 0.0, 6),
    }
