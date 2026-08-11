from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from celery.exceptions import Retry


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from workers.app import tasks as worker_tasks


class DocumentCommandWorkerAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.authority = MagicMock()
        self.store = MagicMock()
        self.store.is_cancel_requested.return_value = False

    def test_summary_uses_object_content_and_control_callbacks(self):
        payload = {
            "task_id": "task-summary", "task_type": "DOC_SUMMARIZE", "tenant_id": "tenant-a",
            "kb_id": "kb-a", "doc_id": "doc-a", "filename": "sample.txt",
            "object_bucket": "documents", "content_object_key": "text/key",
            "content_object_sha256": "a" * 64,
        }
        result_value = {"doc_summary": "summary"}
        with patch.object(worker_tasks, "kb_store", self.store), patch.object(
            worker_tasks, "task_authority", self.authority
        ), patch.object(worker_tasks, "_begin_persistent_task", return_value=None), patch.object(
            worker_tasks, "_payload_content", return_value="object text"
        ) as content, patch.object(
            worker_tasks, "run_document_and_kb_summary", return_value=result_value
        ) as summarize:
            result = worker_tasks.summarize_document_task.run(payload)

        self.assertEqual("ok", result["status"])
        content.assert_called_once_with(payload)
        self.assertEqual("object text", summarize.call_args.kwargs["content"])
        self.authority.update_task_record.assert_any_call("task-summary", "SUCCEEDED", "SUMMARIZED")
        self.authority.append_task_event.assert_any_call(
            "task-summary", "tenant-a", "kb-a", "doc-a", "TASK_COMPLETED", "SUMMARIZED", result_value
        )

    def test_transient_provider_timeout_schedules_retry_without_terminal_failure(self):
        task = MagicMock()
        task.request.retries = 0
        task.max_retries = 2
        task.retry.side_effect = Retry("scheduled")
        failure = RuntimeError("provider: The read operation timed out")
        with patch.object(worker_tasks, "task_authority", self.authority):
            with self.assertRaises(Retry):
                worker_tasks._retry_provider_task(
                    task, failure, "task-summary", "tenant-a", "kb-a", "doc-a", "SUMMARIZING_DOC"
                )

        task.retry.assert_called_once_with(exc=failure, countdown=1)
        self.authority.append_task_event.assert_called_once()
        self.assertEqual("TASK_RETRY_SCHEDULED", self.authority.append_task_event.call_args.args[4])

    def test_replace_tags_use_control_callbacks(self):
        payload = {
            "task_id": "task-tag", "task_type": "DOC_TAG", "tenant_id": "tenant-a",
            "kb_id": "kb-a", "doc_id": "doc-a", "filename": "sample.txt",
            "tags": ["reviewed", "reviewed"], "tag_mode": "replace",
        }
        self.store.set_document_tags.return_value = True
        with patch.object(worker_tasks, "kb_store", self.store), patch.object(
            worker_tasks, "task_authority", self.authority
        ), patch.object(worker_tasks, "_begin_persistent_task", return_value=None):
            result = worker_tasks.tag_document_task.run(payload)

        self.assertEqual("replace", result["result"]["mode"])
        self.store.set_document_tags.assert_called_once_with("tenant-a", "kb-a", "doc-a", ["reviewed"])
        self.store.add_document_tags.assert_not_called()
        self.authority.update_task_record.assert_any_call("task-tag", "SUCCEEDED", "TAGGED")

    def test_delete_completion_is_reported_to_control_after_cross_store_delete(self):
        payload = {
            "task_id": "task-delete", "task_type": "DOC_DELETE", "tenant_id": "tenant-a",
            "kb_id": "kb-a", "doc_id": "doc-a", "filename": "sample.txt",
        }
        self.store.find_active_doc_task_ids.return_value = ["task-delete"]
        self.store.delete_document.return_value = True
        with patch.object(worker_tasks, "kb_store", self.store), patch.object(
            worker_tasks, "task_authority", self.authority
        ), patch.object(worker_tasks, "_begin_persistent_task", return_value=None), patch.object(
            worker_tasks.repair_cross_store_task, "apply_async"
        ):
            result = worker_tasks.delete_document_task.run(payload)

        self.assertEqual("ok", result["status"])
        self.store.delete_document.assert_called_once_with("tenant-a", "kb-a", "doc-a")
        self.authority.update_task_record.assert_any_call("task-delete", "SUCCEEDED", "DELETED")
        self.authority.append_task_event.assert_any_call(
            "task-delete", "tenant-a", "kb-a", "doc-a", "TASK_COMPLETED", "DELETED", {}
        )


if __name__ == "__main__":
    unittest.main()
