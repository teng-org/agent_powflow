import asyncio
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from starlette.datastructures import Headers


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from api.app import main as api_main


class ControlDispatchContractTest(unittest.TestCase):
    def test_internal_secret_resolution_requires_token_and_preserves_tenant_scope(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        resolved = SimpleNamespace(value="secret-value", secret_id="secret-a", fingerprint="fp-a", version=2)
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.workflow_secret_store, "resolve", return_value=resolved
        ) as resolve:
            result = asyncio.run(api_main.resolve_workflow_secret_internal(
                api_main.ApiInternalSecretResolveRequest(tenant_id="tenant-a", secret_ref="secret-a"), request,
            ))
        self.assertEqual("secret-value", result["value"])
        resolve.assert_called_once_with("tenant-a", "secret-a")

        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"):
            with self.assertRaises(api_main.HTTPException) as denied:
                asyncio.run(api_main.resolve_workflow_secret_internal(
                    api_main.ApiInternalSecretResolveRequest(tenant_id="tenant-a", secret_ref="secret-a"),
                    SimpleNamespace(headers={"X-Internal-Token": "wrong"}),
                ))
        self.assertEqual(401, denied.exception.status_code)

    def test_internal_document_metadata_mutations_use_exact_tenant_identity(self):
        request = SimpleNamespace(
            headers={"X-Internal-Token": "test-internal-token"},
            state=SimpleNamespace(trace_id="trace-metadata"),
        )
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "set_document_frozen", return_value=True
        ) as freeze_document, patch.object(
            api_main.kb_store, "get_document", return_value={"doc_id": "doc-a", "frozen": True}
        ), patch.object(
            api_main.kb_store, "set_chunk_frozen", return_value=True
        ) as freeze_chunk, patch.object(
            api_main.kb_store, "set_chunk_draft"
        ) as set_draft, patch.object(
            api_main.kb_store, "clear_chunk_draft", return_value=True
        ) as clear_draft:
            document_result = asyncio.run(api_main.freeze_document_internal(
                "tenant-a", "kb-a", "doc-a", api_main.ApiFreezeRequest(frozen=True), request,
            ))
            chunk_result = asyncio.run(api_main.freeze_chunk_internal(
                "tenant-a", "kb-a", "doc-a", "chunk-a", api_main.ApiFreezeRequest(frozen=True), request,
            ))
            draft_result = asyncio.run(api_main.upsert_chunk_draft_internal(
                "tenant-a", "kb-a", "doc-a", "chunk-a",
                api_main.ApiChunkDraftRequest(action="edit", text="revised text"), request,
            ))
            clear_result = asyncio.run(api_main.clear_chunk_draft_internal(
                "tenant-a", "kb-a", "doc-a", "chunk-a", request,
            ))

        self.assertEqual("ok", document_result["status"])
        self.assertEqual("ok", chunk_result["status"])
        self.assertEqual("edit", draft_result["action"])
        self.assertEqual("chunk-a", clear_result["deleted"])
        freeze_document.assert_called_once_with("tenant-a", "kb-a", "doc-a", True)
        freeze_chunk.assert_called_once_with("tenant-a", "kb-a", "doc-a", "chunk-a", True)
        set_draft.assert_called_once_with("tenant-a", "kb-a", "doc-a", "chunk-a", "edit", "revised text")
        clear_draft.assert_called_once_with("tenant-a", "kb-a", "doc-a", "chunk-a")

    def test_internal_draft_upsert_accepts_delete_action(self):
        request = SimpleNamespace(
            headers={"X-Internal-Token": "test-internal-token"},
            state=SimpleNamespace(trace_id="trace-metadata"),
        )
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"):
            with patch.object(api_main.kb_store, "set_chunk_draft") as set_draft:
                result = asyncio.run(api_main.upsert_chunk_draft_internal(
                    "tenant-a", "kb-a", "doc-a", "chunk-a",
                    api_main.ApiChunkDraftRequest(action="delete"), request,
                ))
        self.assertEqual("ok", result["status"])
        self.assertEqual("delete", result["action"])
        set_draft.assert_called_once_with("tenant-a", "kb-a", "doc-a", "chunk-a", "delete", None)

    def test_internal_multipart_stage_persists_artifacts_without_publishing_task(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        task = {
            "task_id": "task-upload",
            "tenant_id": "tenant-a",
            "kb_id": "kb-a",
            "doc_id": "doc-upload",
            "task_type": "DOC_INGEST",
            "status": "PENDING",
            "stage": "UPLOAD_PENDING",
            "state_owner": "CONTROL",
        }
        upload = api_main.UploadFile(
            file=io.BytesIO(b"hello multipart authority"),
            filename="sample.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        object_ref = {
            "object_bucket": "documents",
            "content_object_key": "extracted/key",
            "content_object_sha256": "a" * 64,
            "source_object_key": "source/key",
            "source_object_sha256": "b" * 64,
            "source_object_size_bytes": 25,
            "extracted_object_size_bytes": 25,
        }
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "ensure_kb"
        ), patch.object(api_main.kb_store, "get_task", return_value=task), patch.object(
            api_main.kb_store, "get_upload_settings", return_value={"max_file_size_mb": 50}
        ), patch.object(
            api_main, "_persist_document_artifacts", new=AsyncMock(return_value=object_ref)
        ) as persist, patch.object(api_main.kb_store, "set_document_status") as set_status, patch.object(
            api_main, "_send_persistent_task"
        ) as send_task:
            result = asyncio.run(api_main.stage_document_internal(
                "tenant-a", "kb-a", request,
                doc_id="doc-upload", task_id="task-upload",
                process_mode="UPLOAD_CLEAN_CHUNK_EMBED", chunk_method="sentence",
                chunk_delimiter=None, chunk_size=500, chunk_overlap=50, file=upload,
            ))

        self.assertEqual("staged", result["status"])
        self.assertEqual("extracted/key", result["content_object_key"])
        persist.assert_awaited_once()
        set_status.assert_called_once_with("tenant-a", "kb-a", "doc-upload", "UPLOADED_RAW")
        send_task.assert_not_called()

    def test_internal_non_index_upload_requires_matching_control_upload_task(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        task = {
            "task_id": "task-upload-only",
            "tenant_id": "tenant-a",
            "kb_id": "kb-a",
            "doc_id": "doc-upload-only",
            "task_type": "DOC_UPLOAD",
            "status": "PENDING",
            "stage": "UPLOAD_PENDING",
            "state_owner": "CONTROL",
        }
        upload = api_main.UploadFile(
            file=io.BytesIO(b"durable non-index upload"),
            filename="sample.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        object_ref = {
            "object_bucket": "documents",
            "content_object_key": "extracted/key",
            "content_object_sha256": "a" * 64,
            "source_object_key": "source/key",
            "source_object_sha256": "b" * 64,
            "source_object_size_bytes": 24,
            "extracted_object_size_bytes": 24,
        }
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "ensure_kb"
        ), patch.object(api_main.kb_store, "get_task", return_value=task), patch.object(
            api_main.kb_store, "get_upload_settings", return_value={"max_file_size_mb": 50}
        ), patch.object(
            api_main, "_persist_document_artifacts", new=AsyncMock(return_value=object_ref)
        ) as persist, patch.object(api_main.kb_store, "set_document_status") as set_status:
            result = asyncio.run(api_main.stage_document_internal(
                "tenant-a", "kb-a", request,
                doc_id="doc-upload-only", task_id="task-upload-only",
                process_mode="UPLOAD_ONLY", chunk_method="sentence",
                chunk_delimiter=None, chunk_size=500, chunk_overlap=50, file=upload,
            ))

        self.assertEqual("UPLOAD_ONLY", result["process_mode"])
        persist.assert_awaited_once()
        set_status.assert_called_once_with("tenant-a", "kb-a", "doc-upload-only", "UPLOADED_RAW")

    def test_internal_non_index_upload_rejects_index_task_identity(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        upload = api_main.UploadFile(
            file=io.BytesIO(b"wrong identity"), filename="sample.txt",
            headers=Headers({"content-type": "text/plain"}),
        )
        task = {
            "task_id": "task-wrong", "tenant_id": "tenant-a", "kb_id": "kb-a",
            "doc_id": "doc-a", "task_type": "DOC_INGEST", "status": "PENDING",
            "stage": "UPLOAD_PENDING", "state_owner": "CONTROL",
        }
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "ensure_kb"
        ), patch.object(api_main.kb_store, "get_task", return_value=task):
            with self.assertRaises(api_main.HTTPException) as raised:
                asyncio.run(api_main.stage_document_internal(
                    "tenant-a", "kb-a", request, doc_id="doc-a", task_id="task-wrong",
                    process_mode="UPLOAD_ONLY", chunk_method="sentence", chunk_delimiter=None,
                    chunk_size=500, chunk_overlap=50, file=upload,
                ))
        self.assertEqual(409, raised.exception.status_code)

    def test_control_owned_task_dispatch_stages_artifacts_and_preserves_task_identity(self):
        body = api_main.ApiInternalTaskDispatchRequest(
            task_id="task-1",
            task_type="DOC_INGEST",
            tenant_id="tenant-a",
            actor_id="admin@example.test",
            trace_id="trace-1",
            source_service="control-plane",
            kb_id="kb-a",
            doc_id="doc-a",
            filename="sample.txt",
            content="hello authority",
            chunk_size=500,
            chunk_overlap=50,
            auto_summary=False,
        )
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        task = {
            "task_id": "task-1",
            "tenant_id": "tenant-a",
            "kb_id": "kb-a",
            "doc_id": "doc-a",
            "task_type": "DOC_INGEST",
            "status": "PENDING",
            "state_owner": "CONTROL",
        }

        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "ensure_kb"
        ), patch.object(api_main.kb_store, "get_task", return_value=task), patch.object(
            api_main.kb_store, "acquire_task_dedup_lock", return_value={"acquired": True, "task_id": "task-1"}
        ), patch.object(
            api_main,
            "_persist_document_artifacts",
            new=AsyncMock(return_value={"source_object_key": "source/key", "text_object_key": "text/key"}),
        ), patch.object(api_main.kb_store, "set_document_status") as set_status, patch.object(
            api_main, "_send_persistent_task"
        ) as send_task:
            result = asyncio.run(api_main.dispatch_task_internal(body, request))

        self.assertEqual("accepted", result["status"])
        set_status.assert_called_once_with("tenant-a", "kb-a", "doc-a", "UPLOADED_RAW")
        sent = send_task.call_args.args
        self.assertEqual("app.tasks.parse_document", sent[0])
        self.assertEqual("task-1", sent[3])
        self.assertEqual("", sent[1]["content"])
        self.assertEqual("source/key", sent[1]["source_object_key"])

    def test_control_owned_object_dispatch_reuses_verified_staged_reference(self):
        body = api_main.ApiInternalTaskDispatchRequest(
            task_id="task-object",
            task_type="DOC_INGEST",
            tenant_id="tenant-a",
            actor_id="admin@example.test",
            trace_id="trace-object",
            source_service="control-plane",
            kb_id="kb-a",
            doc_id="doc-object",
            filename="sample.pdf",
            content="",
            chunk_method="sentence",
            chunk_size=500,
            chunk_overlap=50,
            object_bucket="documents",
            content_object_key="extracted/key",
            content_object_sha256="a" * 64,
            source_object_key="source/key",
            source_object_sha256="b" * 64,
            source_object_size_bytes=123,
            extracted_object_size_bytes=45,
        )
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        task = {
            "task_id": "task-object", "tenant_id": "tenant-a", "kb_id": "kb-a",
            "doc_id": "doc-object", "task_type": "DOC_INGEST", "status": "PENDING",
            "state_owner": "CONTROL",
        }
        document = {
            "filename": "sample.pdf",
            "source_object_bucket": "documents",
            "source_object_key": "source/key",
            "source_object_sha256": "b" * 64,
            "extracted_object_key": "extracted/key",
            "extracted_text_sha256": "a" * 64,
            "chunk_method": "sentence",
            "chunk_size": 500,
            "chunk_overlap": 50,
            "chunk_delimiter": "",
        }
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "ensure_kb"
        ), patch.object(api_main.kb_store, "get_task", return_value=task), patch.object(
            api_main.kb_store, "acquire_task_dedup_lock", return_value={"acquired": True, "task_id": "task-object"}
        ), patch.object(api_main.kb_store, "get_document", return_value=document), patch.object(
            api_main, "_persist_document_artifacts", new=AsyncMock()
        ) as persist, patch.object(api_main, "_send_persistent_task") as send_task:
            result = asyncio.run(api_main.dispatch_task_internal(body, request))

        self.assertEqual("accepted", result["status"])
        persist.assert_not_awaited()
        sent = send_task.call_args.args
        self.assertEqual("extracted/key", sent[1]["content_object_key"])
        self.assertEqual("sentence", sent[1]["chunk_method"])

    def test_control_owned_document_commands_dispatch_to_exact_worker_routes(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        document = {
            "filename": "sample.txt",
            "raw_text": "existing document content",
            "source_object_bucket": "",
            "extracted_object_key": "",
            "extracted_text_sha256": "",
        }
        cases = [
            ("DOC_SUMMARIZE", "app.tasks.summarize_document", "parse_queue", {}),
            ("DOC_CHUNK", "app.tasks.parse_document", "parse_queue", {"chunk_overlap": 0}),
            ("DOC_REINDEX", "app.tasks.parse_document", "parse_queue", {"chunk_method": "paragraph"}),
            ("CHUNK_COMMIT", "app.tasks.commit_chunk_drafts", "embed_queue", {}),
            ("DOC_MOVE", "app.tasks.move_document", "parse_queue", {"target_kb_id": "kb-b"}),
            ("DOC_TAG", "app.tasks.tag_document", "parse_queue", {"tags": ["reviewed"], "tag_mode": "replace"}),
            ("DOC_DELETE", "app.tasks.delete_document", "parse_queue", {}),
        ]
        for task_type, worker_name, queue, overrides in cases:
            with self.subTest(task_type=task_type):
                values = {
                    "task_id": f"task-{task_type.lower()}",
                    "task_type": task_type,
                    "tenant_id": "tenant-a",
                    "actor_id": "admin@example.test",
                    "trace_id": f"trace-{task_type.lower()}",
                    "source_service": "control-plane",
                    "kb_id": "kb-a",
                    "doc_id": "doc-a",
                    "chunk_method": "sentence",
                    "chunk_size": 100,
                    "chunk_overlap": 10,
                    **overrides,
                }
                body = api_main.ApiInternalTaskDispatchRequest(**values)
                task = {
                    "task_id": body.task_id, "tenant_id": "tenant-a", "kb_id": "kb-a",
                    "doc_id": "doc-a", "task_type": task_type, "status": "PENDING",
                    "state_owner": "CONTROL", "filename": "",
                }
                with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
                    api_main.kb_store, "get_task", return_value=task
                ), patch.object(api_main.kb_store, "get_kb", return_value={"name": "KB"}), patch.object(
                    api_main.kb_store, "get_document", return_value=document
                ), patch.object(
                    api_main.kb_store, "acquire_task_dedup_lock", return_value={"acquired": True, "task_id": body.task_id}
                ), patch.object(api_main.kb_store, "set_document_chunk_config") as set_config, patch.object(
                    api_main, "_send_persistent_task"
                ) as send_task:
                    result = asyncio.run(api_main.dispatch_task_internal(body, request))

                self.assertEqual("accepted", result["status"])
                sent = send_task.call_args.args
                self.assertEqual(worker_name, sent[0])
                self.assertEqual(queue, sent[2])
                self.assertEqual(body.task_id, sent[3])
                self.assertEqual("sample.txt", sent[1]["filename"])
                if task_type == "DOC_CHUNK":
                    self.assertEqual(0, sent[1]["chunk_overlap"])
                    set_config.assert_called_once()
                if task_type == "DOC_TAG":
                    self.assertEqual("replace", sent[1]["tag_mode"])

    def test_retried_move_dispatch_accepts_repaired_document_in_target(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        body = api_main.ApiInternalTaskDispatchRequest(
            task_id="task-doc-move-retry",
            task_type="DOC_MOVE",
            tenant_id="tenant-a",
            actor_id="admin@example.test",
            trace_id="trace-doc-move-retry",
            source_service="control-plane",
            kb_id="kb-source",
            doc_id="doc-a",
            target_kb_id="kb-target",
        )
        task = {
            "task_id": body.task_id,
            "tenant_id": body.tenant_id,
            "kb_id": body.kb_id,
            "doc_id": body.doc_id,
            "task_type": body.task_type,
            "status": "PENDING",
            "state_owner": "CONTROL",
            "filename": "",
        }
        target_document = {"filename": "already-moved.txt", "status": "INDEXED"}
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "get_task", return_value=task
        ), patch.object(api_main.kb_store, "get_kb", return_value={"name": "KB"}), patch.object(
            api_main.kb_store, "get_document", side_effect=[None, target_document]
        ), patch.object(
            api_main.kb_store, "find_document_move_repair", return_value={"repair_id": 42, "status": "SUCCEEDED"}
        ), patch.object(
            api_main.kb_store, "acquire_task_dedup_lock", return_value={"acquired": True, "task_id": body.task_id}
        ), patch.object(api_main, "_send_persistent_task") as send_task:
            result = asyncio.run(api_main.dispatch_task_internal(body, request))

        self.assertEqual("accepted", result["status"])
        self.assertEqual("app.tasks.move_document", send_task.call_args.args[0])
        self.assertEqual("already-moved.txt", send_task.call_args.args[1]["filename"])
        self.assertEqual("kb-target", send_task.call_args.args[1]["target_kb_id"])

    def test_control_owned_kb_summary_has_empty_document_identity(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        body = api_main.ApiInternalTaskDispatchRequest(
            task_id="task-kb-summary", task_type="KB_SUMMARIZE", tenant_id="tenant-a",
            actor_id="admin@example.test", trace_id="trace-summary", source_service="control-plane",
            kb_id="kb-a", doc_id="",
        )
        task = {
            "task_id": body.task_id, "tenant_id": "tenant-a", "kb_id": "kb-a", "doc_id": "",
            "task_type": "KB_SUMMARIZE", "status": "PENDING", "state_owner": "CONTROL",
            "filename": "Control KB",
        }
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "get_task", return_value=task
        ), patch.object(api_main.kb_store, "get_kb", return_value={"name": "Execution KB"}), patch.object(
            api_main.kb_store, "acquire_task_dedup_lock", return_value={"acquired": True, "task_id": body.task_id}
        ), patch.object(api_main, "_send_persistent_task") as send_task:
            result = asyncio.run(api_main.dispatch_task_internal(body, request))

        self.assertEqual("accepted", result["status"])
        sent = send_task.call_args.args
        self.assertEqual("app.tasks.summarize_kb", sent[0])
        self.assertEqual("Control KB", sent[1]["kb_name"])
        self.assertEqual("", sent[1]["doc_id"])

    def test_control_owned_batch_child_preserves_batch_identity_in_worker_payload(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        body = api_main.ApiInternalTaskDispatchRequest(
            task_id="task-batch-tag", task_type="DOC_TAG", tenant_id="tenant-a",
            actor_id="admin@example.test", trace_id="trace-batch-tag", source_service="control-plane",
            kb_id="kb-a", doc_id="doc-a", tags=["reviewed"], tag_mode="replace",
            batch_id="batch-a", batch_name="review batch",
        )
        task = {
            "task_id": body.task_id, "tenant_id": "tenant-a", "kb_id": "kb-a",
            "doc_id": "doc-a", "task_type": "DOC_TAG", "status": "PENDING",
            "state_owner": "CONTROL", "filename": "", "batch_id": "batch-a",
            "batch_name": "review batch", "batch_is_parent": False,
        }
        document = {"filename": "sample.txt", "raw_text": "content"}
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "get_task", return_value=task
        ), patch.object(api_main.kb_store, "get_kb", return_value={"name": "KB"}
        ), patch.object(api_main.kb_store, "get_document", return_value=document), patch.object(
            api_main.kb_store, "acquire_task_dedup_lock", return_value={"acquired": True, "task_id": body.task_id}
        ), patch.object(api_main, "_send_persistent_task") as send_task:
            result = asyncio.run(api_main.dispatch_task_internal(body, request))

        self.assertEqual("accepted", result["status"])
        payload = send_task.call_args.args[1]
        self.assertEqual("batch-a", payload["batch_id"])
        self.assertEqual("review batch", payload["batch_name"])

    def test_control_owned_batch_dispatch_rejects_mismatched_or_parent_identity(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        body = api_main.ApiInternalTaskDispatchRequest(
            task_id="task-batch-mismatch", task_type="DOC_DELETE", tenant_id="tenant-a",
            actor_id="admin@example.test", trace_id="trace-batch-delete", source_service="control-plane",
            kb_id="kb-a", doc_id="doc-a", batch_id="batch-request", batch_name="requested",
        )
        base_task = {
            "task_id": body.task_id, "tenant_id": "tenant-a", "kb_id": "kb-a",
            "doc_id": "doc-a", "task_type": "DOC_DELETE", "status": "PENDING",
            "state_owner": "CONTROL", "batch_id": "batch-stored", "batch_name": "stored",
            "batch_is_parent": False,
        }
        for overrides in ({}, {"batch_id": "batch-request", "batch_name": "requested", "batch_is_parent": True}):
            with self.subTest(overrides=overrides), patch.object(
                api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"
            ), patch.object(api_main.kb_store, "get_task", return_value={**base_task, **overrides}), patch.object(
                api_main, "_send_persistent_task"
            ) as send_task:
                with self.assertRaises(api_main.HTTPException) as raised:
                    asyncio.run(api_main.dispatch_task_internal(body, request))
                self.assertEqual(409, raised.exception.status_code)
                self.assertEqual("task immutable batch identity mismatch", raised.exception.detail)
                send_task.assert_not_called()

    def test_internal_document_describe_is_exact_ordered_and_reports_missing(self):
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})
        body = api_main.ApiInternalDocumentDescribeRequest(
            doc_ids=["doc-b", "doc-missing", "doc-a", "doc-b"]
        )
        described = [
            {"doc_id": "doc-a", "filename": "a.txt", "chunk_count": 2, "status": "READY"},
            {"doc_id": "doc-b", "filename": "b.txt", "chunk_count": 0, "status": "UPLOADED_RAW"},
        ]
        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.kb_store, "get_kb", return_value={"kb_id": "kb-a"}
        ), patch.object(api_main.kb_store, "describe_documents", return_value=described) as describe:
            result = asyncio.run(api_main.describe_documents_internal("tenant-a", "kb-a", body, request))

        describe.assert_called_once_with("tenant-a", "kb-a", ["doc-b", "doc-missing", "doc-a"])
        self.assertEqual(["doc-b", "doc-a"], [item["doc_id"] for item in result["items"]])
        self.assertEqual(["doc-missing"], result["missing_doc_ids"])
        self.assertNotIn("raw_text", result["items"][0])

    def test_approval_resume_survives_validation_and_gets_a_distinct_task_id(self):
        checkpoint = {
            "version": 1,
            "node_id": "approval",
            "state": {"answer": "draft"},
            "executed_node_ids": ["start", "draft"],
        }
        body = api_main.ApiControlRunDispatchRequest(
            run_id="run-1",
            tenant_id="tenant-a",
            actor_id="admin@example.test",
            conversation_id="conversation-1",
            status="PENDING",
            workflow_id="workflow-1",
            workflow_version_id="version-1",
            workflow_version_no=1,
            workflow_checksum="a" * 64,
            workflow_dsl={"nodes": []},
            workflow_layout={},
            input_payload={"text": "hello"},
            approval_resume={
                "approval_id": "approval-1234567890",
                "node_id": "approval",
                "decision": "approve",
                "checkpoint": checkpoint,
            },
        )
        request = SimpleNamespace(headers={"X-Internal-Token": "test-internal-token"})

        with patch.object(api_main, "CONTROL_PLANE_INTERNAL_TOKEN", "test-internal-token"), patch.object(
            api_main.celery_app, "send_task"
        ) as send_task:
            result = asyncio.run(api_main.dispatch_control_run(body, request))

        sent_payload = send_task.call_args.kwargs["args"][0]
        self.assertEqual(checkpoint, sent_payload["approval_resume"]["checkpoint"])
        self.assertEqual("control-run-run-1-approval-approval-123", result["task_id"])


if __name__ == "__main__":
    unittest.main()
