from .celery_app import celery_app
from models.embedding_provider import embedding_provider
from retrieval.schemas import IngestDocumentRequest
from retrieval.service import ingest_document
from retrieval.store import kb_store
from runtime.agent_runtime import AgentRunRequest, agent_runtime


def _context(payload: dict) -> dict:
    return {
        "tenant_id": payload.get("tenant_id", "dev-tenant"),
        "actor_id": payload.get("actor_id", "system"),
        "trace_id": payload.get("trace_id", "missing-trace"),
        "source_service": payload.get("source_service", "execution-worker"),
    }


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
            embed_fn=embedding_provider.embed,
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
