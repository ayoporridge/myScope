import importlib
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class Layer1FlomoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
        os.environ.setdefault("MEMORY_API_TOKEN", "test-token")
        scripts_dir = str(Path("scripts").resolve())
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        cls.flomo = importlib.import_module("scripts.layer1_flomo")

    def memo(self, memo_id="memo-1", content="<p>第一段</p><p>第二段</p>"):
        return {
            "id": memo_id,
            "url": f"https://v.flomoapp.com/mine/?memo_id={memo_id}",
            "content": content,
            "tags": "工作, 想法",
            "images": "",
            "created_at": "2026-07-08T07:03:00+08:00",
            "updated_at": "2026-07-08T07:03:00+08:00",
        }

    def test_parse_and_build_searchable_document(self):
        rows = self.flomo.parse_opencli_rows(json.dumps([self.memo()]))

        doc = self.flomo.build_document(rows[0], "2026-07-10T12:00:00+08:00")

        self.assertEqual("第一段\n\n第二段", doc["text"])
        self.assertEqual(doc["text"], doc["content"])
        self.assertEqual("memo-1", doc["memo_id"])
        self.assertEqual("flomo", doc["source"])
        self.assertEqual("2026-07-08", doc["date"])
        self.assertEqual(40, len(doc["id"]))

    def test_parser_rejects_malformed_rows(self):
        with self.assertRaises(ValueError):
            self.flomo.parse_opencli_rows("not-json")
        with self.assertRaises(ValueError):
            self.flomo.parse_opencli_rows(json.dumps([{"content": "missing id"}]))

    def test_parser_deduplicates_same_memo_id(self):
        rows = self.flomo.parse_opencli_rows(json.dumps([
            self.memo("same"),
            self.memo("same", "<p>更新后的内容</p>"),
        ]))

        self.assertEqual(1, len(rows))
        self.assertIn("更新后的内容", rows[0]["content"])

    def test_image_only_and_empty_memos(self):
        image = self.memo(content="")
        image["images"] = "https://img.example/1.jpg"

        image_doc = self.flomo.build_document(image, "2026-07-10T12:00:00+08:00")

        self.assertIn("图片", image_doc["text"])
        self.assertIsNone(
            self.flomo.build_document(
                self.memo(content=""),
                "2026-07-10T12:00:00+08:00",
            )
        )

    def test_document_id_depends_on_memo_id_not_content(self):
        first = self.flomo.build_document(
            self.memo("same", "<p>旧内容</p>"),
            "2026-07-10T12:00:00+08:00",
        )
        second = self.flomo.build_document(
            self.memo("same", "<p>新内容</p>"),
            "2026-07-10T12:01:00+08:00",
        )

        self.assertEqual(first["id"], second["id"])

    def test_inclusive_pages_are_deduplicated(self):
        first = [self.memo(f"memo-{i}") for i in range(200)]
        first[-1]["updated_at"] = "2026-07-08T07:04:00+08:00"
        second = [
            dict(first[-1]),
            self.memo("memo-200"),
        ]
        second[1]["updated_at"] = "2026-07-08T07:05:00+08:00"
        pages = [
            subprocess.CompletedProcess([], 0, json.dumps(first), ""),
            subprocess.CompletedProcess([], 0, json.dumps(second), ""),
        ]

        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), patch.object(
            self.flomo,
            "run_opencli_page",
            side_effect=pages,
        ):
            rows, cursor = self.flomo.collect_flomo(0)

        self.assertEqual(201, len(rows))
        self.assertGreater(cursor, 0)

    def test_empty_result_is_success_but_other_failure_raises(self):
        empty = subprocess.CompletedProcess([], 66, "", "code: EMPTY_RESULT")
        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), patch.object(
            self.flomo,
            "run_opencli_page",
            return_value=empty,
        ):
            self.assertEqual(([], 123), self.flomo.collect_flomo(123))

        failed = subprocess.CompletedProcess([], 1, "", "auth failed")
        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), patch.object(
            self.flomo,
            "run_opencli_page",
            return_value=failed,
        ):
            with self.assertRaises(RuntimeError):
                self.flomo.collect_flomo(0)

    def test_transient_opencli_failure_retries_once(self):
        failed = subprocess.CompletedProcess([], 1, "", "temporary bridge failure")
        recovered = subprocess.CompletedProcess([], 0, json.dumps([self.memo()]), "")

        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), patch.object(
            self.flomo,
            "run_opencli_page",
            side_effect=[failed, recovered],
        ) as run_page, patch.object(self.flomo.time, "sleep"):
            rows, _ = self.flomo.collect_flomo(0)

        self.assertEqual(1, len(rows))
        self.assertEqual(2, run_page.call_count)

    def test_opencli_failure_keeps_error_after_warning_noise(self):
        warning = "(node:1) Warning: " + "x" * 400
        failed = subprocess.CompletedProcess([], 1, "", warning + "\nerror: auth failed")

        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), patch.object(
            self.flomo,
            "run_opencli_page",
            return_value=failed,
        ), patch.object(self.flomo.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "auth failed"):
                self.flomo.collect_flomo(0)

    def test_full_page_without_cursor_progress_fails(self):
        stuck = [self.memo(f"memo-{i}") for i in range(200)]
        result = subprocess.CompletedProcess([], 0, json.dumps(stuck), "")

        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), patch.object(
            self.flomo,
            "run_opencli_page",
            return_value=result,
        ):
            with self.assertRaisesRegex(RuntimeError, "did not advance"):
                self.flomo.collect_flomo(
                    self.flomo.memo_timestamp("2026-07-08T07:03:00+08:00")
                )

    def test_full_pages_with_same_timestamp_advance_by_slug(self):
        timestamp = "2026-07-08T07:03:00+08:00"
        first = [self.memo(f"first-{i}") for i in range(200)]
        second = [self.memo(f"second-{i}") for i in range(200)]
        final = [self.memo("final")]
        for memo in first + second + final:
            memo["updated_at"] = timestamp
        pages = [
            subprocess.CompletedProcess([], 0, json.dumps(first), ""),
            subprocess.CompletedProcess([], 0, json.dumps(second), ""),
            subprocess.CompletedProcess([], 0, json.dumps(final), ""),
        ]

        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), patch.object(
            self.flomo,
            "run_opencli_page",
            side_effect=pages,
        ) as run_page:
            rows, cursor = self.flomo.collect_flomo(0)

        expected_cursor = self.flomo.memo_timestamp(timestamp)
        self.assertEqual(401, len(rows))
        self.assertEqual(expected_cursor, cursor)
        self.assertEqual((0, ""), run_page.call_args_list[0].args)
        self.assertEqual((expected_cursor, "first-199"), run_page.call_args_list[1].args)
        self.assertEqual((expected_cursor, "second-199"), run_page.call_args_list[2].args)

    def test_legacy_state_starts_full_version_two_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            state_file.write_text(json.dumps({"last_run": "2026-07-09T19:10:00"}))

            with patch.object(self.flomo, "STATE_FILE", state_file):
                state = self.flomo.load_state()

        self.assertEqual({
            "version": 2,
            "cursor_updated_at": 0,
            "seen_memo_ids": [],
        }, state)

    def test_ingest_documents_returns_every_task_uid(self):
        first = Mock()
        first.raise_for_status.return_value = None
        first.json.return_value = {"task_uid": 31}
        second = Mock()
        second.raise_for_status.return_value = None
        second.json.return_value = {"task_uid": 32}

        with patch.object(self.flomo.requests, "post", side_effect=[first, second]) as post:
            task_uids = self.flomo.ingest_documents([{"id": str(i)} for i in range(51)])

        self.assertEqual([31, 32], task_uids)
        self.assertEqual(2, post.call_count)

    def test_wait_for_tasks_requires_environment_key(self):
        with patch.object(self.flomo, "MEILI_MASTER_KEY", ""):
            with self.assertRaisesRegex(RuntimeError, "MEILI_MASTER_KEY"):
                self.flomo.wait_for_tasks([31])

    def test_state_commits_only_after_tasks_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with patch.object(self.flomo, "STATE_FILE", state_file), patch.object(
                self.flomo,
                "collect_flomo",
                return_value=([self.memo()], 123),
            ), patch.object(
                self.flomo,
                "ingest_documents",
                return_value=[91],
            ), patch.object(
                self.flomo,
                "wait_for_tasks",
            ) as wait, patch.object(
                self.flomo,
                "record_last_run",
            ), patch.object(
                self.flomo,
                "record_metrics",
            ):
                self.assertEqual(0, self.flomo.run_once())

            wait.assert_called_once_with([91])
            state = json.loads(state_file.read_text())

        self.assertEqual(["memo-1"], state["seen_memo_ids"])
        self.assertEqual(123, state["cursor_updated_at"])
        self.assertEqual(2, state["version"])

    def test_failed_task_leaves_state_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            original = {
                "version": 2,
                "cursor_updated_at": 10,
                "seen_memo_ids": [],
            }
            state_file.write_text(json.dumps(original))
            with patch.object(self.flomo, "STATE_FILE", state_file), patch.object(
                self.flomo,
                "collect_flomo",
                return_value=([self.memo()], 123),
            ), patch.object(
                self.flomo,
                "ingest_documents",
                return_value=[91],
            ), patch.object(
                self.flomo,
                "wait_for_tasks",
                side_effect=RuntimeError("task failed"),
            ), patch.object(
                self.flomo,
                "record_last_run",
            ) as last_run, patch.object(
                self.flomo,
                "record_metrics",
            ):
                self.assertEqual(2, self.flomo.run_once())

            persisted = json.loads(state_file.read_text())

        self.assertEqual(original, persisted)
        last_run.assert_not_called()

    def test_seen_memo_is_not_ingested_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            state_file.write_text(json.dumps({
                "version": 2,
                "cursor_updated_at": 10,
                "seen_memo_ids": ["memo-1"],
            }))
            with patch.object(self.flomo, "STATE_FILE", state_file), patch.object(
                self.flomo,
                "collect_flomo",
                return_value=([self.memo()], 123),
            ), patch.object(
                self.flomo,
                "ingest_documents",
            ) as ingest, patch.object(
                self.flomo,
                "record_last_run",
            ), patch.object(
                self.flomo,
                "record_metrics",
            ) as metrics:
                self.assertEqual(0, self.flomo.run_once())

            state = json.loads(state_file.read_text())

        ingest.assert_not_called()
        self.assertEqual(123, state["cursor_updated_at"])
        self.assertEqual(["memo-1"], state["seen_memo_ids"])
        self.assertEqual(0, metrics.call_args.kwargs["new_memos"])
        self.assertEqual(0, metrics.call_args.kwargs["documents_written"])

    def test_run_once_holds_exclusive_state_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with patch.object(self.flomo, "STATE_FILE", state_file), patch.object(
                self.flomo,
                "collect_flomo",
                return_value=([], 10),
            ), patch.object(
                self.flomo,
                "record_last_run",
            ), patch.object(
                self.flomo,
                "record_metrics",
            ), patch("fcntl.flock") as flock:
                self.assertEqual(0, self.flomo.run_once())

        lock_modes = [args[1] for args, _ in flock.call_args_list]
        self.assertEqual([fcntl.LOCK_EX, fcntl.LOCK_UN], lock_modes)


if __name__ == "__main__":
    unittest.main()
