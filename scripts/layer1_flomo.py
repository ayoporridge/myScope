#!/usr/bin/env python3
from __future__ import annotations

"""
layer1_flomo.py
第一层补充：flomo 采集（Mac mini 端）
OpenCLI 结构化采集 → 稳定 ID 文档 → Meilisearch memory_chunks
每天 19:10 运行
"""

import os
import json
import re
import hashlib
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from dotenv import load_dotenv
import requests
from _metrics import record_last_run, record_metrics

load_dotenv(Path(__file__).parent.parent / ".env")

MEMORY_URL   = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")
MEILI_URL = os.environ.get("MEILI_URL", "http://localhost:7700")
MEILI_MASTER_KEY = os.environ.get("MEILI_MASTER_KEY", "")

def resolve_opencli() -> str:
    """Find opencli without assuming the macOS user or install prefix."""
    candidates = [
        os.environ.get("OPENCLI_PATH", ""),
        shutil.which("opencli") or "",
        "/Users/xizhoumini/.local/nodejs/bin/opencli",
        "/Users/xz/.local/nodejs/bin/opencli",
        "/opt/homebrew/bin/opencli",
        "/usr/local/bin/opencli",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return os.environ.get("OPENCLI_PATH", "") or "opencli"


OPENCLI = resolve_opencli()
OPENCLI_ENV = dict(
    os.environ,
    PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", ""),
)
OPENCLI_EXTENSION_ID = os.environ.get("OPENCLI_EXTENSION_ID", "ildkmabpimmkaediidaifkhjpohdnifk")
OPENCLI_CHROME_APP = os.environ.get("OPENCLI_CHROME_APP", "Google Chrome")
STATE_FILE = Path(__file__).parent.parent / "logs" / "layer1_flomo_state.json"

PAGE_LIMIT = 200


# ── 状态管理 ────────────────────────────────────────────────
def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except (json.JSONDecodeError, OSError):
        data = {}
    if data.get("version") != 2:
        return {"version": 2, "cursor_updated_at": 0, "seen_memo_ids": []}
    return {
        **data,
        "version": 2,
        "cursor_updated_at": int(data.get("cursor_updated_at", 0) or 0),
        "seen_memo_ids": sorted(set(map(str, data.get("seen_memo_ids", [])))),
    }


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(STATE_FILE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def is_browser_bridge_connected() -> bool:
    try:
        result = subprocess.run(
            [OPENCLI, "profile", "list"],
            capture_output=True,
            text=True,
            timeout=10,
            env=OPENCLI_ENV,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    output = f"{result.stdout}\n{result.stderr}"
    return "Connected Browser Bridge profiles" in output


def wake_browser_bridge() -> bool:
    if is_browser_bridge_connected():
        return True

    popup_url = f"chrome-extension://{OPENCLI_EXTENSION_ID}/popup.html"
    subprocess.run(
        ["/usr/bin/open", "-g", "-a", OPENCLI_CHROME_APP, popup_url],
        capture_output=True,
        text=True,
        timeout=15,
    )
    time.sleep(5)
    if is_browser_bridge_connected():
        return True

    subprocess.run(
        [OPENCLI, "daemon", "restart"],
        capture_output=True,
        text=True,
        timeout=20,
        env=OPENCLI_ENV,
    )
    subprocess.run(
        ["/usr/bin/open", "-g", "-a", OPENCLI_CHROME_APP, popup_url],
        capture_output=True,
        text=True,
        timeout=15,
    )
    time.sleep(5)
    return is_browser_bridge_connected()


# ── Flomo 结构化采集 ────────────────────────────────────────
class PlainTextParser(HTMLParser):
    BLOCK_TAGS = {"p", "div", "li", "br", "blockquote", "h1", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.BLOCK_TAGS and self.parts and not self.parts[-1].endswith("\n"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: str) -> str:
    parser = PlainTextParser()
    parser.feed(str(value or ""))
    text = unescape("".join(parser.parts)).replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n\n".join(line for line in lines if line)


def parse_opencli_rows(stdout: str) -> list[dict]:
    try:
        rows = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"opencli returned malformed JSON: {exc}") from exc
    if not isinstance(rows, list):
        raise ValueError("opencli Flomo output must be a JSON array")
    unique: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("id") or "").strip():
            raise ValueError("opencli returned a memo without id")
        unique[str(row["id"])] = row
    return list(unique.values())


def memo_timestamp(value: str) -> int:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return int(parsed.timestamp())


def build_document(memo: dict, indexed_at: str) -> dict | None:
    memo_id = str(memo["id"])
    text = html_to_text(memo.get("content", ""))
    images = str(memo.get("images") or "").strip()
    if not text and images:
        text = f"[图片笔记] {images}"
    if not text:
        return None
    created_at = str(memo.get("created_at") or memo.get("updated_at") or "")
    first_line = next(line for line in text.splitlines() if line.strip())
    return {
        "id": hashlib.sha1(f"flomo:{memo_id}".encode()).hexdigest(),
        "memo_id": memo_id,
        "title": first_line[:60],
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


def run_opencli_page(since: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            OPENCLI,
            "flomo",
            "memos",
            "--limit",
            str(PAGE_LIMIT),
            "--since",
            str(since),
            "-f",
            "json",
            "--window",
            "background",
            "--site-session",
            "persistent",
            "--keep-tab",
            "false",
        ],
        capture_output=True,
        text=True,
        timeout=90,
        env=OPENCLI_ENV,
    )


def collect_flomo(cursor_updated_at: int) -> tuple[list[dict], int]:
    if not wake_browser_bridge():
        raise RuntimeError("opencli browser bridge disconnected")
    cursor = int(cursor_updated_at or 0)
    unique: dict[str, dict] = {}
    while True:
        result = run_opencli_page(cursor)
        if result.returncode == 66 and "EMPTY_RESULT" in result.stderr:
            break
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "opencli failed").strip()[:300]
            raise RuntimeError(detail)
        page = parse_opencli_rows(result.stdout)
        if not page:
            break
        before = len(unique)
        for memo in page:
            unique[str(memo["id"])] = memo
        next_cursor = max(memo_timestamp(row["updated_at"]) for row in page)
        if len(page) < PAGE_LIMIT:
            cursor = max(cursor, next_cursor)
            break
        if next_cursor <= cursor or len(unique) == before:
            raise RuntimeError("Flomo pagination did not advance")
        cursor = next_cursor
    return list(unique.values()), cursor


# ── 写入 Meilisearch ────────────────────────────────────────
def ingest_documents(docs: list[dict]) -> list[int]:
    headers = {
        "Authorization": f"Bearer {MEMORY_TOKEN}",
        "Content-Type": "application/json",
    }
    task_uids: list[int] = []
    for offset in range(0, len(docs), 50):
        response = requests.post(
            f"{MEMORY_URL}/ingest",
            headers=headers,
            json={"index": "memory_chunks", "documents": docs[offset:offset + 50]},
            timeout=30,
        )
        response.raise_for_status()
        task_uid = int(response.json().get("task_uid", -1))
        if task_uid < 0:
            raise RuntimeError("memory-api did not return task_uid")
        task_uids.append(task_uid)
    return task_uids


def wait_for_tasks(task_uids: list[int], timeout_seconds: int = 300) -> None:
    if not task_uids:
        return
    if not MEILI_MASTER_KEY:
        raise RuntimeError("MEILI_MASTER_KEY is required to verify ingest tasks")
    headers = {"Authorization": f"Bearer {MEILI_MASTER_KEY}"}
    deadline = time.time() + timeout_seconds
    pending = set(task_uids)
    while pending and time.time() < deadline:
        for task_uid in list(pending):
            response = requests.get(
                f"{MEILI_URL}/tasks/{task_uid}",
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            status = payload.get("status")
            if status == "succeeded":
                pending.remove(task_uid)
            elif status in {"failed", "canceled"}:
                raise RuntimeError(f"Meilisearch task {task_uid} {status}")
        if pending:
            time.sleep(0.5)
    if pending:
        raise TimeoutError(f"Meilisearch tasks still pending: {sorted(pending)}")


# ── 主流程 ────────────────────────────────────────────────
def run_once() -> int:
    start_time = time.time()
    state = load_state()
    seen = set(state["seen_memo_ids"])
    stage = "collect"
    try:
        fetched, cursor = collect_flomo(state["cursor_updated_at"])
        new_memos = [memo for memo in fetched if str(memo["id"]) not in seen]
        indexed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        pairs = [(memo, build_document(memo, indexed_at)) for memo in new_memos]
        docs = [doc for _, doc in pairs if doc]
        stage = "ingest"
        task_uids = ingest_documents(docs) if docs else []
        wait_for_tasks(task_uids)

        seen.update(str(memo["id"]) for memo in new_memos)
        save_state({
            "version": 2,
            "cursor_updated_at": max(int(state["cursor_updated_at"]), int(cursor)),
            "seen_memo_ids": sorted(seen),
            "last_success_at": indexed_at,
        })
        record_last_run("layer1_flomo")
        record_metrics(
            "layer1_flomo",
            fetched_memos=len(fetched),
            new_memos=len(new_memos),
            memos=len(new_memos),
            skipped_seen_memos=len(fetched) - len(new_memos),
            skipped_empty_memos=sum(doc is None for _, doc in pairs),
            documents_written=len(docs),
            chunks=len(docs),
            collect_errors=0,
            ingest_errors=0,
            latest_memo_updated_at=cursor,
            run_duration_seconds=round(time.time() - start_time, 1),
        )
        print(
            f"  fetched={len(fetched)} new={len(new_memos)} "
            f"written={len(docs)} skipped_seen={len(fetched) - len(new_memos)}"
        )
        return 0
    except Exception as exc:
        summary = str(exc).strip().replace("\n", " ")[:300]
        record_metrics(
            "layer1_flomo",
            fetched_memos=0,
            new_memos=0,
            memos=0,
            documents_written=0,
            chunks=0,
            collect_errors=1 if stage == "collect" else 0,
            collect_error_summary=summary if stage == "collect" else "",
            ingest_errors=1 if stage == "ingest" else 0,
            ingest_error_summary=summary if stage == "ingest" else "",
            run_duration_seconds=round(time.time() - start_time, 1),
        )
        print(f"  flomo {stage} 失败，未更新 state: {summary}")
        return 2


def main() -> int:
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始 flomo 增量采集（Mac mini）")
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
