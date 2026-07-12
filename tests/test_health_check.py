import importlib
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


class HealthCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.health = importlib.import_module("scripts.health_check")

    def setUp(self):
        self.originals = {
            "LAST_RUN_FILE": self.health.LAST_RUN_FILE,
            "METRICS_FILE": self.health.METRICS_FILE,
            "METRICS_SHARED_DIR": self.health.METRICS_SHARED_DIR,
            "JOB_STATUS_FILE": self.health.JOB_STATUS_FILE,
            "JOB_STATUS_SHARED_DIR": self.health.JOB_STATUS_SHARED_DIR,
            "MONITORED_SCRIPTS": list(self.health.MONITORED_SCRIPTS),
            "LOCAL_HOSTNAME": self.health.LOCAL_HOSTNAME,
        }

    def tearDown(self):
        for key, value in self.originals.items():
            setattr(self.health, key, value)

    def _quality_alerts_for(self, metrics):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_metrics = root / "data" / "metrics"
            logs = root / "logs"
            shared_metrics.mkdir(parents=True)
            logs.mkdir(parents=True)
            (shared_metrics / "metrics.jsonl").write_text(
                "".join(json.dumps(metric, ensure_ascii=False) + "\n" for metric in metrics)
            )
            self.health.METRICS_SHARED_DIR = shared_metrics
            self.health.METRICS_FILE = logs / "missing_metrics.jsonl"
            return self.health.check_quality()

    def test_liveness_checks_local_last_run_when_shared_status_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            shared_status = root / "data" / "job_status"
            logs.mkdir(parents=True)
            shared_status.mkdir(parents=True)
            (shared_status / "jodeMacBook-Air.json").write_text(json.dumps({
                "hippocampus_formation": {
                    "status": "success",
                    "last_success_at": "2999-01-01T00:00:00",
                }
            }))
            (logs / "last_run.json").write_text(json.dumps({
                "hippocampus_formation": "2999-01-01T00:00:00",
                "layer1_flomo": "2000-01-01T00:00:00",
            }))

            self.health.LOCAL_HOSTNAME = "xizhouMINIdeMac-mini"
            self.health.MONITORED_SCRIPTS = ["hippocampus_formation", "layer1_flomo"]
            self.health.JOB_STATUS_SHARED_DIR = shared_status
            self.health.JOB_STATUS_FILE = logs / "missing_job_status.json"
            self.health.LAST_RUN_FILE = logs / "last_run.json"

            alerts = self.health.check_liveness()

        self.assertTrue(any("layer1_flomo" in alert for alert in alerts))
        self.assertTrue(any("xizhouMINIdeMac-mini" in alert for alert in alerts))

    def test_liveness_uses_newer_local_last_run_over_stale_shared_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            shared_status = root / "data" / "job_status"
            logs.mkdir(parents=True)
            shared_status.mkdir(parents=True)
            (shared_status / "xizhouMINIdeMac-mini.json").write_text(json.dumps({
                "layer2_wiki": {
                    "status": "success",
                    "last_success_at": "2000-01-01T00:00:00",
                }
            }))
            (logs / "last_run.json").write_text(json.dumps({
                "layer2_wiki": "2999-01-01T00:00:00",
            }))

            self.health.LOCAL_HOSTNAME = "xizhouMINIdeMac-mini"
            self.health.MONITORED_SCRIPTS = ["layer2_wiki"]
            self.health.JOB_STATUS_SHARED_DIR = shared_status
            self.health.JOB_STATUS_FILE = logs / "missing_job_status.json"
            self.health.LAST_RUN_FILE = logs / "last_run.json"

            alerts = self.health.check_liveness()

        self.assertFalse(any("layer2_wiki" in alert for alert in alerts))

    def test_quality_alerts_on_llm_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_metrics = root / "data" / "metrics"
            logs = root / "logs"
            shared_metrics.mkdir(parents=True)
            logs.mkdir(parents=True)
            today = datetime.now().strftime("%Y-%m-%d")
            (shared_metrics / "jodeMacBook-Air.jsonl").write_text(json.dumps({
                "date": today,
                "script": "layer2_wiki",
                "timestamp": f"{today}T05:30:00",
                "hostname": "jodeMacBook-Air",
                "llm_errors": 1,
                "llm_error_summary": "Insufficient Balance",
                "wiki_entries_written": 0,
            }) + "\n")

            self.health.METRICS_SHARED_DIR = shared_metrics
            self.health.METRICS_FILE = logs / "missing_metrics.jsonl"

            alerts = self.health.check_quality()

        self.assertTrue(any("LLM 调用失败" in alert for alert in alerts))
        self.assertTrue(any("Insufficient Balance" in alert for alert in alerts))

    def test_quality_ignores_single_rag_input_without_output_when_flomo_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_metrics = root / "data" / "metrics"
            logs = root / "logs"
            shared_metrics.mkdir(parents=True)
            logs.mkdir(parents=True)
            today = datetime.now().strftime("%Y-%m-%d")
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
                    "timestamp": f"{today}T04:30:00",
                    "hostname": "xizhouMINIdeMac-mini",
                    "memos": 1,
                    "chunks": 15,
                }) + "\n"
            )

            self.health.METRICS_SHARED_DIR = shared_metrics
            self.health.METRICS_FILE = logs / "missing_metrics.jsonl"

            alerts = self.health.check_quality()

        self.assertFalse(any(
            "layer1_rag" in alert and "写入 0" in alert
            for alert in alerts
        ))

    def test_quality_alerts_on_multiple_rag_inputs_without_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_metrics = root / "data" / "metrics"
            logs = root / "logs"
            shared_metrics.mkdir(parents=True)
            logs.mkdir(parents=True)
            today = datetime.now().strftime("%Y-%m-%d")
            (shared_metrics / "layer1.jsonl").write_text(json.dumps({
                "date": today,
                "script": "layer1_rag",
                "timestamp": f"{today}T05:00:00",
                "hostname": "jodeMacBook-Air",
                "raw_texts": 3,
                "chunks_produced": 0,
            }) + "\n")

            self.health.METRICS_SHARED_DIR = shared_metrics
            self.health.METRICS_FILE = logs / "missing_metrics.jsonl"

            alerts = self.health.check_quality()

        self.assertTrue(any(
            "layer1_rag" in alert and "写入 0" in alert
            for alert in alerts
        ))

    def test_quality_ignores_recovered_llm_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_metrics = root / "data" / "metrics"
            logs = root / "logs"
            shared_metrics.mkdir(parents=True)
            logs.mkdir(parents=True)
            today = datetime.now().strftime("%Y-%m-%d")
            (shared_metrics / "wiki.jsonl").write_text(
                json.dumps({
                    "date": today,
                    "script": "layer2_wiki",
                    "timestamp": f"{today}T03:48:00",
                    "hostname": "jodeMacBook-Air",
                    "llm_errors": 1,
                    "llm_error_summary": "Expecting ',' delimiter",
                    "wiki_entries_written": 0,
                }) + "\n" +
                json.dumps({
                    "date": today,
                    "script": "layer2_wiki",
                    "timestamp": f"{today}T03:54:00",
                    "hostname": "jodeMacBook-Air",
                    "llm_errors": 0,
                    "llm_error_summary": "",
                    "wiki_entries_written": 4,
                }) + "\n"
            )

            self.health.METRICS_SHARED_DIR = shared_metrics
            self.health.METRICS_FILE = logs / "missing_metrics.jsonl"

            alerts = self.health.check_quality()

        self.assertFalse(any("LLM 调用失败" in alert for alert in alerts))

    def test_quality_alerts_on_flomo_collect_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_metrics = root / "data" / "metrics"
            logs = root / "logs"
            shared_metrics.mkdir(parents=True)
            logs.mkdir(parents=True)
            today = datetime.now().strftime("%Y-%m-%d")
            (shared_metrics / "xizhouMINIdeMac-mini.jsonl").write_text(json.dumps({
                "date": today,
                "script": "layer1_flomo",
                "timestamp": f"{today}T04:30:00",
                "hostname": "xizhouMINIdeMac-mini",
                "memos": 0,
                "chunks": 0,
                "collect_errors": 1,
                "collect_error_summary": "opencli open failed",
            }) + "\n")

            self.health.METRICS_SHARED_DIR = shared_metrics
            self.health.METRICS_FILE = logs / "missing_metrics.jsonl"

            alerts = self.health.check_quality()

        self.assertTrue(any("layer1_flomo" in alert and "采集失败" in alert for alert in alerts))
        self.assertTrue(any("opencli open failed" in alert for alert in alerts))

    def test_quality_allows_successful_flomo_run_with_no_new_memos(self):
        today = datetime.now().strftime("%Y-%m-%d")
        alerts = self._quality_alerts_for([{
            "date": today,
            "script": "layer1_flomo",
            "timestamp": f"{today}T19:10:00",
            "hostname": "mini",
            "new_memos": 0,
            "documents_written": 0,
            "chunks": 0,
            "collect_errors": 0,
            "ingest_errors": 0,
        }])

        self.assertFalse(any("layer1" in alert for alert in alerts))

    def test_quality_alerts_when_new_flomo_memos_write_no_documents(self):
        today = datetime.now().strftime("%Y-%m-%d")
        alerts = self._quality_alerts_for([{
            "date": today,
            "script": "layer1_flomo",
            "timestamp": f"{today}T19:10:00",
            "hostname": "mini",
            "new_memos": 2,
            "documents_written": 0,
            "chunks": 0,
            "collect_errors": 0,
            "ingest_errors": 0,
        }])

        self.assertTrue(any(
            "layer1_flomo" in alert and "写入 0" in alert
            for alert in alerts
        ))


if __name__ == "__main__":
    unittest.main()
