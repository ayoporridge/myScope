import importlib
import json
import tempfile
import unittest
from datetime import datetime as RealDateTime
from pathlib import Path


class FrozenDateTime(RealDateTime):
    @classmethod
    def now(cls):
        return cls(2026, 7, 4, 6, 30, 0)


class HealthCheckTests(unittest.TestCase):
    def test_liveness_prefers_fresher_local_last_run(self):
        health_check = importlib.import_module("scripts.health_check")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            shared_status = root / "data" / "job_status"
            logs.mkdir()
            shared_status.mkdir(parents=True)
            (shared_status / "mini.json").write_text(json.dumps({
                "layer1_flomo": {
                    "status": "success",
                    "last_success_at": "2026-07-02T08:29:30",
                    "last_finished_at": "2026-07-02T08:29:30",
                }
            }))
            (logs / "last_run.json").write_text(json.dumps({
                "layer1_flomo": "2026-07-03T19:10:40",
            }))

            originals = {
                "LOCAL_HOSTNAME": health_check.LOCAL_HOSTNAME,
                "JOB_STATUS_SHARED_DIR": health_check.JOB_STATUS_SHARED_DIR,
                "JOB_STATUS_FILE": health_check.JOB_STATUS_FILE,
                "LAST_RUN_FILE": health_check.LAST_RUN_FILE,
                "MONITORED_SCRIPTS": health_check.MONITORED_SCRIPTS,
                "datetime": health_check.datetime,
            }
            health_check.LOCAL_HOSTNAME = "mini"
            health_check.JOB_STATUS_SHARED_DIR = shared_status
            health_check.JOB_STATUS_FILE = logs / "missing_job_status.json"
            health_check.LAST_RUN_FILE = logs / "last_run.json"
            health_check.MONITORED_SCRIPTS = ["layer1_flomo"]
            health_check.datetime = FrozenDateTime
            try:
                alerts = health_check.check_liveness()
            finally:
                for name, value in originals.items():
                    setattr(health_check, name, value)

            self.assertEqual([], alerts)


if __name__ == "__main__":
    unittest.main()
