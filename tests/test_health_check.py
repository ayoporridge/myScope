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

    def test_quality_alerts_on_llm_errors(self):
        health_check = importlib.import_module("scripts.health_check")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_metrics = root / "data" / "metrics"
            logs = root / "logs"
            shared_metrics.mkdir(parents=True)
            logs.mkdir(parents=True)
            today = RealDateTime.now().strftime("%Y-%m-%d")
            (shared_metrics / "jodeMacBook-Air.jsonl").write_text(json.dumps({
                "date": today,
                "script": "layer2_wiki",
                "timestamp": f"{today}T05:30:00",
                "hostname": "jodeMacBook-Air",
                "llm_errors": 1,
                "llm_error_summary": "Insufficient Balance",
                "wiki_entries_written": 0,
            }) + "\n")

            originals = {
                "METRICS_SHARED_DIR": health_check.METRICS_SHARED_DIR,
                "METRICS_FILE": health_check.METRICS_FILE,
            }
            health_check.METRICS_SHARED_DIR = shared_metrics
            health_check.METRICS_FILE = logs / "missing_metrics.jsonl"
            try:
                alerts = health_check.check_quality()
            finally:
                for name, value in originals.items():
                    setattr(health_check, name, value)

        self.assertTrue(any("LLM 调用失败" in alert for alert in alerts))
        self.assertTrue(any("Insufficient Balance" in alert for alert in alerts))

    def test_quality_aggregates_layer1_sources_before_chunk_alert(self):
        health_check = importlib.import_module("scripts.health_check")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_metrics = root / "data" / "metrics"
            logs = root / "logs"
            shared_metrics.mkdir(parents=True)
            logs.mkdir(parents=True)
            today = RealDateTime.now().strftime("%Y-%m-%d")
            (shared_metrics / "layer1.jsonl").write_text(
                json.dumps({
                    "date": today,
                    "script": "layer1_rag",
                    "timestamp": f"{today}T05:00:00",
                    "hostname": "jodeMacBook-Air",
                    "raw_texts": 1,
                    "chunks_produced": 0,
                }) + "\n" +
                json.dumps({
                    "date": today,
                    "script": "layer1_flomo",
                    "timestamp": f"{today}T19:10:00",
                    "hostname": "xizhouMINIdeMac-mini",
                    "memos": 1,
                    "chunks": 15,
                }) + "\n"
            )

            originals = {
                "METRICS_SHARED_DIR": health_check.METRICS_SHARED_DIR,
                "METRICS_FILE": health_check.METRICS_FILE,
            }
            health_check.METRICS_SHARED_DIR = shared_metrics
            health_check.METRICS_FILE = logs / "missing_metrics.jsonl"
            try:
                alerts = health_check.check_quality()
            finally:
                for name, value in originals.items():
                    setattr(health_check, name, value)

        self.assertFalse(any("新增切片仅" in alert for alert in alerts))

    def test_quality_alerts_on_flomo_collect_errors(self):
        health_check = importlib.import_module("scripts.health_check")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_metrics = root / "data" / "metrics"
            logs = root / "logs"
            shared_metrics.mkdir(parents=True)
            logs.mkdir(parents=True)
            today = RealDateTime.now().strftime("%Y-%m-%d")
            (shared_metrics / "xizhouMINIdeMac-mini.jsonl").write_text(json.dumps({
                "date": today,
                "script": "layer1_flomo",
                "timestamp": f"{today}T19:10:00",
                "hostname": "xizhouMINIdeMac-mini",
                "memos": 0,
                "chunks": 0,
                "collect_errors": 1,
                "collect_error_summary": "opencli open failed",
            }) + "\n")

            originals = {
                "METRICS_SHARED_DIR": health_check.METRICS_SHARED_DIR,
                "METRICS_FILE": health_check.METRICS_FILE,
            }
            health_check.METRICS_SHARED_DIR = shared_metrics
            health_check.METRICS_FILE = logs / "missing_metrics.jsonl"
            try:
                alerts = health_check.check_quality()
            finally:
                for name, value in originals.items():
                    setattr(health_check, name, value)

        self.assertTrue(any("layer1_flomo" in alert and "采集失败" in alert for alert in alerts))
        self.assertTrue(any("opencli open failed" in alert for alert in alerts))


if __name__ == "__main__":
    unittest.main()
