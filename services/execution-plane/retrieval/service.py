import hashlib
import re
from typing import Callable

from models.ai_gateway import ai_gateway
from models.embedding_provider import embedding_provider
from retrieval.schemas import IngestDocumentRequest, SearchRequest
from retrieval.store import ChunkRecord, kb_store


def _strip_nul(text: str) -> str:
    return (text or "").replace("\x00", "")

def _emit_stage(req: IngestDocumentRequest, stage: str, status: str = "RUNNING") -> None:
    if not req.task_id:
        return
    task_status = "SUCCEEDED" if status == "SUCCEEDED" else "RUNNING"
    kb_store.update_task_record(req.task_id, task_status, stage)
    kb_store.append_task_event(
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
    kb_store.update_task_progress(req.task_id, stage, c, t, pct)
    kb_store.append_task_event(
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
    chunk_texts = _chunk_text(
        preprocessed,
        req.chunk_size,
        req.chunk_overlap,
        req.chunk_method,
        req.chunk_delimiter,
    )
    max_chars = embedding_provider.max_input_chars(req.tenant_id)
    if max_chars:
        safe_chunks: list[str] = []
        for text in chunk_texts:
            safe_chunks.extend(_split_for_embedding_limit(text, max_chars=max_chars, overlap=min(40, req.chunk_overlap)))
        chunk_texts = safe_chunks
    kb_store.set_document_chunking_preview(req.tenant_id, req.kb_id, req.doc_id, len(chunk_texts))
    kb_store.replace_chunks_preview(
        req.tenant_id,
        req.kb_id,
        req.doc_id,
        chunk_texts,
        safe_filename,
    )
    _emit_stage(req, "CHUNKING")

    kb_store.set_document_status(req.tenant_id, req.kb_id, req.doc_id, "EMBEDDING")
    _emit_stage(req, "EMBEDDING")
    total_chunks = len(chunk_texts)
    _emit_progress(req, "EMBEDDING", 0, total_chunks)
    chunks: list[ChunkRecord] = []
    emit_step = max(1, total_chunks // 50) if total_chunks > 0 else 1
    for idx, chunk_text in enumerate(chunk_texts):
        if _is_task_cancel_requested(req):
            raise RuntimeError("TASK_CANCELLED")
        raw = f"{req.tenant_id}:{req.kb_id}:{req.doc_id}:{idx}:{chunk_text}"
        chunk_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()  # nosec B324
        vector = embedding_provider.embed(
            req.tenant_id,
            chunk_text,
            task="doc_embedding",
            kb_id=req.kb_id,
            doc_id=req.doc_id,
        )
        chunks.append(
            ChunkRecord(
                tenant_id=req.tenant_id,
                kb_id=req.kb_id,
                doc_id=req.doc_id,
                chunk_id=chunk_id,
                chunk_index=idx,
                text=chunk_text,
                embedding=vector,
                metadata={"filename": safe_filename},
            )
        )
        done = idx + 1
        if done == total_chunks or done % emit_step == 0:
            _emit_progress(req, "EMBEDDING", done, total_chunks)

    kb_store.set_document_status(req.tenant_id, req.kb_id, req.doc_id, "INDEXING")
    _emit_stage(req, "INDEXING")
    kb_store.replace_chunks(req.tenant_id, req.kb_id, req.doc_id, chunks)
    kb_store.set_document_status(req.tenant_id, req.kb_id, req.doc_id, "INDEXED")
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
        "chunks_indexed": len(chunks),
        "status": "INDEXED",
    }


def search(req: SearchRequest) -> list[dict]:
    query_vec = embedding_provider.embed(
        req.tenant_id,
        req.query,
        task="query_embedding",
        kb_id=req.kb_id,
        doc_id="",
    )
    data = kb_store.search_chunks(
        tenant_id=req.tenant_id,
        kb_id=req.kb_id,
        query_vector=query_vec,
        top_k=req.top_k,
        doc_ids=req.doc_ids,
    )
    for item in data:
        item["score"] = round(item["score"], 4)
    return data
