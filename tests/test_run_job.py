import importlib
import os
import tempfile
import unittest
from pathlib import Path


class RunJobLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_job = importlib.import_module("scripts.run_job")

    def test_acquire_lock_replaces_stale_pid_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = self.run_job.LOCKS_DIR
            self.run_job.LOCKS_DIR = Path(tmp)
            try:
                lock_path = self.run_job.LOCKS_DIR / "demo.lock"
                lock_path.write_text("99999999\n2026-07-07T02:56:45\n", encoding="utf-8")

                acquired = self.run_job.acquire_lock("demo")

                self.assertEqual(lock_path, acquired)
                self.assertTrue(lock_path.exists())
                self.assertEqual(str(os.getpid()), lock_path.read_text(encoding="utf-8").splitlines()[0])
            finally:
                self.run_job.release_lock(self.run_job.LOCKS_DIR / "demo.lock")
                self.run_job.LOCKS_DIR = original

    def test_acquire_lock_keeps_active_pid_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            original = self.run_job.LOCKS_DIR
            self.run_job.LOCKS_DIR = Path(tmp)
            try:
                lock_path = self.run_job.LOCKS_DIR / "demo.lock"
                lock_path.write_text(f"{os.getpid()}\n2026-07-09T06:46:00\n", encoding="utf-8")

                self.assertIsNone(self.run_job.acquire_lock("demo"))
                self.assertEqual(str(os.getpid()), lock_path.read_text(encoding="utf-8").splitlines()[0])
            finally:
                lock_path.unlink(missing_ok=True)
                self.run_job.LOCKS_DIR = original


if __name__ == "__main__":
    unittest.main()
