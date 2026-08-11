import hashlib
import re
import time
from typing import Callable

from models.ai_gateway import ai_gateway
from models.embedding_provider import embedding_provider
from retrieval.schemas import IngestDocumentRequest, SearchRequest
from retrieval.rag2 import fuse_rankings, limit_per_document, normalize_rank_method
from retrieval.store import ChunkRecord, kb_store
from retrieval.index_lifecycle import index_lifecycle
from retrieval.structured_chunking import build_parent_child_chunks, parse_content_blocks
from models.reranker_provider import reranker_provider
from runtime.task_authority import TaskAuthorityBridge


task_authority = TaskAuthorityBridge(kb_store)


def _strip_nul(text: str) -> str:
    return re.sub(r"[\x00\ud800-\udfff]", "", text or "")

def _emit_stage(req: IngestDocumentRequest, stage: str, status: str = "RUNNING") -> None:
    if not req.task_id:
        return
    task_status = "SUCCEEDED" if status == "SUCCEEDED" else "RUNNING"
    task_authority.update_task_record(req.task_id, task_status, stage)
    task_authority.append_task_event(
        req.task_id,
        req.tenant_id,
        req.kb_id,
        req.doc_id,
        "STAGE_CHANGED",
        stage,
        {"status": status},
    )


def _emit_progress(req: IngestDocumentRequest, stage: str, current: int, total: int) -> None:
    if not req.task_id:
        return
    c = max(0, int(current or 0))
    t = max(0, int(total or 0))
    pct = int((c * 100 / t)) if t > 0 else 0
    task_authority.update_task_progress(req.task_id, stage, c, t, pct)
    task_authority.append_task_event(
        req.task_id,
        req.tenant_id,
        req.kb_id,
        req.doc_id,
        "STAGE_PROGRESS",
        stage,
        {"status": "RUNNING", "current": c, "total": t, "progress_pct": pct},
    )


def _is_task_cancel_requested(req: IngestDocumentRequest) -> bool:
    task_id = str(req.task_id or "").strip()
    if not task_id:
        return False
    try:
        return bool(kb_store.is_cancel_requested(task_id))
    except Exception:
        return False


def preprocess_text_for_chunk(text: str) -> str:
    """Normalize OCR-like spacing noise before chunking.

    Keep semantic separators for non-CJK content, while removing noisy spaces
    between consecutive Chinese characters.
    """
    value = _strip_nul(text or "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\u3000", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"[ \t]*\n[ \t]*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    # Remove OCR-inserted spaces between adjacent CJK characters.
    value = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", value)
    return value.strip()


def _split_sentences(text: str) -> list[str]:
    items = re.findall(r".+?(?:[。！？!?；;\n]|$)", text, flags=re.DOTALL)
    return [it.strip() for it in items if it and it.strip()]


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n{2,}", text)
    return [p.strip() for p in parts if p and p.strip()]


def _split_markdown_sections(text: str) -> list[str]:
    lines = text.split("\n")
    sections: list[str] = []
    buf: list[str] = []
    heading_pat = re.compile(r"^\s{0,3}#{1,6}\s+\S+")
    for line in lines:
        if heading_pat.match(line) and buf:
            sections.append("\n".join(buf).strip())
            buf = [line]
            continue
        buf.append(line)
    if buf:
        sections.append("\n".join(buf).strip())
    return [s for s in sections if s]


def _chunks_from_units(units: list[str], chunk_size: int, chunk_overlap: int, sep: str) -> list[str]:
    chunks: list[str] = []
    if not units:
        return chunks

    def _split_oversized(text: str) -> list[str]:
        value = (text or "").strip()
        if not value:
            return []
        if len(value) <= chunk_size:
            return [value]
        step = max(1, chunk_size - max(0, chunk_overlap))
        parts: list[str] = []
        start = 0
        while start < len(value):
            piece = value[start : start + chunk_size].strip()
            if piece:
                parts.append(piece)
            start += step
        return parts

    current = ""
    for unit in units:
        expanded = _split_oversized(unit)
        if not expanded:
            continue
        for piece in expanded:
            if not current:
                current = piece
                continue
            candidate = f"{current}{sep}{piece}"
            if len(candidate) <= chunk_size:
                current = candidate
                continue
            chunks.append(current.strip())
            if chunk_overlap > 0:
                tail = current[-chunk_overlap:]
                current = f"{tail}{sep}{piece}".strip()
            else:
                current = piece
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _normalize_delimiter(delimiter: str | None) -> str:
    raw = (delimiter or "").strip()
    if not raw:
        return "\n\n"
    return raw.replace("\\n", "\n").replace("\\t", "\t")


def _chunk_text(
    text: str,
    chunk_size: int,
    chunk_overlap: int,
    chunk_method: str = "fixed",
    chunk_delimiter: str | None = None,
) -> list[str]:
    chunks: list[str] = []
    value = (text or "").strip()
    if not value.strip():
        return chunks

    method = (chunk_method or "fixed").lower().strip()
    if method == "sentence":
        return _chunks_from_units(_split_sentences(value), chunk_size, chunk_overlap, "")
    if method == "paragraph":
        paragraphs = _split_paragraphs(value)
        normalized: list[str] = []
        for p in paragraphs:
            if len(p) <= chunk_size:
                normalized.append(p)
            else:
                normalized.extend(_split_sentences(p))
        return _chunks_from_units(normalized, chunk_size, chunk_overlap, "\n\n")
    if method == "markdown":
        sections = _split_markdown_sections(value)
        normalized: list[str] = []
        for sec in sections:
            if len(sec) <= chunk_size:
                normalized.append(sec)
            else:
                normalized.extend(_split_paragraphs(sec))
        return _chunks_from_units(normalized, chunk_size, chunk_overlap, "\n\n")
    if method == "delimiter":
        delim = _normalize_delimiter(chunk_delimiter)
        parts = [p.strip() for p in value.split(delim)]
        return [p for p in parts if p]

    # default: fixed-length with overlap
    start = 0
    step = max(1, chunk_size - chunk_overlap)
    while start < len(value):
        end = min(len(value), start + chunk_size)
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += step
    return chunks


def _split_for_embedding_limit(text: str, max_chars: int, overlap: int = 40) -> list[str]:
    value = (text or "").strip()
    if not value:
        return []
    if max_chars <= 0 or len(value) <= max_chars:
        return [value]
    step = max(1, max_chars - max(0, overlap))
    out: list[str] = []
    start = 0
    while start < len(value):
        piece = value[start : start + max_chars].strip()
        if piece:
            out.append(piece)
        start += step
    return out


def run_document_and_kb_summary(
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    filename: str,
    content: str,
    progress_cb: Callable[[str, int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    doc_summary = ai_gateway.summarize_document(
        tenant_id=tenant_id,
        filename=filename,
        text=content,
        progress_cb=progress_cb,
        should_cancel=should_cancel,
        kb_id=kb_id,
        doc_id=doc_id,
    )
    kb_store.set_document_summary(
        tenant_id=tenant_id,
        kb_id=kb_id,
        doc_id=doc_id,
        overview=doc_summary.get("overview", ""),
        summary=doc_summary.get("summary", ""),
        embedding=doc_summary.get("embedding", []),
    )

    kb_summary_text = ""
    auto_kb_summary_triggered = False
    if kb_store.should_auto_first_kb_summary(tenant_id, kb_id):
        kb_item = kb_store.get_kb(tenant_id, kb_id) or {}
        kb_name = str(kb_item.get("name") or kb_id)
        summaries = kb_store.list_document_summaries(tenant_id, kb_id, limit=300)
        kb_summary = ai_gateway.summarize_kb(
            tenant_id=tenant_id,
            kb_id=kb_id,
            kb_name=kb_name,
            doc_summaries=summaries,
            progress_cb=progress_cb,
            should_cancel=should_cancel,
        )
        kb_summary_text = str(kb_summary.get("summary", "") or "")
        kb_store.set_kb_summary(
            tenant_id=tenant_id,
            kb_id=kb_id,
            summary=kb_summary_text,
            embedding=kb_summary.get("embedding", []),
        )
        auto_kb_summary_triggered = True
    return {
        "doc_overview": doc_summary.get("overview", ""),
        "doc_summary": doc_summary.get("summary", ""),
        "kb_summary": kb_summary_text,
        "auto_kb_summary_triggered": auto_kb_summary_triggered,
    }


def run_kb_summary_only(
    tenant_id: str,
    kb_id: str,
    progress_cb: Callable[[str, int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    kb_item = kb_store.get_kb(tenant_id, kb_id) or {}
    kb_name = str(kb_item.get("name") or kb_id)
    summaries = kb_store.list_document_summaries(tenant_id, kb_id, limit=300)
    kb_summary = ai_gateway.summarize_kb(
        tenant_id=tenant_id,
        kb_id=kb_id,
        kb_name=kb_name,
        doc_summaries=summaries,
        progress_cb=progress_cb,
        should_cancel=should_cancel,
    )
    kb_store.set_kb_summary(
        tenant_id=tenant_id,
        kb_id=kb_id,
        summary=kb_summary.get("summary", ""),
        embedding=kb_summary.get("embedding", []),
    )
    return {
        "kb_id": kb_id,
        "kb_name": kb_name,
        "kb_summary": kb_summary.get("summary", ""),
    }


def ingest_document(req: IngestDocumentRequest) -> dict:
    safe_filename = _strip_nul(req.filename)
    safe_content = _strip_nul(req.content)
    cleaned_content = preprocess_text_for_chunk(safe_content)
    kb_store.save_document(
        req.tenant_id,
        req.kb_id,
        req.doc_id,
        safe_filename,
        cleaned_content,
        chunk_method=req.chunk_method,
        chunk_size=req.chunk_size,
        chunk_overlap=req.chunk_overlap,
        chunk_delimiter=req.chunk_delimiter,
    )
    kb_store.set_document_status(req.tenant_id, req.kb_id, req.doc_id, "PARSING")
    _emit_stage(req, "PARSING")

    preprocessed = cleaned_content.strip()
    max_chars = embedding_provider.max_input_chars(req.tenant_id)
    structured_chunks = []
    if (req.chunk_method or "fixed").strip().lower() not in {"sentence", "delimiter"}:
        structured_chunks = build_parent_child_chunks(
            parse_content_blocks(preprocessed),
            identity=f"{req.tenant_id}:{req.kb_id}:{req.doc_id}",
            child_size=req.chunk_size,
            child_overlap=req.chunk_overlap,
            max_child_chars=max_chars or None,
        )
    chunk_texts = [item.text for item in structured_chunks]
    if not chunk_texts:
        chunk_texts = _chunk_text(
            preprocessed,
            req.chunk_size,
            req.chunk_overlap,
            req.chunk_method,
            req.chunk_delimiter,
        )
        if max_chars:
            safe_chunks: list[str] = []
            for text in chunk_texts:
                safe_chunks.extend(_split_for_embedding_limit(text, max_chars=max_chars, overlap=min(40, req.chunk_overlap)))
            chunk_texts = safe_chunks
    kb_store.set_document_chunking_preview(req.tenant_id, req.kb_id, req.doc_id, len(chunk_texts))
    existing_chunks = kb_store.list_document_chunks_for_embedding(req.tenant_id, req.kb_id, req.doc_id)
    existing_texts = [_strip_nul(str(item.get("text") or "")) for item in existing_chunks]
    next_texts = [_strip_nul(text) for text in chunk_texts]
    if existing_texts != next_texts:
        kb_store.replace_chunks_preview(
            req.tenant_id,
            req.kb_id,
            req.doc_id,
            next_texts,
            safe_filename,
        )
    if structured_chunks:
        kb_store.replace_chunk_structure(
            req.tenant_id,
            req.kb_id,
            req.doc_id,
            [
                {
                    **item.metadata(),
                    "parent_text": item.parent_text,
                }
                for item in structured_chunks
            ],
            safe_filename,
        )
    existing_chunks = kb_store.list_document_chunks_for_embedding(req.tenant_id, req.kb_id, req.doc_id)
    _emit_stage(req, "CHUNKING")

    kb_store.set_document_status(req.tenant_id, req.kb_id, req.doc_id, "EMBEDDING")
    _emit_stage(req, "EMBEDDING")
    total_chunks = len(next_texts)
    existing_by_index = {int(item.get("chunk_index") or 0): item for item in existing_chunks}
    embedded_done = sum(1 for item in existing_chunks if item.get("embedding_json"))
    _emit_progress(req, "EMBEDDING", embedded_done, total_chunks)
    emit_step = max(1, total_chunks // 50) if total_chunks > 0 else 1
    for idx, chunk_text in enumerate(next_texts):
        if _is_task_cancel_requested(req):
            raise RuntimeError("TASK_CANCELLED")
        existing = existing_by_index.get(idx) or {}
        existing_embedding = existing.get("embedding_json") or []
        raw = f"{req.tenant_id}:{req.kb_id}:{req.doc_id}:{idx}:{chunk_text}"
        chunk_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()  # nosec B324
        if existing_embedding:
            vector = existing_embedding
        else:
            vector = embedding_provider.embed(
                req.tenant_id,
                chunk_text,
                task="doc_embedding",
                kb_id=req.kb_id,
                doc_id=req.doc_id,
            )
        structure = structured_chunks[idx].metadata() if idx < len(structured_chunks) else {}
        kb_store.persist_chunk_embedding(
            ChunkRecord(
                tenant_id=req.tenant_id,
                kb_id=req.kb_id,
                doc_id=req.doc_id,
                chunk_id=chunk_id,
                chunk_index=idx,
                text=chunk_text,
                embedding=vector,
                metadata={"filename": safe_filename, **structure},
            )
        )
        done = idx + 1
        if done == total_chunks or done % emit_step == 0:
            _emit_progress(req, "EMBEDDING", done, total_chunks)

    kb_store.set_document_status(req.tenant_id, req.kb_id, req.doc_id, "INDEXING")
    _emit_stage(req, "INDEXING")
    counters = kb_store.publish_document_embeddings(req.tenant_id, req.kb_id, req.doc_id)
    if int(counters.get("embedded_count") or 0) < total_chunks:
        raise RuntimeError("document embedding incomplete")
    kb_store.set_document_status(req.tenant_id, req.kb_id, req.doc_id, "INDEXED")
    try:
        index_lifecycle.sync_document_to_active(req.tenant_id, req.kb_id, req.doc_id)
    except Exception:
        # The active revision is the publication authority. Do not expose a
        # document as indexed when its revision-scoped snapshot could not be
        # updated; the task retry can republish it safely.
        kb_store.set_document_status(req.tenant_id, req.kb_id, req.doc_id, "FAILED")
        raise
    kb_store.refresh_rag_index_manifest(req.tenant_id, req.kb_id)
    if req.auto_summary:
        _emit_stage(req, "SUMMARIZING")
        try:
            run_document_and_kb_summary(
                tenant_id=req.tenant_id,
                kb_id=req.kb_id,
                doc_id=req.doc_id,
                filename=safe_filename,
                content=cleaned_content,
            )
        except Exception:
            # Summary pipeline is best-effort; do not block core indexing.
            pass
    _emit_stage(req, "INDEXED", "SUCCEEDED")

    return {
        "doc_id": req.doc_id,
        "kb_id": req.kb_id,
        "chunks_indexed": total_chunks,
        "status": "INDEXED",
    }


def search_with_meta(req: SearchRequest, *, store=None) -> dict:
    retrieval_store = store or kb_store
    started = time.perf_counter()
    method = normalize_rank_method(req.rank_method)
    final_k = max(1, min(int(req.final_k or req.top_k), 50))
    rerank_k = max(final_k, min(int(req.rerank_k or 30), 100)) if req.rerank_enabled else final_k
    candidate_k = max(rerank_k, min(int(req.candidate_k or max(final_k * 5, 20)), 200))
    dense_items: list[dict] = []
    lexical_items: list[dict] = []
    dense_ms = 0.0
    lexical_ms = 0.0
    if method != "keyword":
        dense_started = time.perf_counter()
        expected_dim = retrieval_store.get_kb_embedding_dimension(req.tenant_id, req.kb_id)
        query_vec = embedding_provider.embed(
            req.tenant_id,
            req.query,
            task="query_embedding",
            kb_id=req.kb_id,
            doc_id="",
            local_dimensions=expected_dim or None,
        )
        if expected_dim > 0 and len(query_vec) != expected_dim:
            query_vec = embedding_provider.local_embed(req.query, dimensions=expected_dim)
        dense_items = retrieval_store.search_chunks(
            tenant_id=req.tenant_id,
            kb_id=req.kb_id,
            query_vector=query_vec,
            top_k=candidate_k,
            doc_ids=req.doc_ids,
            index_revision=req.index_revision,
        )
        dense_ms = (time.perf_counter() - dense_started) * 1000.0
    if method in {"hybrid", "rrf", "keyword"}:
        lexical_started = time.perf_counter()
        lexical_items = retrieval_store.search_lexical_chunks(
            tenant_id=req.tenant_id,
            kb_id=req.kb_id,
            query=req.query,
            top_k=candidate_k,
            doc_ids=req.doc_ids,
            index_revision=req.index_revision,
        )
        lexical_ms = (time.perf_counter() - lexical_started) * 1000.0
    effective_method = method
    if method in {"hybrid", "rrf"}:
        if lexical_items:
            evidence = fuse_rankings(
                dense_items,
                lexical_items,
                final_k=rerank_k,
                max_chunks_per_doc=req.max_chunks_per_doc,
            )
        else:
            effective_method = "vector_fallback"
            evidence = limit_per_document(dense_items, rerank_k, req.max_chunks_per_doc)
    elif method == "keyword":
        evidence = limit_per_document(lexical_items, rerank_k, req.max_chunks_per_doc)
    else:
        evidence = limit_per_document(dense_items, rerank_k, req.max_chunks_per_doc)
    rerank_meta: dict = {
        "requested": bool(req.rerank_enabled),
        "required": bool(req.rerank_required),
        "status": "not_requested",
        "candidate_count": len(evidence),
        "returned_count": min(len(evidence), final_k),
    }
    if req.rerank_enabled and evidence:
        pre_rerank_count = len(evidence[:rerank_k])
        try:
            rerank_result = reranker_provider.rerank(
                tenant_id=req.tenant_id,
                kb_id=req.kb_id,
                query=req.query,
                items=evidence[:rerank_k],
                top_n=final_k,
            )
            evidence = list(rerank_result.get("items") or [])
            rerank_meta = {"requested": True, "required": bool(req.rerank_required), **dict(rerank_result.get("meta") or {})}
        except Exception as exc:
            if req.rerank_required:
                raise
            evidence = evidence[:final_k]
            rerank_meta = {
                "requested": True,
                "required": False,
                "status": "fallback",
                "reason": str(exc)[:400],
                "candidate_count": pre_rerank_count,
                "returned_count": len(evidence),
            }
    else:
        evidence = evidence[:final_k]
        if req.rerank_enabled:
            rerank_meta["status"] = "skipped_empty"
    if req.context_window > 0 and evidence:
        evidence = retrieval_store.expand_chunk_context(
            req.tenant_id,
            req.kb_id,
            evidence,
            window=req.context_window,
            index_revision=req.index_revision,
        )
    for item in evidence:
        item["score"] = round(float(item.get("score") or 0.0), 6)
        scores = item.get("scores") or {}
        item["scores"] = {
            str(key): round(float(value), 6) if isinstance(value, (int, float)) else value
            for key, value in scores.items()
        }
    manifest = retrieval_store.get_rag_index_manifest(req.tenant_id, req.kb_id, req.index_revision)
    return {
        "evidence": evidence,
        "meta": {
            "requested_rank_method": method,
            "effective_rank_method": effective_method,
            "candidate_k": candidate_k,
            "final_k": final_k,
            "rerank_k": rerank_k if req.rerank_enabled else 0,
            "rerank": rerank_meta,
            "dense_candidates": len(dense_items),
            "lexical_candidates": len(lexical_items),
            "returned_count": len(evidence),
            "context_window": req.context_window,
            "dense_ms": round(dense_ms, 3),
            "lexical_ms": round(lexical_ms, 3),
            "total_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "index_revision": str(manifest.get("index_revision") or "legacy-v1"),
            "lexical_coverage": (
                round(
                    int(manifest.get("lexical_chunk_count") or 0)
                    / max(1, int(manifest.get("chunk_count") or 0)),
                    4,
                )
                if int(manifest.get("chunk_count") or 0) > 0
                else 0.0
            ),
        },
    }


def search(req: SearchRequest) -> list[dict]:
    return list(search_with_meta(req)["evidence"])
