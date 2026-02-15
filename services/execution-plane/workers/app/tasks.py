from .celery_app import celery_app
from models.embedding_provider import embedding_provider
from retrieval.schemas import IngestDocumentRequest
from retrieval.service import ingest_document, run_document_and_kb_summary, run_kb_summary_only
from retrieval.store import kb_store
from runtime.agent_runtime import AgentRunRequest, agent_runtime


def _context(payload: dict) -> dict:
    return {
        "tenant_id": payload.get("tenant_id", "dev-tenant"),
        "actor_id": payload.get("actor_id", "system"),
        "trace_id": payload.get("trace_id", "missing-trace"),
        "source_service": payload.get("source_service", "execution-worker"),
    }


def _emit_task_stage_progress(task_id: str, tenant_id: str, kb_id: str, doc_id: str, stage: str, current: int, total: int) -> None:
    c = max(0, int(current or 0))
    t = max(0, int(total or 0))
    pct = int((c * 100 / t)) if t > 0 else 0
    kb_store.update_task_progress(task_id, stage, c, t, pct)
    kb_store.append_task_event(
        task_id,
        tenant_id,
        kb_id,
        doc_id,
        "STAGE_PROGRESS",
        stage,
        {"status": "RUNNING", "current": c, "total": t, "progress_pct": pct},
    )


@celery_app.task(name="app.tasks.parse_document", bind=True, max_retries=2)
def parse_document(self, payload: dict) -> dict:
    task_id = payload.get("task_id") or self.request.id
    tenant_id = payload["tenant_id"]
    kb_id = payload["kb_id"]
    doc_id = payload["doc_id"]
    task_type = payload.get("task_type", "DOC_INGEST")
    kb_store.create_task_record(task_id, tenant_id, kb_id, doc_id, task_type, "PARSING", filename=payload.get("filename", "unknown.txt"))
    kb_store.update_task_record(task_id, "RUNNING", "PARSING")
    kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "PARSING", {})
    if kb_store.is_cancel_requested(task_id):
        kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.set_document_status(tenant_id, kb_id, doc_id, "CANCELLED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        result = ingest_document(
            IngestDocumentRequest(
                tenant_id=tenant_id,
                kb_id=kb_id,
                doc_id=doc_id,
                task_id=task_id,
                filename=payload.get("filename", "unknown.txt"),
                content=payload.get("content", ""),
                chunk_method=payload.get("chunk_method", "fixed"),
                chunk_delimiter=payload.get("chunk_delimiter"),
                chunk_size=payload.get("chunk_size", 500),
                chunk_overlap=payload.get("chunk_overlap", 50),
                auto_summary=bool(payload.get("auto_summary", False)),
            )
        )
        if kb_store.is_cancel_requested(task_id):
            kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.set_document_status(tenant_id, kb_id, doc_id, "CANCELLED")
            kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        kb_store.update_task_record(task_id, "SUCCEEDED", "INDEXED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "INDEXED", result)
        return {"status": "ok", "stage": "parse", "context": _context(payload), "result": result}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id) or str(exc) == "TASK_CANCELLED":
            kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.set_document_status(tenant_id, kb_id, doc_id, "CANCELLED")
            kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        kb_store.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        kb_store.mark_document_failed(tenant_id, kb_id, doc_id)
        kb_store.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            doc_id,
            "TASK_FAILED",
            "FAILED",
            {"error_message": str(exc)},
        )
        raise


@celery_app.task(name="app.tasks.commit_chunk_drafts", bind=True, max_retries=1)
def commit_chunk_drafts_task(self, payload: dict) -> dict:
    task_id = payload.get("task_id") or self.request.id
    tenant_id = payload["tenant_id"]
    kb_id = payload["kb_id"]
    doc_id = payload["doc_id"]
    kb_store.create_task_record(task_id, tenant_id, kb_id, doc_id, "CHUNK_COMMIT", "EMBEDDING")
    kb_store.update_task_record(task_id, "RUNNING", "EMBEDDING")
    kb_store.set_document_status(tenant_id, kb_id, doc_id, "EMBEDDING")
    kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "EMBEDDING", {})
    kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "EMBEDDING", {"status": "RUNNING"})
    if kb_store.is_cancel_requested(task_id):
        kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.set_document_status(tenant_id, kb_id, doc_id, "CANCELLED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        result = kb_store.commit_chunk_drafts(
            tenant_id=tenant_id,
            kb_id=kb_id,
            doc_id=doc_id,
            embed_fn=lambda t, text: embedding_provider.embed(
                t,
                text,
                task="chunk_commit_embedding",
                kb_id=kb_id,
                doc_id=doc_id,
            ),
        )
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "INDEXING", {"status": "RUNNING"})
        kb_store.set_document_status(tenant_id, kb_id, doc_id, "INDEXED")
        kb_store.update_task_record(task_id, "SUCCEEDED", "INDEXED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "INDEXED", {"status": "SUCCEEDED"})
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "INDEXED", result)
        return {"status": "ok", "stage": "commit", "context": _context(payload), "result": result}
    except Exception as exc:
        kb_store.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        kb_store.mark_document_failed(tenant_id, kb_id, doc_id)
        kb_store.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            doc_id,
            "TASK_FAILED",
            "FAILED",
            {"error_message": str(exc)},
        )
        raise


@celery_app.task(name="app.tasks.embed_document", bind=True, max_retries=2)
def embed_document(self, payload: dict) -> dict:
    return {"status": "ok", "stage": "embed", "context": _context(payload)}


@celery_app.task(name="app.tasks.index_document", bind=True, max_retries=2)
def index_document(self, payload: dict) -> dict:
    return {"status": "ok", "stage": "index", "context": _context(payload)}


@celery_app.task(name="app.tasks.run_agent", bind=True, max_retries=1)
def run_agent(self, payload: dict) -> dict:
    result = agent_runtime.run(
        AgentRunRequest(
            tenant_id=payload["tenant_id"],
            actor_id=payload.get("actor_id", "system"),
            trace_id=payload.get("trace_id", "missing-trace"),
            source_service=payload.get("source_service", "execution-worker"),
            kb_id=payload["kb_id"],
            question=payload["question"],
            top_k=payload.get("top_k", 5),
            conversation_id=payload.get("conversation_id"),
            run_id=payload.get("run_id"),
            allow_generation_without_evidence=payload.get("allow_generation_without_evidence", True),
        )
    )
    return {
        "status": result["status"],
        "stage": "agent",
        "low_evidence": result.get("low_evidence", False),
        "answer": result.get("answer"),
        "citations": result.get("citations", []),
        "metrics": result.get("metrics", {}),
        "context": _context(payload),
    }


@celery_app.task(name="app.tasks.summarize_document", bind=True, max_retries=1)
def summarize_document_task(self, payload: dict) -> dict:
    task_id = payload.get("task_id") or self.request.id
    tenant_id = payload["tenant_id"]
    kb_id = payload["kb_id"]
    doc_id = payload["doc_id"]
    task_type = payload.get("task_type", "DOC_SUMMARIZE")
    filename = payload.get("filename", "unknown.txt")
    kb_store.create_task_record(task_id, tenant_id, kb_id, doc_id, task_type, "SUMMARIZING_DOC", filename=filename)
    kb_store.update_task_record(task_id, "RUNNING", "SUMMARIZING_DOC")
    kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "SUMMARIZING_DOC", {})
    kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "SUMMARIZING_DOC", {"status": "RUNNING"})
    if kb_store.is_cancel_requested(task_id):
        kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        def _on_progress(stage: str, current: int, total: int) -> None:
            _emit_task_stage_progress(task_id, tenant_id, kb_id, doc_id, stage, current, total)
            if kb_store.is_cancel_requested(task_id):
                raise RuntimeError("TASK_CANCELLED")

        def _should_cancel() -> bool:
            return kb_store.is_cancel_requested(task_id)

        result = run_document_and_kb_summary(
            tenant_id=tenant_id,
            kb_id=kb_id,
            doc_id=doc_id,
            filename=filename,
            content=payload.get("content", ""),
            progress_cb=_on_progress,
            should_cancel=_should_cancel,
        )
        if kb_store.is_cancel_requested(task_id):
            kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        kb_store.update_task_record(task_id, "SUCCEEDED", "SUMMARIZED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "SUMMARIZED", {"status": "SUCCEEDED"})
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "SUMMARIZED", result)
        return {"status": "ok", "stage": "summarize", "context": _context(payload), "result": result}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        kb_store.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        kb_store.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            doc_id,
            "TASK_FAILED",
            "FAILED",
            {"error_message": str(exc)},
        )
        raise


@celery_app.task(name="app.tasks.summarize_kb", bind=True, max_retries=1)
def summarize_kb_task(self, payload: dict) -> dict:
    task_id = payload.get("task_id") or self.request.id
    tenant_id = payload["tenant_id"]
    kb_id = payload["kb_id"]
    kb_name = payload.get("kb_name", kb_id)
    kb_store.create_task_record(task_id, tenant_id, kb_id, "", "KB_SUMMARIZE", "SUMMARIZING_KB", filename=kb_name)
    kb_store.update_task_record(task_id, "RUNNING", "SUMMARIZING_KB")
    kb_store.append_task_event(task_id, tenant_id, kb_id, "", "WORKER_STARTED", "SUMMARIZING_KB", {})
    kb_store.append_task_event(task_id, tenant_id, kb_id, "", "STAGE_CHANGED", "SUMMARIZING_KB", {"status": "RUNNING"})
    if kb_store.is_cancel_requested(task_id):
        kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, "", "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        def _on_progress(stage: str, current: int, total: int) -> None:
            _emit_task_stage_progress(task_id, tenant_id, kb_id, "", stage, current, total)
            if kb_store.is_cancel_requested(task_id):
                raise RuntimeError("TASK_CANCELLED")

        def _should_cancel() -> bool:
            return kb_store.is_cancel_requested(task_id)

        result = run_kb_summary_only(tenant_id=tenant_id, kb_id=kb_id, progress_cb=_on_progress, should_cancel=_should_cancel)
        kb_store.update_task_record(task_id, "SUCCEEDED", "SUMMARIZED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, "", "STAGE_CHANGED", "SUMMARIZED", {"status": "SUCCEEDED"})
        kb_store.append_task_event(task_id, tenant_id, kb_id, "", "TASK_COMPLETED", "SUMMARIZED", result)
        return {"status": "ok", "stage": "summarize_kb", "context": _context(payload), "result": result}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.append_task_event(task_id, tenant_id, kb_id, "", "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        kb_store.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        kb_store.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            "",
            "TASK_FAILED",
            "FAILED",
            {"error_message": str(exc)},
        )
        raise


@celery_app.task(name="app.tasks.move_document", bind=True, max_retries=1)
def move_document_task(self, payload: dict) -> dict:
    task_id = payload.get("task_id") or self.request.id
    tenant_id = payload["tenant_id"]
    kb_id = payload["kb_id"]
    doc_id = payload["doc_id"]
    target_kb_id = payload.get("target_kb_id", "")
    task_type = payload.get("task_type", "DOC_MOVE")
    filename = payload.get("filename", "unknown.txt")
    kb_store.create_task_record(task_id, tenant_id, kb_id, doc_id, task_type, "MOVING", filename=filename)
    kb_store.update_task_record(task_id, "RUNNING", "MOVING")
    kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "MOVING", {"target_kb_id": target_kb_id})
    if kb_store.is_cancel_requested(task_id):
        kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        moved = kb_store.move_document(tenant_id, kb_id, target_kb_id, doc_id)
        if not moved:
            raise ValueError("document not found")
        kb_store.update_task_record(task_id, "SUCCEEDED", "MOVED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "MOVED", {"target_kb_id": target_kb_id})
        return {"status": "ok", "stage": "move", "context": _context(payload), "result": {"target_kb_id": target_kb_id}}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        kb_store.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        kb_store.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            doc_id,
            "TASK_FAILED",
            "FAILED",
            {"error_message": str(exc)},
        )
        raise


@celery_app.task(name="app.tasks.tag_document", bind=True, max_retries=1)
def tag_document_task(self, payload: dict) -> dict:
    task_id = payload.get("task_id") or self.request.id
    tenant_id = payload["tenant_id"]
    kb_id = payload["kb_id"]
    doc_id = payload["doc_id"]
    tags = [str(x or "").strip() for x in (payload.get("tags") or []) if str(x or "").strip()]
    task_type = payload.get("task_type", "DOC_TAG")
    filename = payload.get("filename", "unknown.txt")
    kb_store.create_task_record(task_id, tenant_id, kb_id, doc_id, task_type, "TAGGING", filename=filename)
    kb_store.update_task_record(task_id, "RUNNING", "TAGGING")
    kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "TAGGING", {"tags": tags})
    if kb_store.is_cancel_requested(task_id):
        kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        ok = kb_store.add_document_tags(tenant_id, kb_id, doc_id, tags)
        if not ok:
            raise ValueError("document not found")
        kb_store.update_task_record(task_id, "SUCCEEDED", "TAGGED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "TAGGED", {"tags": tags})
        return {"status": "ok", "stage": "tag", "context": _context(payload), "result": {"tags": tags}}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        kb_store.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        kb_store.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            doc_id,
            "TASK_FAILED",
            "FAILED",
            {"error_message": str(exc)},
        )
        raise


@celery_app.task(name="app.tasks.delete_document", bind=True, max_retries=1)
def delete_document_task(self, payload: dict) -> dict:
    task_id = payload.get("task_id") or self.request.id
    tenant_id = payload["tenant_id"]
    kb_id = payload["kb_id"]
    doc_id = payload["doc_id"]
    task_type = payload.get("task_type", "DOC_DELETE")
    filename = payload.get("filename", "unknown.txt")
    kb_store.create_task_record(task_id, tenant_id, kb_id, doc_id, task_type, "DELETING", filename=filename)
    kb_store.update_task_record(task_id, "RUNNING", "DELETING")
    kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "DELETING", {})
    if kb_store.is_cancel_requested(task_id):
        kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        active_task_ids = kb_store.find_active_doc_task_ids(tenant_id, kb_id, doc_id)
        for active_task_id in active_task_ids:
            if active_task_id == task_id:
                continue
            kb_store.request_cancel_task(active_task_id)
            try:
                celery_app.control.revoke(active_task_id, terminate=True)
            except Exception:
                pass
        deleted = kb_store.delete_document(tenant_id, kb_id, doc_id)
        if not deleted:
            raise ValueError("document not found")
        kb_store.update_task_record(task_id, "SUCCEEDED", "DELETED")
        kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "DELETED", {})
        return {"status": "ok", "stage": "delete", "context": _context(payload), "result": {"deleted": doc_id}}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            kb_store.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        kb_store.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        kb_store.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            doc_id,
            "TASK_FAILED",
            "FAILED",
            {"error_message": str(exc)},
        )
        raise
