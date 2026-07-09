import importlib
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch


class RunDueJobsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = importlib.import_module("scripts.run_due_jobs")

    def test_job_is_due_when_last_success_missing_or_stale(self):
        now = datetime(2026, 6, 24, 14, 0, 0)

        self.assertTrue(self.runner.is_due(None, 24, now=now))
        self.assertTrue(self.runner.is_due("2026-06-23T13:00:00", 24, now=now))
        self.assertFalse(self.runner.is_due("2026-06-24T13:30:00", 24, now=now))

    def test_plan_due_jobs_keeps_layer2_after_collectors(self):
        now = datetime(2026, 6, 24, 19, 30, 0)
        status = {
            "jodeMacBook-Air": {
                "dayflow_sync": {"last_success_at": "2026-06-21T20:47:09"},
                "layer3_wechat": {"last_success_at": "2026-06-21T20:55:39"},
                "layer1_rag": {"last_success_at": "2026-06-21T22:02:37"},
                "hippocampus_formation": {"last_success_at": "2026-06-21T06:03:08"},
                "dayflow_daily_summary": {"last_success_at": "2026-06-21T06:37:21"},
                "layer2_wiki": {"last_success_at": "2026-06-21T22:03:09"},
            }
        }

        planned = self.runner.plan_due_jobs("macbook", status, hostname="jodeMacBook-Air", now=now)
        names = [job.name for job in planned]

        self.assertEqual("layer2_wiki", names[-1])
        self.assertIn("dayflow_daily_summary", names)
        self.assertIn("dayflow_sync", names)

    def test_deepseek_jobs_only_run_in_night_window(self):
        status = {
            "jodeMacBook-Air": {
                "dayflow_sync": {"last_success_at": "2026-06-20T20:47:09"},
                "layer1_rag": {"last_success_at": "2026-06-20T22:02:37"},
                "layer2_wiki": {"last_success_at": "2026-06-20T22:03:09"},
            }
        }

        daytime = self.runner.plan_due_jobs(
            "macbook",
            status,
            hostname="jodeMacBook-Air",
            now=datetime(2026, 6, 24, 14, 0, 0),
        )
        night = self.runner.plan_due_jobs(
            "macbook",
            status,
            hostname="jodeMacBook-Air",
            now=datetime(2026, 6, 24, 19, 30, 0),
        )

        self.assertNotIn("layer1_rag", [job.name for job in daytime])
        self.assertNotIn("layer2_wiki", [job.name for job in daytime])
        self.assertIn("layer1_rag", [job.name for job in night])
        self.assertIn("layer2_wiki", [job.name for job in night])

    def test_deepseek_window_crosses_midnight(self):
        self.assertTrue(self.runner.is_deepseek_window(now=datetime(2026, 6, 24, 19, 0, 0)))
        self.assertTrue(self.runner.is_deepseek_window(now=datetime(2026, 6, 25, 7, 59, 0)))
        self.assertFalse(self.runner.is_deepseek_window(now=datetime(2026, 6, 25, 8, 0, 0)))
        self.assertFalse(self.runner.is_deepseek_window(now=datetime(2026, 6, 24, 18, 59, 0)))

    def test_skip_noop_metrics_leaves_status_unchanged_when_nothing_due(self):
        with patch.object(self.runner, "plan_due_jobs", return_value=[]), \
             patch.object(self.runner, "record_metrics") as record_metrics, \
             patch.object(self.runner, "record_job_result") as record_job_result:
            exit_code = self.runner.run_due_jobs("macbook", skip_noop_metrics=True)

        self.assertEqual(0, exit_code)
        record_metrics.assert_not_called()
        record_job_result.assert_not_called()


if __name__ == "__main__":
    unittest.main()
