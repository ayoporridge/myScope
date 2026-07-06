from pathlib import Path
import unittest


class Python39CompatibilityTests(unittest.TestCase):
    def test_scripts_with_pep604_annotations_defer_annotation_evaluation(self):
        scripts = [
            "scripts/dashboard.py",
            "scripts/_metrics.py",
            "scripts/formation_quality.py",
            "scripts/run_due_jobs.py",
            "scripts/run_job.py",
            "scripts/layer1_rag.py",
            "scripts/layer2_wiki.py",
            "scripts/layer3_wechat.py",
            "scripts/layer3_index.py",
            "scripts/health_check.py",
            "scripts/subscribe_podcasts.py",
            "scripts/ingest_wechat_url.py",
        ]

        for script in scripts:
            content = Path(script).read_text()
            if " | " not in content:
                continue
            with self.subTest(script=script):
                first_lines = "\n".join(content.splitlines()[:40])
                self.assertIn("from __future__ import annotations", first_lines)


if __name__ == "__main__":
    unittest.main()
