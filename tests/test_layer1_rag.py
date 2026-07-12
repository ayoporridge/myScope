import importlib
import os
import unittest


class Layer1RagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
        os.environ.setdefault("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        os.environ.setdefault("DEEPSEEK_MODEL", "deepseek-chat")
        cls.layer1 = importlib.import_module("scripts.layer1_rag")

    def test_sliceable_input_matches_slice_text_minimum(self):
        self.assertFalse(self.layer1.is_sliceable_input("不过认识你是个很好的事儿"))
        self.assertTrue(self.layer1.is_sliceable_input("这是一段足够长的微信收藏文本，可以被送入第一层切片流程，避免短句制造空切片告警。"))


if __name__ == "__main__":
    unittest.main()
