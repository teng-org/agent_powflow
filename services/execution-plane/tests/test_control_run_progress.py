import sys
import unittest
from pathlib import Path


EXECUTION_ROOT = Path(__file__).resolve().parents[1]
if str(EXECUTION_ROOT) not in sys.path:
    sys.path.insert(0, str(EXECUTION_ROOT))

from workers.app.tasks import _control_run_start_progress


class ControlRunProgressTest(unittest.TestCase):
    def test_new_run_starts_at_ten_percent(self):
        self.assertEqual(10, _control_run_start_progress({}))

    def test_approval_resume_preserves_checkpoint_progress(self):
        payload = {"approval_resume": {"checkpoint": {"progress": 44}}}
        self.assertEqual(44, _control_run_start_progress(payload))

    def test_invalid_or_out_of_range_checkpoint_progress_is_safe(self):
        self.assertEqual(10, _control_run_start_progress({"approval_resume": {"checkpoint": {"progress": "bad"}}}))
        self.assertEqual(99, _control_run_start_progress({"approval_resume": {"checkpoint": {"progress": 1000}}}))


if __name__ == "__main__":
    unittest.main()
