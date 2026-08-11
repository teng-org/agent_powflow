import pathlib
import sys
import unittest
from unittest.mock import Mock


SERVICE_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from runtime.control_run_dead_letter import publish_control_run_dead_letter


class ControlRunDeadLetterTest(unittest.TestCase):
    def test_final_task_failure_publishes_durable_dead_letter(self) -> None:
        celery_app = Mock()
        publish_control_run_dead_letter(
            celery_app,
            RuntimeError("callback retry exhausted"),
            "task-1",
            ({"run_id": "run-1", "tenant_id": "tenant-a"},),
        )

        _, kwargs = celery_app.send_task.call_args
        self.assertEqual("control_run_dead_letter", kwargs["queue"])
        self.assertEqual("control_run_dead_letter", kwargs["exchange"])
        self.assertEqual("control_run.dead", kwargs["routing_key"])
        self.assertTrue(kwargs["ignore_result"])


if __name__ == "__main__":
    unittest.main()
