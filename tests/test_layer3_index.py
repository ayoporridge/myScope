import hashlib
import importlib
import os
import unittest
from datetime import datetime


class Layer3IndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("FRESHRSS_URL", "http://freshrss.example.test")
        os.environ.setdefault("FRESHRSS_USERNAME", "user")
        os.environ.setdefault("FRESHRSS_API_PASSWORD", "pass")
        cls.layer3 = importlib.import_module("scripts.layer3_index")

    def test_item_to_doc_preserves_trace_fields(self):
        published = 1783296000
        url = "https://example.com/post"
        item = {
            "title": "Example title",
            "canonical": [{"href": url}],
            "summary": {"content": "<p>Hello <b>world</b></p>"},
            "published": published,
            "origin": {"title": "Example feed"},
        }

        doc = self.layer3.item_to_doc(item)

        self.assertEqual(hashlib.md5(url.encode()).hexdigest(), doc["id"])
        self.assertEqual("Example title", doc["title"])
        self.assertEqual("Hello world", doc["content"])
        self.assertEqual("Example title Hello world", doc["text"])
        self.assertEqual(url, doc["url"])
        self.assertEqual("Example feed", doc["source"])
        self.assertEqual(datetime.fromtimestamp(published).strftime("%Y-%m-%d"), doc["date"])
        self.assertEqual(datetime.fromtimestamp(published).isoformat(), doc["published_at"])
        self.assertTrue(doc["indexed_at"])

    def test_item_to_doc_handles_missing_canonical_url(self):
        item = {
            "id": "fresh-item-1",
            "title": "No canonical",
            "canonical": [],
            "summary": {"content": ""},
            "origin": {"title": "Example feed"},
        }

        doc = self.layer3.item_to_doc(item)

        self.assertEqual("fresh-item-1", doc["id"])
        self.assertEqual("", doc["url"])


if __name__ == "__main__":
    unittest.main()
