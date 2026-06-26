import importlib
import unittest
from datetime import datetime, timedelta


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
        now = datetime(2026, 6, 24, 14, 0, 0)
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


if __name__ == "__main__":
    unittest.main()
