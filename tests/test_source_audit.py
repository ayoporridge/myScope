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
                "wechat:\n"
                "  accounts:\n"
                "    - A\n"
                "    - B\n"
                "xiaoyuzhou:\n"
                "  podcasts:\n"
                "    - P1\n"
                "    - name: P2\n"
                "    - name: Skipped Podcast\n"
                "      skip: true\n"
                "blogs:\n"
                "  feeds:\n"
                "    - https://example.com/feed\n"
                "    - name: Blog 1\n"
                "      feed_url: https://example.com/blog.xml\n"
                "    - name: Skipped Blog\n"
                "      skip: true\n",
                encoding="utf-8",
            )

            sources = self.audit.load_subscription_sources(path)

            self.assertEqual(2, len([s for s in sources if s["kind"] == "wechat"]))
            self.assertEqual(2, len([s for s in sources if s["kind"] == "podcast"]))
            self.assertEqual(2, len([s for s in sources if s["kind"] == "blog"]))
            self.assertIn("podcast:P2", {s["id"] for s in sources})
            self.assertIn("blog:Blog 1", {s["id"] for s in sources})


if __name__ == "__main__":
    unittest.main()
