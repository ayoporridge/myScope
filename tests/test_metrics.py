import importlib
import json
import tempfile
import unittest
from pathlib import Path


class MetricsTests(unittest.TestCase):
    def test_record_job_result_tracks_success_and_failure_separately(self):
        metrics = importlib.import_module("scripts._metrics")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            metrics.LOGS_DIR = tmp_path / "logs"
            metrics.LAST_RUN_FILE = metrics.LOGS_DIR / "last_run.json"
            metrics.METRICS_FILE = metrics.LOGS_DIR / "metrics.jsonl"
            metrics.JOB_STATUS_FILE = metrics.LOGS_DIR / "job_status.json"
            metrics.JOB_EVENTS_FILE = metrics.LOGS_DIR / "job_events.jsonl"
            metrics.METRICS_SHARED_DIR = tmp_path / "data" / "metrics"
            metrics.METRICS_SHARED_FILE = metrics.METRICS_SHARED_DIR / "test-host.jsonl"
            metrics.JOB_STATUS_SHARED_DIR = tmp_path / "data" / "job_status"
            metrics.JOB_STATUS_SHARED_FILE = metrics.JOB_STATUS_SHARED_DIR / "test-host.json"
            metrics.HOSTNAME = "test-host"

            metrics.record_job_result("demo", "failure", exit_code=1, error_summary="boom")
            metrics.record_job_result("demo", "success", exit_code=0, output_count=3)

            status = json.loads(metrics.JOB_STATUS_FILE.read_text())
            demo = status["test-host"]["demo"]

            self.assertEqual("success", demo["status"])
            self.assertNotIn("last_error_summary", demo)
            self.assertIn("last_failure_at", demo)
            self.assertEqual(3, demo["last_output_count"])
            self.assertIn("demo", json.loads(metrics.LAST_RUN_FILE.read_text()))
            self.assertIn("demo", json.loads(metrics.JOB_STATUS_SHARED_FILE.read_text()))
            self.assertEqual(2, len(metrics.JOB_EVENTS_FILE.read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()
