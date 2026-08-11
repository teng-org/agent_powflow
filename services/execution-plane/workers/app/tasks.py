from .celery_app import celery_app
from runtime.object_storage import ObjectStorageError, object_storage
from celery import Task
from models.embedding_provider import embedding_provider
from retrieval.schemas import IngestDocumentRequest
from retrieval.service import ingest_document, run_document_and_kb_summary, run_kb_summary_only
from retrieval.store import InvalidTaskStateTransition, kb_store
from runtime.agent_runtime import AgentRunRequest, agent_runtime
from runtime.control_plane_client import ControlPlaneCallbackError, control_plane_client
from runtime.control_run_dead_letter import publish_control_run_dead_letter
from runtime.execution_lease import ExecutionLeaseGuard, ExecutionLeaseLost
from runtime.provider_idempotency import ProviderInvocationInProgress
from runtime.task_authority import TaskAuthorityBridge
from runtime.workflow_runtime import WorkflowSuspended, execute_workflow
import hashlib
import os
import socket


task_authority = TaskAuthorityBridge(kb_store)


class ControlRunTask(Task):
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        try:
            publish_control_run_dead_letter(celery_app, exc, task_id, args)
        except Exception:
            # Broker failure is already represented by the task failure log; do
            # not hide the original exception with a secondary publish error.
            pass
        super().on_failure(exc, task_id, args, kwargs, einfo)


def _payload_content(payload: dict) -> str:
    inline = payload.get("content")
    if inline is not None and str(inline) != "":
        return str(inline)
    bucket = str(payload.get("object_bucket") or "")
    key = str(payload.get("content_object_key") or "")
    checksum = str(payload.get("content_object_sha256") or "")
    if not bucket or not key or not checksum:
        raise ObjectStorageError("task payload has neither inline content nor a complete object reference")
    raw = object_storage.get_bytes(bucket, key, expected_sha256=checksum)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ObjectStorageError("extracted text object is not valid UTF-8") from exc


@celery_app.task(name="agentpowflow.control_run.dead_letter", bind=True, max_retries=8, ignore_result=True)
def ingest_control_run_dead_letter(self, payload: dict) -> dict:
    request = {
        "tenant_id": str(payload.get("tenant_id") or ""),
        "run_id": str(payload.get("run_id") or ""),
        "failed_task_id": str(payload.get("failed_task_id") or self.request.id),
        "source_queue": str(payload.get("source_queue") or "control_run_queue"),
        "error": str(payload.get("error") or "")[:2000],
        "payload": payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
    }
    try:
        response = control_plane_client.record_dead_letter(request)
        return {"status": "persisted", "duplicate": bool(response.get("duplicate")), "run_id": request["run_id"]}
    except ControlPlaneCallbackError as exc:
        raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 60))


def _context(payload: dict) -> dict:
    return {
        "tenant_id": payload.get("tenant_id", "dev-tenant"),
        "actor_id": payload.get("actor_id", "system"),
        "trace_id": payload.get("trace_id", "missing-trace"),
        "source_service": payload.get("source_service", "execution-worker"),
    }


def _is_retryable_provider_failure(exc: Exception) -> bool:
    message = str(exc or "").strip().lower()
    return any(token in message for token in (
        "timed out", "timeout", "rate limit", "too many requests", "http 429",
        "http 500", "http 502", "http 503", "http 504", "connection reset",
        "connection refused", "temporarily unavailable", "transport error",
    ))


def _retry_provider_task(task, exc: Exception, task_id: str, tenant_id: str, kb_id: str, doc_id: str, stage: str) -> None:
    if not _is_retryable_provider_failure(exc) or task.request.retries >= task.max_retries:
        return
    countdown = min(2 ** task.request.retries, 8)
    task_authority.append_task_event(
        task_id, tenant_id, kb_id, doc_id, "TASK_RETRY_SCHEDULED", stage,
        {"retry_no": task.request.retries + 1, "countdown_seconds": countdown, "reason": str(exc)[:500]},
    )
    raise task.retry(exc=exc, countdown=countdown)


def _control_run_start_progress(payload: dict) -> int:
    """Keep run progress monotonic when a suspended workflow is resumed."""
    approval_resume = payload.get("approval_resume")
    if not isinstance(approval_resume, dict):
        return 10
    checkpoint = approval_resume.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return 10
    try:
        return max(10, min(int(checkpoint.get("progress") or 10), 99))
    except (TypeError, ValueError):
        return 10


def _begin_persistent_task(
    *,
    task_id: str,
    tenant_id: str,
    kb_id: str,
    doc_id: str,
    task_type: str,
    stage: str,
    payload: dict,
    filename: str = "",
) -> dict | None:
    task_authority.ensure_task(task_id, tenant_id, kb_id, doc_id, task_type, stage, filename=filename)
    record = kb_store.get_task(task_id, tenant_id=tenant_id)
    if record is None:
        raise RuntimeError("persistent task disappeared after creation")
    current_status = str(record.get("status") or "").upper()
    if current_status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
        return {
            "status": "already_terminal",
            "state": current_status,
            "stage": str(record.get("stage") or stage),
            "task_id": task_id,
            "context": _context(payload),
        }
    if current_status == "CANCELLING" or bool(record.get("cancel_requested")):
        task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
        return {"status": "cancelled", "state": "CANCELLED", "task_id": task_id, "context": _context(payload)}
    try:
        task_authority.update_task_record(task_id, "RUNNING", stage)
    except InvalidTaskStateTransition:
        latest = kb_store.get_task(task_id, tenant_id=tenant_id) or {}
        latest_status = str(latest.get("status") or "").upper()
        if latest_status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return {
                "status": "already_terminal",
                "state": latest_status,
                "stage": str(latest.get("stage") or stage),
                "task_id": task_id,
                "context": _context(payload),
            }
        if latest_status == "CANCELLING" or bool(latest.get("cancel_requested")):
            task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
            return {"status": "cancelled", "state": "CANCELLED", "task_id": task_id, "context": _context(payload)}
        raise
    return None


def _emit_task_stage_progress(task_id: str, tenant_id: str, kb_id: str, doc_id: str, stage: str, current: int, total: int) -> None:
    c = max(0, int(current or 0))
    t = max(0, int(total or 0))
    pct = int((c * 100 / t)) if t > 0 else 0
    task_authority.update_task_progress(task_id, stage, c, t, pct)
    task_authority.append_task_event(
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
    early = _begin_persistent_task(
        task_id=task_id, tenant_id=tenant_id, kb_id=kb_id, doc_id=doc_id, task_type=task_type,
        stage="PARSING", payload=payload, filename=payload.get("filename", "unknown.txt"),
    )
    if early is not None:
        return early
    task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "PARSING", {})
    if kb_store.is_cancel_requested(task_id):
        task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.set_document_status(tenant_id, kb_id, doc_id, "CANCELLED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        result = ingest_document(
            IngestDocumentRequest(
                tenant_id=tenant_id,
                kb_id=kb_id,
                doc_id=doc_id,
                task_id=task_id,
                filename=payload.get("filename", "unknown.txt"),
                content=_payload_content(payload),
                chunk_method=payload.get("chunk_method", "fixed"),
                chunk_delimiter=payload.get("chunk_delimiter"),
                chunk_size=payload.get("chunk_size", 500),
                chunk_overlap=payload.get("chunk_overlap", 50),
                auto_summary=bool(payload.get("auto_summary", False)),
            )
        )
        if kb_store.is_cancel_requested(task_id):
            task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.set_document_status(tenant_id, kb_id, doc_id, "CANCELLED")
            task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        task_authority.update_task_record(task_id, "SUCCEEDED", "INDEXED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "INDEXED", result)
        return {"status": "ok", "stage": "parse", "context": _context(payload), "result": result}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id) or str(exc) == "TASK_CANCELLED":
            task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
            kb_store.set_document_status(tenant_id, kb_id, doc_id, "CANCELLED")
            task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        task_authority.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        kb_store.mark_document_failed(tenant_id, kb_id, doc_id)
        task_authority.append_task_event(
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
    early = _begin_persistent_task(
        task_id=task_id, tenant_id=tenant_id, kb_id=kb_id, doc_id=doc_id, task_type="CHUNK_COMMIT",
        stage="EMBEDDING", payload=payload,
    )
    if early is not None:
        return early
    kb_store.set_document_status(tenant_id, kb_id, doc_id, "EMBEDDING")
    task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "EMBEDDING", {})
    task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "EMBEDDING", {"status": "RUNNING"})
    if kb_store.is_cancel_requested(task_id):
        task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
        kb_store.set_document_status(tenant_id, kb_id, doc_id, "CANCELLED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
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
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "INDEXING", {"status": "RUNNING"})
        kb_store.set_document_status(tenant_id, kb_id, doc_id, "INDEXED")
        task_authority.update_task_record(task_id, "SUCCEEDED", "INDEXED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "INDEXED", {"status": "SUCCEEDED"})
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "INDEXED", result)
        return {"status": "ok", "stage": "commit", "context": _context(payload), "result": result}
    except Exception as exc:
        task_authority.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        kb_store.mark_document_failed(tenant_id, kb_id, doc_id)
        task_authority.append_task_event(
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


@celery_app.task(
    name="app.tasks.execute_control_run",
    bind=True,
    base=ControlRunTask,
    max_retries=3,
    ignore_result=True,
)
def execute_control_run(self, payload: dict) -> dict:
    run_id = payload["run_id"]
    tenant_id = payload["tenant_id"]
    worker_id = f"{socket.gethostname()}:{self.request.id}:{self.request.retries}"
    lease = ExecutionLeaseGuard(control_plane_client, run_id, tenant_id, worker_id)
    try:
        claim = lease.claim()
        if not claim.get("acquired"):
            return {"status": "lease_rejected", "run_id": run_id, "reason": claim.get("reason", "unknown")}
        lease.start()
        execution_attempt = int(claim.get("attempt_no") or 1)
        started = control_plane_client.append_event(
            run_id,
            {
                "event_id": f"execution:{run_id}:attempt:{execution_attempt}:started",
                "tenant_id": tenant_id,
                "source_service": "execution-worker",
                "event_type": "RUN_STARTED",
                "status": "RUNNING",
                "stage": "workflow.start",
                "progress": _control_run_start_progress(payload),
                "payload": {"task_id": self.request.id},
            },
        )

        def _on_node_event(item: dict) -> None:
            lease.assert_valid()
            node_id = str(item.get("node_id") or "unknown")
            node_key = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16]
            event_type = str(item.get("event_type") or "NODE_PROGRESS")
            details = item.get("details") if isinstance(item.get("details"), dict) else {}
            attempt_no = int(details.get("attempt_no") or 0)
            attempt_suffix = f":attempt:{attempt_no}" if attempt_no > 0 else ""
            control_plane_client.append_event(
                run_id,
                {
                    "event_id": f"execution:{run_id}:execution:{execution_attempt}:node:{node_key}{attempt_suffix}:{event_type.lower()}",
                    "tenant_id": tenant_id,
                    "source_service": "execution-worker",
                    "event_type": event_type,
                    "status": "RUNNING",
                    "stage": f"node.{str(item.get('node_type') or 'unknown')}"[:64],
                    "progress": int(item.get("progress") or 10),
                    "payload": {
                        "node_id": node_id,
                        "node_type": str(item.get("node_type") or ""),
                        "ordinal": int(item.get("ordinal") or 0),
                        "details": details,
                    },
                },
            )

        result = execute_workflow(payload, should_cancel=lease.should_abort, on_node_event=_on_node_event)
        lease.assert_valid()
        current = control_plane_client.get_execution_context(run_id)
        if current.get("status") == "CANCELLED":
            return {"status": "cancelled", "run_id": run_id}

        lease.assert_valid()
        control_plane_client.append_event(
            run_id,
            {
                "event_id": f"execution:{run_id}:attempt:{execution_attempt}:succeeded",
                "tenant_id": tenant_id,
                "source_service": "execution-worker",
                "event_type": "RUN_SUCCEEDED",
                "status": "SUCCEEDED",
                "stage": "workflow.completed",
                "progress": 100,
                "payload": {"provider_mode": result["provider_mode"]},
                "output_payload": result["output_payload"],
                "citations": result.get("citations", []),
                "metrics": result.get("metrics", {}),
            },
        )
        return {"status": "succeeded", "run_id": run_id}
    except WorkflowSuspended as suspension:
        try:
            lease.assert_valid()
            approval = control_plane_client.suspend_for_approval(
                run_id,
                {
                    "tenant_id": tenant_id,
                    "lease_token": lease.lease_token,
                    "node_id": suspension.node_id,
                    "node_type": suspension.node_type,
                    "title": suspension.title,
                    "instructions": suspension.instructions,
                    "assignee_roles": suspension.assignee_roles,
                    "timeout_seconds": suspension.timeout_seconds,
                    "timeout_action": suspension.timeout_action,
                    "checkpoint": suspension.checkpoint,
                    "progress": suspension.progress,
                },
            )
            return {"status": "waiting_approval", "run_id": run_id, "approval_id": approval.get("approval_id")}
        except (ControlPlaneCallbackError, ExecutionLeaseLost) as exc:
            raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 8))
    except (ControlPlaneCallbackError, ExecutionLeaseLost, ProviderInvocationInProgress) as exc:
        raise self.retry(exc=exc, countdown=min(2 ** self.request.retries, 8))
    except Exception as exc:
        try:
            current = control_plane_client.get_execution_context(run_id)
            if current.get("status") == "CANCELLED":
                return {"status": "cancelled", "run_id": run_id}
            if str(exc) == "TASK_CANCELLED" and lease.should_abort():
                raise self.retry(
                    exc=ExecutionLeaseLost(lease.lost_reason or "execution interrupted after lease loss"),
                    countdown=min(2 ** self.request.retries, 8),
                )
            if current.get("status") not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                control_plane_client.append_event(
                    run_id,
                    {
                        "event_id": f"execution:{run_id}:attempt:{execution_attempt}:failed",
                        "tenant_id": tenant_id,
                        "source_service": "execution-worker",
                        "event_type": "RUN_FAILED",
                        "status": "FAILED",
                        "stage": "workflow.failed",
                        "progress": 100,
                        "payload": {},
                        "error_code": "WORKFLOW_EXECUTION_FAILED",
                        "error_message": str(exc)[:2000],
                    },
                )
        except ControlPlaneCallbackError as callback_exc:
            raise self.retry(exc=callback_exc, countdown=min(2 ** self.request.retries, 8))
        return {"status": "failed", "run_id": run_id, "error": str(exc)}
    finally:
        lease.close()


@celery_app.task(name="app.tasks.summarize_document", bind=True, max_retries=2)
def summarize_document_task(self, payload: dict) -> dict:
    task_id = payload.get("task_id") or self.request.id
    tenant_id = payload["tenant_id"]
    kb_id = payload["kb_id"]
    doc_id = payload["doc_id"]
    task_type = payload.get("task_type", "DOC_SUMMARIZE")
    filename = payload.get("filename", "unknown.txt")
    early = _begin_persistent_task(
        task_id=task_id, tenant_id=tenant_id, kb_id=kb_id, doc_id=doc_id, task_type=task_type,
        stage="SUMMARIZING_DOC", payload=payload, filename=filename,
    )
    if early is not None:
        return early
    task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "SUMMARIZING_DOC", {})
    task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "SUMMARIZING_DOC", {"status": "RUNNING"})
    if kb_store.is_cancel_requested(task_id):
        task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
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
            content=_payload_content(payload),
            progress_cb=_on_progress,
            should_cancel=_should_cancel,
        )
        if kb_store.is_cancel_requested(task_id):
            task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
            task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        task_authority.update_task_record(task_id, "SUCCEEDED", "SUMMARIZED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "STAGE_CHANGED", "SUMMARIZED", {"status": "SUCCEEDED"})
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "SUMMARIZED", result)
        return {"status": "ok", "stage": "summarize", "context": _context(payload), "result": result}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
            task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        _retry_provider_task(self, exc, task_id, tenant_id, kb_id, doc_id, "SUMMARIZING_DOC")
        task_authority.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        task_authority.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            doc_id,
            "TASK_FAILED",
            "FAILED",
            {"error_message": str(exc)},
        )
        raise


@celery_app.task(name="app.tasks.summarize_kb", bind=True, max_retries=2)
def summarize_kb_task(self, payload: dict) -> dict:
    task_id = payload.get("task_id") or self.request.id
    tenant_id = payload["tenant_id"]
    kb_id = payload["kb_id"]
    kb_name = payload.get("kb_name", kb_id)
    early = _begin_persistent_task(
        task_id=task_id, tenant_id=tenant_id, kb_id=kb_id, doc_id="", task_type="KB_SUMMARIZE",
        stage="SUMMARIZING_KB", payload=payload, filename=kb_name,
    )
    if early is not None:
        return early
    task_authority.append_task_event(task_id, tenant_id, kb_id, "", "WORKER_STARTED", "SUMMARIZING_KB", {})
    task_authority.append_task_event(task_id, tenant_id, kb_id, "", "STAGE_CHANGED", "SUMMARIZING_KB", {"status": "RUNNING"})
    if kb_store.is_cancel_requested(task_id):
        task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, "", "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        def _on_progress(stage: str, current: int, total: int) -> None:
            _emit_task_stage_progress(task_id, tenant_id, kb_id, "", stage, current, total)
            if kb_store.is_cancel_requested(task_id):
                raise RuntimeError("TASK_CANCELLED")

        def _should_cancel() -> bool:
            return kb_store.is_cancel_requested(task_id)

        result = run_kb_summary_only(tenant_id=tenant_id, kb_id=kb_id, progress_cb=_on_progress, should_cancel=_should_cancel)
        task_authority.update_task_record(task_id, "SUCCEEDED", "SUMMARIZED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, "", "STAGE_CHANGED", "SUMMARIZED", {"status": "SUCCEEDED"})
        task_authority.append_task_event(task_id, tenant_id, kb_id, "", "TASK_COMPLETED", "SUMMARIZED", result)
        return {"status": "ok", "stage": "summarize_kb", "context": _context(payload), "result": result}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
            task_authority.append_task_event(task_id, tenant_id, kb_id, "", "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        _retry_provider_task(self, exc, task_id, tenant_id, kb_id, "", "SUMMARIZING_KB")
        task_authority.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        task_authority.append_task_event(
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
    early = _begin_persistent_task(
        task_id=task_id, tenant_id=tenant_id, kb_id=kb_id, doc_id=doc_id, task_type=task_type,
        stage="MOVING", payload=payload, filename=filename,
    )
    if early is not None:
        return early
    task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "MOVING", {"target_kb_id": target_kb_id})
    if kb_store.is_cancel_requested(task_id):
        task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        moved = kb_store.move_document(tenant_id, kb_id, target_kb_id, doc_id)
        if not moved:
            raise ValueError("document not found")
        task_authority.update_task_record(task_id, "SUCCEEDED", "MOVED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "MOVED", {"target_kb_id": target_kb_id})
        return {"status": "ok", "stage": "move", "context": _context(payload), "result": {"target_kb_id": target_kb_id}}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
            task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        task_authority.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        task_authority.append_task_event(
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
    tags = list(dict.fromkeys(str(x or "").strip() for x in (payload.get("tags") or []) if str(x or "").strip()))
    tag_mode = str(payload.get("tag_mode") or "add").strip().lower()
    task_type = payload.get("task_type", "DOC_TAG")
    filename = payload.get("filename", "unknown.txt")
    early = _begin_persistent_task(
        task_id=task_id, tenant_id=tenant_id, kb_id=kb_id, doc_id=doc_id, task_type=task_type,
        stage="TAGGING", payload=payload, filename=filename,
    )
    if early is not None:
        return early
    task_authority.append_task_event(
        task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "TAGGING", {"tags": tags, "mode": tag_mode}
    )
    if kb_store.is_cancel_requested(task_id):
        task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
        return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
    try:
        ok = (
            kb_store.set_document_tags(tenant_id, kb_id, doc_id, tags)
            if tag_mode == "replace"
            else kb_store.add_document_tags(tenant_id, kb_id, doc_id, tags)
        )
        if not ok:
            raise ValueError("document not found")
        task_authority.update_task_record(task_id, "SUCCEEDED", "TAGGED")
        task_authority.append_task_event(
            task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "TAGGED", {"tags": tags, "mode": tag_mode}
        )
        return {
            "status": "ok", "stage": "tag", "context": _context(payload),
            "result": {"tags": tags, "mode": tag_mode},
        }
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
            task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        task_authority.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        task_authority.append_task_event(
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
    early = _begin_persistent_task(
        task_id=task_id, tenant_id=tenant_id, kb_id=kb_id, doc_id=doc_id, task_type=task_type,
        stage="DELETING", payload=payload, filename=filename,
    )
    if early is not None:
        return early
    task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "WORKER_STARTED", "DELETING", {})
    if kb_store.is_cancel_requested(task_id):
        task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
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
        try:
            repair_cross_store_task.apply_async(countdown=1)
        except Exception:
            # The PostgreSQL repair ledger is authoritative; broker outage must
            # not turn a logically completed deletion into a failed task.
            pass
        task_authority.update_task_record(task_id, "SUCCEEDED", "DELETED")
        task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_COMPLETED", "DELETED", {})
        return {"status": "ok", "stage": "delete", "context": _context(payload), "result": {"deleted": doc_id}}
    except Exception as exc:
        if kb_store.is_cancel_requested(task_id):
            task_authority.update_task_record(task_id, "CANCELLED", "CANCELLED")
            task_authority.append_task_event(task_id, tenant_id, kb_id, doc_id, "TASK_CANCELLED", "CANCELLED", {})
            return {"status": "cancelled", "stage": "cancelled", "context": _context(payload)}
        task_authority.update_task_record(task_id, "FAILED", "FAILED", error_message=str(exc))
        task_authority.append_task_event(
            task_id,
            tenant_id,
            kb_id,
            doc_id,
            "TASK_FAILED",
            "FAILED",
            {"error_message": str(exc)},
        )
        raise


@celery_app.task(name="app.tasks.repair_cross_store", bind=True, max_retries=8, ignore_result=True)
def repair_cross_store_task(self) -> dict:
    try:
        stale_after = max(30, min(int(os.getenv("CROSS_STORE_REPAIR_STALE_AFTER_SECONDS", "300")), 86400))
    except ValueError:
        stale_after = 300
    result = kb_store.repair_cross_store_operations(limit=50, stale_after_seconds=stale_after)
    if int(result.get("failed") or 0) > 0 or int(result.get("remaining") or 0) > 0:
        countdown = min(300, 2 ** min(int(self.request.retries or 0) + 1, 8))
        raise self.retry(exc=RuntimeError("cross-store repair remains pending"), countdown=countdown)
    return result
