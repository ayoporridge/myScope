import importlib
import tempfile
import unittest
from pathlib import Path


class SyncDashboardStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.syncer = importlib.import_module("scripts.sync_dashboard_state")

    def test_split_target_requires_host_and_absolute_path(self):
        self.assertEqual(("macmini", "/Users/xizhoumini/myScope"), self.syncer._split_target("macmini:/Users/xizhoumini/myScope"))
        with self.assertRaises(ValueError):
            self.syncer._split_target("macmini")
        with self.assertRaises(ValueError):
            self.syncer._split_target("macmini:relative/path")

    def test_sync_fails_clearly_when_host_files_are_missing(self):
        original_project = self.syncer.PROJECT_DIR
        original_hostname = self.syncer.HOSTNAME
        with tempfile.TemporaryDirectory() as tmp:
            self.syncer.PROJECT_DIR = Path(tmp)
            self.syncer.HOSTNAME = "test-host"
            try:
                with self.assertRaises(FileNotFoundError):
                    self.syncer.sync_dashboard_state("macmini:/tmp/myscope")
            finally:
                self.syncer.PROJECT_DIR = original_project
                self.syncer.HOSTNAME = original_hostname


if __name__ == "__main__":
    unittest.main()
