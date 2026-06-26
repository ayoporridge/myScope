import importlib
import tempfile
import unittest
from pathlib import Path


class SourceAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = importlib.import_module("scripts.source_audit")

    def test_load_subscription_sources_counts_wechat_and_podcasts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subscriptions.yaml"
            path.write_text(
                "wechat:\n  accounts:\n    - A\n    - B\nxiaoyuzhou:\n  podcasts:\n    - P1\nblogs:\n  feeds:\n    - https://example.com/feed\n",
                encoding="utf-8",
            )

            sources = self.audit.load_subscription_sources(path)

            self.assertEqual(2, len([s for s in sources if s["kind"] == "wechat"]))
            self.assertEqual(1, len([s for s in sources if s["kind"] == "podcast"]))
            self.assertEqual(1, len([s for s in sources if s["kind"] == "blog"]))


if __name__ == "__main__":
    unittest.main()
