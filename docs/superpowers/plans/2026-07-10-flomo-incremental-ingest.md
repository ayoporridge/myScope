# Flomo Incremental Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the broken DOM/DeepSeek Flomo pipeline with an automatic, deterministic, memo-ID-based incremental collector and safely rebuild the current Flomo index.

**Architecture:** `scripts/layer1_flomo.py` will call `opencli flomo memos`, page with an inclusive `updated_at` cursor, normalize one memo into one stable document, and commit versioned state only after every Meilisearch task succeeds. The migration writes and verifies all active memos before deleting the snapshotted legacy IDs.

**Tech Stack:** Python 3.9, standard library, `requests`, `python-dotenv`, OpenCLI 1.8.5, Memory API, Meilisearch, `unittest`.

## Global Constraints

- Preserve `/usr/bin/python3` 3.9 compatibility.
- Load credentials only from `.env`; never add credential literals to tracked code.
- Preserve and do not stage current runtime-state, metrics, `reports/`, or `scripts/tests/` changes.
- Use `sha1("flomo:" + memo_id)` as the document ID.
- Treat OpenCLI exit 66 plus `EMPTY_RESULT` as successful zero input; every other non-zero exit fails without advancing state.
- Do not delete the 293 legacy documents until the active memo rebuild is verified.
- Keep the single 19:10 `com.myscope.layer1-flomo` launchd schedule.

---

### Task 1: Structured collection and canonical documents

**Files:**
- Create: `tests/test_layer1_flomo.py`
- Modify: `scripts/layer1_flomo.py`

**Interfaces:**
- Consumes: OpenCLI rows containing `id`, `content`, `tags`, `images`, `created_at`, `updated_at`, and `url`.
- Produces: `parse_opencli_rows(stdout)`, `html_to_text(value)`, `memo_timestamp(value)`, `build_document(memo, indexed_at)`, `run_opencli_page(since)`, and `collect_flomo(cursor)`.

- [ ] **Step 1: Write failing parser/document tests**

```python
import importlib
import json
import unittest


class Layer1FlomoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        self.assertEqual("2026-07-08", doc["date"])
        self.assertEqual(40, len(doc["id"]))

    def test_parser_rejects_malformed_rows(self):
        with self.assertRaises(ValueError):
            self.flomo.parse_opencli_rows("not-json")
        with self.assertRaises(ValueError):
            self.flomo.parse_opencli_rows(json.dumps([{"content": "missing id"}]))

    def test_image_only_and_empty_memos(self):
        image = self.memo(content="")
        image["images"] = "https://img.example/1.jpg"
        self.assertIn("图片", self.flomo.build_document(image, "2026-07-10T12:00:00+08:00")["text"])
        self.assertIsNone(self.flomo.build_document(self.memo(content=""), "2026-07-10T12:00:00+08:00"))
```

- [ ] **Step 2: Run RED**

Run `/usr/bin/python3 -m unittest discover -s tests -p 'test_layer1_flomo.py' -v`.

Expected: FAIL because the parser and document functions do not exist.

- [ ] **Step 3: Implement deterministic normalization**

Remove the OpenAI import, client, prompt, globals, and `slice_text`. Add `from __future__ import annotations`, a small `HTMLParser` subclass that inserts newlines for block tags, and these exact contracts:

```python
def parse_opencli_rows(stdout: str) -> list[dict]:
    try:
        rows = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"opencli returned malformed JSON: {exc}") from exc
    if not isinstance(rows, list):
        raise ValueError("opencli Flomo output must be a JSON array")
    unique = {}
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            raise ValueError("opencli returned a memo without id")
        unique[str(row["id"])] = row
    return list(unique.values())


def build_document(memo: dict, indexed_at: str) -> dict | None:
    memo_id = str(memo["id"])
    text = html_to_text(memo.get("content", ""))
    images = str(memo.get("images") or "").strip()
    if not text and images:
        text = f"[图片笔记] {images}"
    if not text:
        return None
    created_at = str(memo.get("created_at") or memo.get("updated_at") or "")
    return {
        "id": hashlib.sha1(f"flomo:{memo_id}".encode()).hexdigest(),
        "memo_id": memo_id,
        "title": next(line for line in text.splitlines() if line.strip())[:60],
        "text": text,
        "content": text,
        "source": "flomo",
        "date": created_at[:10],
        "created_at": created_at,
        "updated_at": str(memo.get("updated_at") or created_at),
        "indexed_at": indexed_at,
        "url": str(memo.get("url") or ""),
        "tags": str(memo.get("tags") or ""),
        "images": images,
    }
```

- [ ] **Step 4: Add failing pagination tests**

```python
import subprocess
from unittest.mock import patch

    def test_inclusive_pages_are_deduplicated(self):
        first = [self.memo(f"memo-{i}") for i in range(200)]
        second = [first[-1], self.memo("memo-200")]
        first[-1]["updated_at"] = "2026-07-08T07:04:00+08:00"
        second[0]["updated_at"] = "2026-07-08T07:04:00+08:00"
        second[1]["updated_at"] = "2026-07-08T07:05:00+08:00"
        pages = [
            subprocess.CompletedProcess([], 0, json.dumps(first), ""),
            subprocess.CompletedProcess([], 0, json.dumps(second), ""),
        ]
        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), \
             patch.object(self.flomo, "run_opencli_page", side_effect=pages):
            rows, cursor = self.flomo.collect_flomo(0)
        self.assertEqual(201, len(rows))
        self.assertGreater(cursor, 0)

    def test_empty_result_is_success_but_other_failure_raises(self):
        empty = subprocess.CompletedProcess([], 66, "", "code: EMPTY_RESULT")
        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), \
             patch.object(self.flomo, "run_opencli_page", return_value=empty):
            self.assertEqual(([], 123), self.flomo.collect_flomo(123))
        failed = subprocess.CompletedProcess([], 1, "", "auth failed")
        with patch.object(self.flomo, "wake_browser_bridge", return_value=True), \
             patch.object(self.flomo, "run_opencli_page", return_value=failed):
            with self.assertRaises(RuntimeError):
                self.flomo.collect_flomo(0)
```

- [ ] **Step 5: Run RED, then implement page collection**

Run the Task 1 test command. Expected: pagination tests FAIL for missing structured collection.

Implement `run_opencli_page(since)` with `opencli flomo memos --limit 200 --since <since> -f json --window background --site-session persistent --keep-tab false`. Implement `collect_flomo(cursor)` to deduplicate by ID, advance using the maximum parsed `updated_at`, stop on a short page or EMPTY_RESULT, and raise `RuntimeError("Flomo pagination did not advance")` when a full page has no time/ID progress.

- [ ] **Step 6: Run GREEN and commit**

```bash
/usr/bin/python3 -m unittest discover -s tests -p 'test_layer1_flomo.py' -v
/usr/bin/python3 -m py_compile scripts/layer1_flomo.py
git add scripts/layer1_flomo.py tests/test_layer1_flomo.py
git commit -m "fix: collect Flomo memos by stable ID"
```

### Task 2: Atomic state and verified ingest

**Files:**
- Modify: `scripts/layer1_flomo.py`
- Modify: `tests/test_layer1_flomo.py`

**Interfaces:**
- Consumes: Task 1 memo documents and Memory API `/ingest`.
- Produces: `load_state`, atomic `save_state`, `ingest_documents`, `wait_for_tasks`, and `run_once`.

- [ ] **Step 1: Write failing state tests**

```python
import tempfile
from pathlib import Path

    def test_state_commits_only_after_tasks_succeed(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with patch.object(self.flomo, "STATE_FILE", state_file), \
                 patch.object(self.flomo, "collect_flomo", return_value=([self.memo()], 123)), \
                 patch.object(self.flomo, "ingest_documents", return_value=[91]), \
                 patch.object(self.flomo, "wait_for_tasks") as wait, \
                 patch.object(self.flomo, "record_last_run"), \
                 patch.object(self.flomo, "record_metrics"):
                self.assertEqual(0, self.flomo.run_once())
            wait.assert_called_once_with([91])
            state = json.loads(state_file.read_text())
            self.assertEqual(["memo-1"], state["seen_memo_ids"])
            self.assertEqual(123, state["cursor_updated_at"])

    def test_failed_task_leaves_state_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            original = {"version": 2, "cursor_updated_at": 10, "seen_memo_ids": []}
            state_file.write_text(json.dumps(original))
            with patch.object(self.flomo, "STATE_FILE", state_file), \
                 patch.object(self.flomo, "collect_flomo", return_value=([self.memo()], 123)), \
                 patch.object(self.flomo, "ingest_documents", return_value=[91]), \
                 patch.object(self.flomo, "wait_for_tasks", side_effect=RuntimeError("task failed")), \
                 patch.object(self.flomo, "record_last_run") as last_run, \
                 patch.object(self.flomo, "record_metrics"):
                self.assertEqual(2, self.flomo.run_once())
            self.assertEqual(original, json.loads(state_file.read_text()))
            last_run.assert_not_called()

    def test_seen_memo_is_not_ingested_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            state_file.write_text(json.dumps({"version": 2, "cursor_updated_at": 10, "seen_memo_ids": ["memo-1"]}))
            with patch.object(self.flomo, "STATE_FILE", state_file), \
                 patch.object(self.flomo, "collect_flomo", return_value=([self.memo()], 123)), \
                 patch.object(self.flomo, "ingest_documents") as ingest, \
                 patch.object(self.flomo, "record_last_run"), \
                 patch.object(self.flomo, "record_metrics"):
                self.assertEqual(0, self.flomo.run_once())
            ingest.assert_not_called()
```

- [ ] **Step 2: Run RED**

Run the Flomo tests. Expected: FAIL because version 2 state, verified task waiting, and `run_once` do not exist.

- [ ] **Step 3: Implement state and ingest contracts**

`load_state()` must convert missing/legacy state to `{version: 2, cursor_updated_at: 0, seen_memo_ids: []}`. `save_state()` must use `tempfile.mkstemp` plus `os.replace`. `ingest_documents()` posts batches of 50 and returns every integer `task_uid`. `wait_for_tasks()` requires `MEILI_MASTER_KEY` from `.env`, polls `${MEILI_URL}/tasks/<uid>` until all are `succeeded`, and raises on timeout, `failed`, or `canceled`.

`run_once()` must:

1. load state;
2. collect from the cursor;
3. filter seen IDs;
4. build documents;
5. ingest and wait;
6. atomically save all fetched new IDs and the new cursor;
7. record success metrics and `last_run`.

On any exception it records error metrics, returns 2, and does not save state. Success metrics are exactly `fetched_memos`, `new_memos`, `skipped_seen_memos`, `skipped_empty_memos`, `documents_written`, `chunks`, `latest_memo_updated_at`, `collect_errors`, `ingest_errors`, and duration.

- [ ] **Step 4: Run GREEN and commit**

```bash
/usr/bin/python3 -m unittest discover -s tests -p 'test_layer1_flomo.py' -v
/usr/bin/python3 -m py_compile scripts/layer1_flomo.py
git add scripts/layer1_flomo.py tests/test_layer1_flomo.py
git commit -m "fix: persist verified Flomo ingest state"
```

### Task 3: Health semantics and docs

**Files:**
- Modify: `scripts/health_check.py`
- Modify: `tests/test_health_check.py`
- Modify: `tests/test_python39_compat.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `new_memos` and `documents_written` metrics.
- Produces: zero-new success without warning; new-input/zero-output red alert.

- [ ] **Step 1: Add failing health tests**

Add a test helper that writes supplied metrics to a temporary JSONL file and calls `check_quality()`. Add one test with `new_memos=0, documents_written=0` asserting no Layer 1 warning, and one with `new_memos=2, documents_written=0` asserting an alert containing `layer1_flomo` and `写入 0`.

- [ ] **Step 2: Run RED**

Run `/usr/bin/python3 -m unittest discover -s tests -p 'test_health_check.py' -v`.

Expected: zero-new fails under the current `<3 chunks` rule; input/write failure lacks its specific alert.

- [ ] **Step 3: Implement input/output validation**

Replace the fixed Layer 1 threshold block with per-job logic:

```python
if script == "layer1_flomo":
    inputs = int(metric.get("new_memos", metric.get("memos", 0)) or 0)
    outputs = int(metric.get("documents_written", metric.get("chunks", 0)) or 0)
    if inputs > 0 and outputs == 0:
        alerts.append(f"🔴 `{host}` `layer1_flomo` 有 {inputs} 条新 memo，但写入 0 条文档")
else:
    inputs = int(metric.get("raw_texts", 0) or 0)
    outputs = int(metric.get("chunks_produced", 0) or 0)
    if inputs > 0 and outputs == 0:
        alerts.append(f"🔴 `{host}` `layer1_rag` 有 {inputs} 条输入，但写入 0 条切片")
```

- [ ] **Step 4: Update docs and Python 3.9 coverage**

Change README from DOM/DeepSeek Flomo slicing to structured OpenCLI/stable-ID documents, retain 19:10, and add `scripts/layer1_flomo.py` to `tests/test_python39_compat.py`.

- [ ] **Step 5: Run GREEN and commit**

```bash
/usr/bin/python3 -m unittest discover -s tests -p 'test_health_check.py' -v
/usr/bin/python3 -m unittest discover -s tests -p 'test_python39_compat.py' -v
git add scripts/health_check.py tests/test_health_check.py tests/test_python39_compat.py README.md
git commit -m "fix: monitor Flomo input and writes"
```

### Task 4: Safe rebuild and live idempotency proof

**Files:**
- Runtime backup: `logs/backups/flomo-memory-chunks-before-rebuild-20260710.json`
- Runtime backup: `logs/backups/layer1-flomo-state-before-rebuild-20260710.json`
- Runtime audit: `logs/backups/flomo-active-deleted-audit-20260710.json`
- Runtime state: `logs/layer1_flomo_state.json`
- Create: `docs/superpowers/plans/2026-07-10-flomo-incremental-ingest.md`

**Interfaces:**
- Consumes: Tasks 1-3, current Flomo login, Memory API, and Meilisearch.
- Produces: active memo coverage, deleted legacy cleanup, v2 state, and a verified no-op second run.

- [ ] **Step 1: Configure task verification securely**

Copy the running local Memory API's existing Meilisearch key into gitignored `.env` as `MEILI_MASTER_KEY` without printing it. Do not add it to tracked files.

- [ ] **Step 2: Snapshot before mutation**

Fetch all `memory_chunks` documents, write the full list of `source == flomo and memo_id missing` to the legacy backup, and back up the current state. Assert the legacy backup count before continuing.

- [ ] **Step 3: Audit active/deleted IDs without storing memo content or token**

Use the authenticated Browser Bridge and the same signed `/api/v1/memo/updated/` endpoint used by the installed OpenCLI adapter. Persist only `{active_ids, deleted_ids, audited_at}`. Assert the sets are disjoint.

- [ ] **Step 4: Backfill before deleting legacy data**

Seed a v2 bootstrap state with cursor 0 and only deleted IDs in `seen_memo_ids`. Run `/usr/bin/python3 scripts/layer1_flomo.py`. Verify distinct indexed `memo_id` equals the audited active ID set, no deleted ID is indexed, and every document has `text`, `date`, `created_at`, and `updated_at`.

- [ ] **Step 5: Delete only snapshotted legacy IDs**

Call Meilisearch `delete_documents` with exactly the backed-up legacy IDs and wait for `succeeded`. Re-fetch flomo documents and assert no flomo document lacks `memo_id`.

- [ ] **Step 6: Prove the second run is a no-op**

Capture the flomo document count, run the script again, and assert exit 0, `new_memos=0`, `documents_written=0`, unchanged document count, and unchanged seen IDs/cursor.

- [ ] **Step 7: Run full relevant verification**

```bash
/usr/bin/python3 -m unittest discover -s tests -p 'test_layer1_flomo.py' -v
/usr/bin/python3 -m unittest discover -s tests -p 'test_health_check.py' -v
/usr/bin/python3 -m unittest discover -s tests -p 'test_python39_compat.py' -v
/usr/bin/python3 -m unittest discover -s tests -p 'test_run_due_jobs.py' -v
/usr/bin/python3 -m py_compile scripts/layer1_flomo.py scripts/health_check.py
launchctl print gui/$(id -u)/com.myscope.layer1-flomo
git diff --check
git status --short --branch
```

Expected: all targeted tests pass, compile exits 0, launchd shows Hour 19 and Minute 10, and pre-existing unrelated worktree changes remain unstaged.

- [ ] **Step 8: Commit this plan only**

```bash
git add docs/superpowers/plans/2026-07-10-flomo-incremental-ingest.md
git commit -m "docs: plan idempotent Flomo ingestion"
```

Do not commit `.env`, runtime backups/state/metrics, reports, or `scripts/tests/`.
