import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from runtime import task_authority


class _Store:
    def __init__(self, task=None):
        self.task = task

    def get_task(self, task_id):
        return self.task


class TaskAuthorityBridgeTest(unittest.TestCase):
    def test_ensure_task_preserves_running_state_on_redelivery(self):
        store = _Store({"status": "RUNNING", "stage": "EMBEDDING", "state_owner": "CONTROL"})
        bridge = task_authority.TaskAuthorityBridge(store)

        with patch.dict(os.environ, {"TASK_AUTHORITY_CALLBACK_ENABLED": "true"}), patch.object(
            task_authority.control_plane_client, "append_task_event", return_value={"status": "ok"}
        ) as append_event:
            self.assertTrue(
                bridge.ensure_task(
                    "task-1", "tenant-a", "kb-a", "doc-a", "DOC_INGEST", "PARSING", filename="a.txt"
                )
            )

        event = append_event.call_args.args[1]
        self.assertEqual("RUNNING", event["status"])
        self.assertEqual("EMBEDDING", event["stage"])
        self.assertEqual("TASK_ENSURE", event["event_type"])

    def test_ensure_task_defaults_new_control_task_to_pending(self):
        bridge = task_authority.TaskAuthorityBridge(_Store())

        with patch.dict(os.environ, {"TASK_AUTHORITY_CALLBACK_ENABLED": "true"}), patch.object(
            task_authority.control_plane_client, "append_task_event", return_value={"status": "ok"}
        ) as append_event:
            bridge.ensure_task(
                "task-2", "tenant-a", "kb-a", "doc-a", "DOC_INGEST", "PARSING", filename="a.txt"
            )

        event = append_event.call_args.args[1]
        self.assertEqual("PENDING", event["status"])
        self.assertEqual("PARSING", event["stage"])

    def test_execution_owned_task_keeps_local_compatibility_path(self):
        store = _Store({"status": "PENDING", "stage": "QUEUED", "state_owner": "EXECUTION"})
        store.create_task_record = Mock(return_value=False)
        bridge = task_authority.TaskAuthorityBridge(store)

        with patch.dict(os.environ, {"TASK_AUTHORITY_CALLBACK_ENABLED": "true"}), patch.object(
            task_authority.control_plane_client, "append_task_event"
        ) as append_event:
            created = bridge.ensure_task(
                "legacy-task", "tenant-a", "kb-a", "doc-a", "DOC_TAG", "TAGGING", filename="a.txt"
            )

        self.assertFalse(created)
        store.create_task_record.assert_called_once()
        append_event.assert_not_called()

    def test_business_payload_status_does_not_mutate_authoritative_task_status(self):
        store = _Store({
            "tenant_id": "tenant-a",
            "kb_id": "kb-a",
            "doc_id": "doc-a",
            "task_type": "DOC_INGEST",
            "filename": "a.txt",
            "status": "SUCCEEDED",
            "stage": "INDEXED",
            "state_owner": "CONTROL",
        })
        bridge = task_authority.TaskAuthorityBridge(store)

        with patch.dict(os.environ, {"TASK_AUTHORITY_CALLBACK_ENABLED": "true"}), patch.object(
            task_authority.control_plane_client, "append_task_event", return_value={"status": "ok"}
        ) as append_event:
            bridge.append_task_event(
                "task-1", "tenant-a", "kb-a", "doc-a", "TASK_COMPLETED", "INDEXED", {"status": "INDEXED"}
            )

        event = append_event.call_args.args[1]
        self.assertNotIn("status", event)
        self.assertEqual("INDEXED", event["payload"]["status"])


if __name__ == "__main__":
    unittest.main()
