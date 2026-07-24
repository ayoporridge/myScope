import importlib
import io
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


class Layer3WechatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("MEMORY_API_TOKEN", "test-token")
        cls.layer3 = importlib.import_module("scripts.layer3_wechat")

    def test_fetch_articles_returns_none_on_opencli_failure(self):
        failed = subprocess.CompletedProcess(
            args=["opencli"],
            returncode=127,
            stdout="",
            stderr="env: node: No such file or directory",
        )

        with patch.object(self.layer3.subprocess, "run", return_value=failed) as run, redirect_stdout(io.StringIO()):
            articles = self.layer3.fetch_articles("2026-06-21")

        self.assertIsNone(articles)
        self.assertEqual(self.layer3.OPENCLI_TIMEOUT_SECONDS, run.call_args.kwargs["timeout"])


if __name__ == "__main__":
    unittest.main()
