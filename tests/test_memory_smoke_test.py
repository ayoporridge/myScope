import importlib
import unittest


class MemorySmokeTestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.smoke = importlib.import_module("scripts.memory_smoke_test")

    def test_default_checks_cover_three_indexes_and_recall(self):
        checks = self.smoke.default_checks()
        names = {check["name"] for check in checks}

        self.assertIn("recent_personal_memory", names)
        self.assertIn("hubble_radius", names)
        self.assertIn("wiki_entries", names)
        self.assertIn("hippocampus_recall", names)


if __name__ == "__main__":
    unittest.main()
