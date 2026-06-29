import importlib
import os
import unittest
from datetime import datetime, timedelta


class Layer2WikiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
        os.environ.setdefault("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        os.environ.setdefault("DEEPSEEK_MODEL", "deepseek-chat")
        cls.layer2 = importlib.import_module("scripts.layer2_wiki")

    def test_extract_topics_falls_back_to_text_content(self):
        chunks = [
            {
                "text": "今天继续修复 MyScope dashboard 监控，重点是 MacBook 调度和哈勃半径融合。",
                "source": "dayflow",
                "date": datetime.now().strftime("%Y-%m-%d"),
            },
            {
                "content": "Layer 2 wiki 需要从 hubble_radius 中召回 AI Agent 相关内容。",
                "source": "flomo",
                "created_at": datetime.now().isoformat(),
            },
        ]

        topics = self.layer2.extract_topics(chunks)

        self.assertIn("myscope", [t.lower() for t in topics])
        self.assertTrue(any("dashboard" in t.lower() for t in topics))
        self.assertTrue(any("hubble" in t.lower() or "哈勃" in t for t in topics))

    def test_filter_recent_chunks_drops_old_dated_docs(self):
        now = datetime.now()
        chunks = [
            {"text": "old", "date": (now - timedelta(days=7)).strftime("%Y-%m-%d")},
            {"text": "new", "created_at": now.isoformat()},
            {"text": "unknown date"},
        ]

        recent = self.layer2.filter_recent_chunks(chunks, now=now, hours=25)

        self.assertEqual(["new", "unknown date"], [c["text"] for c in recent])

    def test_existing_title_extraction_uses_title_or_text_first_line(self):
        docs = [
            {"title": "明确标题"},
            {"text": "文本标题\n正文内容"},
            {"content": "内容标题：更多说明"},
        ]

        titles = self.layer2.extract_wiki_titles(docs)

        self.assertEqual(["明确标题", "文本标题", "内容标题"], titles)


if __name__ == "__main__":
    unittest.main()
