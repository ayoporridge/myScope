#!/usr/bin/env python3
"""
ingest_obsidian_podcasts.py

Backfill Obsidian podcast transcript Markdown files into myScope memory_chunks.

This intentionally uses deterministic local chunking instead of the generic
Layer 1 LLM slicer, because podcast transcripts are long and should remain
fully searchable rather than only indexing the first few thousand characters.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is available on the current host.
    yaml = None


PROJECT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / ".env")

DEFAULT_VAULT = Path(os.environ.get("OBSIDIAN_VAULT", "/Users/xizhoumini/Documents/obsidian-default")).expanduser()
DEFAULT_ROOT = Path(os.environ.get("OBSIDIAN_PODCAST_ROOT", str(DEFAULT_VAULT / "播客"))).expanduser()

MEMORY_URL = os.environ.get("MEMORY_API_URL", "http://localhost:8092").rstrip("/")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")
MEILI_URL = os.environ.get("MEILI_URL", "http://localhost:7700").rstrip("/")
MEILI_KEY = os.environ.get("MEILI_KEY") or os.environ.get("MEILI_MASTER_KEY") or "memory-master-key-2026"
INDEX_NAME = "memory_chunks"
STATE_FILE = PROJECT_DIR / "logs" / "obsidian_podcast_ingest_state.json"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            raw_meta = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :]).strip()
            if yaml is None:
                return {}, body
            try:
                meta = yaml.safe_load(raw_meta) or {}
                return meta if isinstance(meta, dict) else {}, body
            except Exception:
                return {}, body
    return {}, text.strip()


def first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def split_long_block(block: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    block = block.strip()
    while len(block) > max_chars:
        window = block[:max_chars]
        cut = -1
        for sep in ("。", "！", "？", "；", ".", "!", "?", ";", "\n"):
            pos = window.rfind(sep, int(max_chars * 0.55))
            if pos > cut:
                cut = pos + len(sep)
        if cut <= 0:
            cut = max_chars
        parts.append(block[:cut].strip())
        block = block[cut:].strip()
    if block:
        parts.append(block)
    return parts


def chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    text = text.replace("\x00", "").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return []

    blocks: list[str] = []
    for block in re.split(r"\n{2,}", text):
        block = block.strip()
        if not block:
            continue
        if len(block) > max_chars:
            blocks.extend(split_long_block(block, max_chars))
        else:
            blocks.append(block)

    chunks: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = block
    if current:
        chunks.append(current)

    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks

    overlapped = [chunks[0]]
    for prev, chunk in zip(chunks, chunks[1:]):
        tail = prev[-overlap_chars:].strip()
        overlapped.append(f"{tail}\n\n{chunk}" if tail else chunk)
    return overlapped


def stable_id(relative_path: str, chunk_index: int) -> str:
    key = f"obsidian-podcast:{relative_path}:{chunk_index:04d}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def build_docs(path: Path, root: Path, chunk_chars: int, overlap_chars: int) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta, body = split_frontmatter(raw)
    relative_path = path.relative_to(root).as_posix()
    source_path = f"播客/{relative_path}"
    podcast = str(meta.get("podcast") or path.parent.name)
    episode_title = str(meta.get("title") or first_heading(body) or path.stem)
    episode_date = str(meta.get("date") or "")
    status = str(meta.get("transcription_status") or "")
    url = str(meta.get("url") or "")

    chunks = chunk_text(body, chunk_chars, overlap_chars)
    total = len(chunks)
    docs: list[dict[str, Any]] = []
    indexed_at = datetime.now().isoformat(timespec="seconds")
    for idx, chunk in enumerate(chunks, start=1):
        header = (
            f"播客：{podcast}\n"
            f"标题：{episode_title}\n"
            f"日期：{episode_date}\n"
            f"片段：{idx}/{total}\n\n"
        )
        docs.append(
            {
                "id": stable_id(source_path, idx),
                "title": f"{episode_title} ({idx}/{total})",
                "text": header + chunk,
                "source": f"obsidian:{source_path}",
                "path": str(path),
                "podcast": podcast,
                "episode_title": episode_title,
                "date": episode_date,
                "url": url,
                "transcription_status": status,
                "chunk_index": idx,
                "chunk_total": total,
                "indexed_at": indexed_at,
            }
        )
    return docs


def ingest_batch(docs: list[dict[str, Any]], timeout: int) -> int:
    headers = {
        "Authorization": f"Bearer {MEMORY_TOKEN}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        f"{MEMORY_URL}/ingest",
        headers=headers,
        json={"index": INDEX_NAME, "documents": docs},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return int(payload.get("task_uid", -1))


def wait_for_tasks(task_uids: list[int], timeout_seconds: int = 300) -> None:
    if not task_uids:
        return
    headers = {"Authorization": f"Bearer {MEILI_KEY}"}
    deadline = time.time() + timeout_seconds
    pending = set(task_uids)
    failed: list[tuple[int, str]] = []
    while pending and time.time() < deadline:
        for task_uid in list(pending):
            response = requests.get(f"{MEILI_URL}/tasks/{task_uid}", headers=headers, timeout=10)
            if response.status_code != 200:
                continue
            payload = response.json()
            status = payload.get("status")
            if status == "succeeded":
                pending.remove(task_uid)
            elif status in {"failed", "canceled"}:
                pending.remove(task_uid)
                failed.append((task_uid, json.dumps(payload.get("error", {}), ensure_ascii=False)))
        if pending:
            time.sleep(0.5)
    if pending:
        raise TimeoutError(f"Meilisearch tasks still pending: {sorted(pending)[:10]}")
    if failed:
        raise RuntimeError(f"Meilisearch tasks failed: {failed[:3]}")


def iter_markdown_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in root.rglob("*.md")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest Obsidian podcast transcripts into myScope.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Podcast Markdown root.")
    parser.add_argument("--chunk-chars", type=int, default=2400)
    parser.add_argument("--overlap-chars", type=int, default=160)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = args.root.expanduser()
    if not root.exists():
        raise SystemExit(f"podcast root not found: {root}")

    files = iter_markdown_files(root)
    state = load_json(STATE_FILE, {"files": {}})
    all_docs: list[dict[str, Any]] = []
    per_podcast: dict[str, int] = {}
    per_file_chunks: dict[str, int] = {}

    for path in files:
        docs = build_docs(path, root, args.chunk_chars, args.overlap_chars)
        if not docs:
            continue
        all_docs.extend(docs)
        rel = path.relative_to(root).as_posix()
        per_file_chunks[f"播客/{rel}"] = len(docs)
        per_podcast[docs[0]["podcast"]] = per_podcast.get(docs[0]["podcast"], 0) + 1

    print(f"podcast_root={root}")
    print(f"markdown_files={len(files)}")
    print(f"documents_to_ingest={len(all_docs)}")
    print("files_by_podcast=" + json.dumps(dict(sorted(per_podcast.items())), ensure_ascii=False))

    if args.dry_run:
        return 0

    task_uids: list[int] = []
    for start in range(0, len(all_docs), args.batch_size):
        batch = all_docs[start : start + args.batch_size]
        task_uid = ingest_batch(batch, args.timeout)
        task_uids.append(task_uid)
        print(f"submitted {min(start + len(batch), len(all_docs))}/{len(all_docs)} docs")

    wait_for_tasks(task_uids)
    state["last_run_at"] = datetime.now().isoformat(timespec="seconds")
    state["root"] = str(root)
    state["markdown_files"] = len(files)
    state["documents_ingested"] = len(all_docs)
    state["files"] = per_file_chunks
    save_json(STATE_FILE, state)
    print(f"[done] ingested {len(all_docs)} docs from {len(files)} markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
