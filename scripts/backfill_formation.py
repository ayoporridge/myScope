#!/usr/bin/env python3
"""
backfill_formation.py
一次性补跑 hippocampus formation，处理所有被 25h cutoff 过滤掉的历史数据。
只处理尚未在 .hippo_state.json 中标记为已完成的文件。
"""

import os
import re
import json
import sys
import time
from datetime import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ANDA_BASE_URL    = os.environ["ANDA_BASE_URL"]
ANDA_SPACE_ID    = os.environ["ANDA_SPACE_ID"]
ANDA_SPACE_TOKEN = os.environ["ANDA_SPACE_TOKEN"]

FORMATION_URL = f"{ANDA_BASE_URL}/v1/{ANDA_SPACE_ID}/formation"
HEADERS = {
    "Authorization": f"Bearer {ANDA_SPACE_TOKEN}",
    "Content-Type": "application/json",
}

STATE_FILE = Path(__file__).parent.parent / ".hippo_state.json"

BATCH_SIZE = 40


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


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Clacky chunk parser ──
def _parse_clacky_chunk_md(path: Path) -> list[dict]:
    messages = []
    try:
        content = path.read_text(encoding="utf-8")
        parts = re.split(r'\n## (User|Assistant)\n', content)
        i = 1
        while i + 1 < len(parts):
            role = "user" if parts[i].strip() == "User" else "assistant"
            text = parts[i + 1].strip()
            if text:
                messages.append({"role": role, "content": text[:1000]})
            i += 2
    except Exception as e:
        print(f"  [读取失败] {path}: {e}")
    return messages


def _parse_clacky_json(path: Path, last_count: int) -> tuple[list[dict], int]:
    messages = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        all_msgs = data.get("messages", [])
        total = len(all_msgs)
        for msg in all_msgs[last_count:]:
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
            content_raw = msg.get("content", "")
            if isinstance(content_raw, list):
                text = "\n".join(
                    p.get("text", "") for p in content_raw
                    if isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text")
                )
            else:
                text = str(content_raw)
            text = text.strip()
            if text:
                messages.append({"role": role, "content": text[:1000]})
        return messages, total
    except Exception as e:
        print(f"  [读取失败] {path}: {e}")
        return [], last_count


# ── Codex parser ──
def _parse_codex_jsonl(path: Path, last_line: int) -> tuple[list[dict], int]:
    messages = []
    line_count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for i, raw in enumerate(f):
                line_count = i + 1
                if i < last_line:
                    continue
                try:
                    obj = json.loads(raw.strip())
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "response_item":
                    continue
                payload = obj.get("payload", {})
                role = payload.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                content_raw = payload.get("content", "")
                if isinstance(content_raw, list):
                    text = "\n".join(
                        p.get("text", "") for p in content_raw
                        if isinstance(p, dict) and p.get("type") in ("input_text", "output_text", "text")
                    )
                else:
                    text = str(content_raw)
                text = text.strip()
                if text:
                    messages.append({"role": role, "content": text[:1000]})
    except Exception as e:
        print(f"  [读取失败] {path}: {e}")
    return messages, line_count


# ── Claude Code parser ──
def _parse_claude_jsonl(path: Path, last_line: int) -> tuple[list[dict], int]:
    messages = []
    line_count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for i, raw in enumerate(f):
                line_count = i + 1
                if i < last_line:
                    continue
                try:
                    obj = json.loads(raw.strip())
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message", {})
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                content_raw = msg.get("content", "")
                if isinstance(content_raw, list):
                    text = "\n".join(
                        p.get("text", "") for p in content_raw
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                else:
                    text = str(content_raw)
                text = text.strip()
                if text:
                    messages.append({"role": role, "content": text[:1000]})
    except Exception as e:
        print(f"  [读取失败] {path}: {e}")
    return messages, line_count


# ── Hermes parser ──
def _parse_hermes_jsonl(path: Path, last_line: int) -> tuple[list[dict], int]:
    messages = []
    line_count = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for i, raw in enumerate(f):
                line_count = i + 1
                if i < last_line:
                    continue
                try:
                    obj = json.loads(raw.strip())
                except json.JSONDecodeError:
                    continue
                role = obj.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                text = str(obj.get("content", "")).strip()
                if text:
                    messages.append({"role": role, "content": text[:1000]})
    except Exception as e:
        print(f"  [读取失败] {path}: {e}")
    return messages, line_count


def post_formation(messages: list[dict]) -> bool:
    payload = {
        "messages": messages,
        "context": {"counterparty": "xz"},
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        resp = requests.post(FORMATION_URL, headers=HEADERS, json=payload, timeout=60)
        resp.raise_for_status()
        return True
    except requests.HTTPError as e:
        print(f"  [Formation 失败] HTTP {e.response.status_code}: {e.response.text[:200]}")
        return False
    except Exception as e:
        print(f"  [Formation 失败] {e}")
        return False


def submit_batches(messages: list[dict], label: str) -> bool:
    if not messages:
        return True
    filtered = [m for m in messages if is_meaningful(m)]
    if not filtered:
        print(f"  [{label}] 全部被过滤，跳过")
        return True

    total_batches = (len(filtered) + BATCH_SIZE - 1) // BATCH_SIZE
    success = 0
    for i in range(0, len(filtered), BATCH_SIZE):
        batch = filtered[i:i + BATCH_SIZE]
        ok = post_formation(batch)
        batch_num = i // BATCH_SIZE + 1
        if ok:
            success += 1
            print(f"  [{label}] 批次 {batch_num}/{total_batches}：提交成功（{len(batch)} 条）")
        else:
            print(f"  [{label}] 批次 {batch_num}/{total_batches}：提交失败")
        time.sleep(1)

    return success == total_batches


def main():
    start_time = time.time()
    state = load_state()
    new_state = dict(state)
    all_ok = True

    total_files = 0
    total_messages = 0
    skipped = 0

    # ── 1. Clacky chunk-md files（无 cutoff，跳过已完成） ──
    sessions_dir = Path.home() / ".clacky" / "sessions"
    if sessions_dir.exists():
        print("\n=== Clacky chunk-md ===")
        for md_file in sorted(sessions_dir.glob("*-chunk-*.md")):
            key = str(md_file)
            if state.get(key):
                skipped += 1
                continue
            msgs = _parse_clacky_chunk_md(md_file)
            if msgs:
                total_files += 1
                total_messages += len(msgs)
                print(f"  {md_file.name}: {len(msgs)} 条消息")
                ok = submit_batches(msgs, md_file.name)
                if ok:
                    new_state[key] = True
                else:
                    all_ok = False

        print("\n=== Clacky JSON sessions ===")
        for json_file in sorted(sessions_dir.glob("*.json")):
            key = str(json_file)
            last = state.get(key, 0)
            msgs, new_count = _parse_clacky_json(json_file, last)
            if msgs:
                total_files += 1
                total_messages += len(msgs)
                print(f"  {json_file.name}: {len(msgs)} 条新消息 (from line {last})")
                ok = submit_batches(msgs, json_file.name)
                if ok:
                    new_state[key] = new_count
                else:
                    all_ok = False
            else:
                skipped += 1

    # ── 2. Claude Code（无 cutoff） ──
    print("\n=== Claude Code ===")
    claude_dirs = [
        Path.home() / ".claude" / "projects",
        Path.home() / ".claude-internal" / "projects",
    ]
    for base in claude_dirs:
        if not base.exists():
            continue
        for project_dir in sorted(base.iterdir()):
            if not project_dir.is_dir():
                continue
            for jsonl_file in sorted(project_dir.glob("*.jsonl")):
                key = str(jsonl_file)
                last = state.get(key, 0)
                msgs, new_last = _parse_claude_jsonl(jsonl_file, last)
                if msgs:
                    total_files += 1
                    total_messages += len(msgs)
                    print(f"  {jsonl_file.name[:36]}: {len(msgs)} 条新消息")
                    ok = submit_batches(msgs, jsonl_file.name[:36])
                    if ok:
                        new_state[key] = new_last
                    else:
                        all_ok = False
                else:
                    skipped += 1

    # ── 3. Codex（无 cutoff） ──
    print("\n=== Codex ===")
    codex_dir = Path.home() / ".codex" / "archived_sessions"
    if codex_dir.exists():
        for jsonl_file in sorted(codex_dir.glob("*.jsonl")):
            key = str(jsonl_file)
            last = state.get(key, 0)
            msgs, new_last = _parse_codex_jsonl(jsonl_file, last)
            if msgs:
                total_files += 1
                total_messages += len(msgs)
                print(f"  {jsonl_file.name[:36]}: {len(msgs)} 条新消息")
                ok = submit_batches(msgs, jsonl_file.name[:36])
                if ok:
                    new_state[key] = new_last
                else:
                    all_ok = False
            else:
                skipped += 1

    # ── 4. Hermes（无 cutoff） ──
    print("\n=== Hermes ===")
    hermes_dirs = [
        Path.home() / ".hermes" / "hermes-agent" / "sessions",
        Path.home() / ".hermes" / "sessions",
    ]
    for hermes_dir in hermes_dirs:
        if not hermes_dir.exists():
            continue
        for jsonl_file in sorted(hermes_dir.glob("*.jsonl")):
            key = str(jsonl_file)
            last = state.get(key, 0)
            msgs, new_last = _parse_hermes_jsonl(jsonl_file, last)
            if msgs:
                total_files += 1
                total_messages += len(msgs)
                print(f"  {jsonl_file.name[:36]}: {len(msgs)} 条新消息")
                ok = submit_batches(msgs, jsonl_file.name[:36])
                if ok:
                    new_state[key] = new_last
                else:
                    all_ok = False
            else:
                skipped += 1

    # ── 保存状态 ──
    force_save = "--force-save" in sys.argv
    if all_ok or force_save:
        save_state(new_state)
        print(f"\n[完成] 处理 {total_files} 个文件，{total_messages} 条消息，跳过 {skipped} 个已完成")
        if not all_ok:
            print(f"  （force-save 模式，部分批次失败但状态已保存）")
    else:
        print(f"\n[警告] 部分批次失败，状态未保存（下次可重试，或加 --force-save 参数）")
        print(f"  已处理 {total_files} 个文件，{total_messages} 条消息")

    elapsed = round(time.time() - start_time, 1)
    print(f"  耗时: {elapsed}s")


if __name__ == "__main__":
    main()
