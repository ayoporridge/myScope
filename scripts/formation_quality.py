#!/usr/bin/env python3
"""Build content-date quality metrics for Hippocampus formation."""

from __future__ import annotations

import argparse
import json
import re
import socket
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_DIR = Path(__file__).parent.parent
OUTPUT_DIR = PROJECT_DIR / "data" / "formation_quality"
HOSTNAME = socket.gethostname().split(".")[0]


def is_meaningful(msg: dict) -> bool:
    text = msg.get("content", "")
    role = msg.get("role", "")
    if not (20 <= len(text) <= 800):
        return False
    if text.strip().startswith("<"):
        return False
    if text.count("{") > 5 and text.count("}") > 5:
        return False
    if role == "user" and len(text) < 10:
        return False
    return True


def parse_time(value, fallback: datetime | None = None) -> datetime | None:
    if value is None or value == "":
        return fallback
    if isinstance(value, (int, float)):
        try:
            timestamp = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(timestamp)
        except (OSError, OverflowError, ValueError):
            return fallback
    text = str(value).strip()
    if not text:
        return fallback
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return fallback


def _text_from_content(content_raw) -> str:
    if isinstance(content_raw, list):
        return "\n".join(
            str(part.get("text", ""))
            for part in content_raw
            if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text")
        ).strip()
    return str(content_raw or "").strip()


def _message(role: str, content: str, occurred_at: datetime | None) -> dict | None:
    if role not in ("user", "assistant"):
        return None
    text = str(content or "").strip()
    if not text:
        return None
    return {"role": role, "content": text[:1000], "occurred_at": occurred_at}


def _path_mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def collect_claude_messages(start: datetime) -> list[dict]:
    messages = []
    for base in (Path.home() / ".claude" / "projects", Path.home() / ".claude-internal" / "projects"):
        if not base.exists():
            continue
        for path in base.rglob("*.jsonl"):
            fallback = _path_mtime(path)
            if fallback < start:
                continue
            try:
                with path.open("r", encoding="utf-8") as f:
                    for raw in f:
                        try:
                            obj = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        msg = obj.get("message", {})
                        occurred_at = parse_time(obj.get("timestamp") or msg.get("timestamp"), fallback)
                        if not occurred_at or occurred_at < start:
                            continue
                        item = _message(msg.get("role", ""), _text_from_content(msg.get("content", "")), occurred_at)
                        if item:
                            messages.append(item)
            except OSError:
                continue
    return messages


def collect_codex_messages(start: datetime) -> list[dict]:
    messages = []
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.exists():
        return messages
    for path in sessions_dir.rglob("*.jsonl"):
        fallback = _path_mtime(path)
        if fallback < start:
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for raw in f:
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") != "response_item":
                        continue
                    payload = obj.get("payload", {})
                    occurred_at = parse_time(obj.get("timestamp") or payload.get("timestamp"), fallback)
                    if not occurred_at or occurred_at < start:
                        continue
                    item = _message(payload.get("role", ""), _text_from_content(payload.get("content", "")), occurred_at)
                    if item:
                        messages.append(item)
        except OSError:
            continue
    return messages


def collect_hermes_jsonl_messages(start: datetime) -> list[dict]:
    messages = []
    sessions_dir = Path.home() / ".hermes" / "hermes-agent" / "sessions"
    if not sessions_dir.exists():
        sessions_dir = Path.home() / ".hermes" / "sessions"
    if not sessions_dir.exists():
        return messages
    for path in sessions_dir.glob("*.jsonl"):
        fallback = _path_mtime(path)
        if fallback < start:
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for raw in f:
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    occurred_at = parse_time(obj.get("timestamp"), fallback)
                    if not occurred_at or occurred_at < start:
                        continue
                    item = _message(obj.get("role", ""), obj.get("content", ""), occurred_at)
                    if item:
                        messages.append(item)
        except OSError:
            continue
    return messages


def collect_hermes_sqlite_messages(start: datetime) -> list[dict]:
    messages = []
    for db_path in (Path.home() / ".hermes" / "state.db", Path.home() / ".hermes" / "hermes-agent" / "state.db"):
        if not db_path.exists():
            continue
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "SELECT role, content, timestamp FROM messages "
                "WHERE timestamp >= ? AND role IN ('user', 'assistant') "
                "ORDER BY timestamp",
                (start.timestamp(),),
            )
            for role, content, timestamp in cur.fetchall():
                item = _message(role, content, parse_time(timestamp))
                if item:
                    messages.append(item)
            conn.close()
        except sqlite3.Error:
            continue
        if messages:
            break
    return messages


def collect_clacky_messages(start: datetime) -> list[dict]:
    messages = []
    sessions_dir = Path.home() / ".clacky" / "sessions"
    if not sessions_dir.exists():
        return messages

    for path in sessions_dir.glob("*.json"):
        fallback = _path_mtime(path)
        if fallback < start:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for msg in data.get("messages", []):
            occurred_at = parse_time(
                msg.get("timestamp") or msg.get("created_at") or msg.get("time"),
                fallback,
            )
            if not occurred_at or occurred_at < start:
                continue
            item = _message(msg.get("role", ""), _text_from_content(msg.get("content", "")), occurred_at)
            if item:
                messages.append(item)

    for path in sessions_dir.glob("*-chunk-*.md"):
        fallback = _path_mtime(path)
        if fallback < start:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        parts = re.split(r"\n## (User|Assistant)\n", content)
        i = 1
        while i + 1 < len(parts):
            role = "user" if parts[i].strip() == "User" else "assistant"
            item = _message(role, parts[i + 1], fallback)
            if item:
                messages.append(item)
            i += 2
    return messages


def collect_messages(days: int) -> dict:
    start = datetime.now() - timedelta(days=max(days, 1) - 1)
    sources = {}
    all_messages = []
    collectors = [
        ("claude", collect_claude_messages),
        ("codex", collect_codex_messages),
        ("hermes_jsonl", collect_hermes_jsonl_messages),
        ("clacky", collect_clacky_messages),
    ]
    for name, collector in collectors:
        messages = collector(start)
        sources[name] = len(messages)
        all_messages.extend(messages)

    if sources.get("hermes_jsonl", 0) == 0:
        hermes_sqlite = collect_hermes_sqlite_messages(start)
        sources["hermes_sqlite"] = len(hermes_sqlite)
        all_messages.extend(hermes_sqlite)

    return {"messages": all_messages, "sources": sources}


def build_rows(messages: list[dict], days: int) -> list[dict]:
    dates = [(datetime.now().date() - timedelta(days=max(days, 1) - 1) + timedelta(days=i)).isoformat() for i in range(max(days, 1))]
    by_date = defaultdict(lambda: {"total_messages": 0, "meaningful_messages": 0, "filtered_messages": 0})
    for msg in messages:
        occurred_at = msg.get("occurred_at")
        if not isinstance(occurred_at, datetime):
            continue
        date = occurred_at.date().isoformat()
        by_date[date]["total_messages"] += 1
        if is_meaningful(msg):
            by_date[date]["meaningful_messages"] += 1
        else:
            by_date[date]["filtered_messages"] += 1
    return [{"date": date, **by_date[date]} for date in dates]


def build_snapshot(days: int = 14, hostname: str = HOSTNAME) -> dict:
    days = max(1, min(days, 90))
    collected = collect_messages(days)
    rows = build_rows(collected["messages"], days)
    return {
        "ok": True,
        "mode": "content_date",
        "hostname": hostname,
        "days": days,
        "rows": rows,
        "sources": collected["sources"],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def write_snapshot(days: int = 14, output_dir: Path = OUTPUT_DIR, hostname: str = HOSTNAME) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{hostname}.json"
    snapshot = build_snapshot(days=days, hostname=hostname)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build content-date formation quality snapshot")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()
    path = write_snapshot(days=args.days, output_dir=Path(args.output_dir))
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
