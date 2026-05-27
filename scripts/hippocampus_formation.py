#!/usr/bin/env python3
"""
hippocampus_formation.py
海马体 Formation：多源对话记忆提取
来源：Claude Code（手动+SDK）、Codex、Hermes、Clacky
每天凌晨 06:00 运行
"""

import os
import re
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
import requests
from dotenv import load_dotenv
from _metrics import record_last_run, record_metrics

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

# 25小时窗口：覆盖昨天 06:00 到今天 06:00，留 1 小时余量
CUTOFF_HOURS = 25


# ── 有效消息过滤器 ────────────────────────────────────────
def is_meaningful(msg: dict) -> bool:
    """判断一条消息是否有信息量，过滤系统 prompt、工具调用、XML 注入等噪音。"""
    text = msg.get("content", "")
    role = msg.get("role", "")

    # 1. 长度在合理范围（太短是工具调用，太长是系统 prompt）
    if not (20 <= len(text) <= 800):
        return False

    # 2. 不以 XML 结构开头（系统注入）
    if text.strip().startswith("<"):
        return False

    # 3. 不包含大段 JSON 结构（工具定义/调用结果）
    if text.count("{") > 5 and text.count("}") > 5:
        return False

    # 4. user 消息：必须有实质内容
    if role == "user" and len(text) < 10:
        return False

    return True


# ── 状态持久化 ────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── 来源一：Claude Code（~/.claude 和 ~/.claude-internal） ──
def _parse_claude_jsonl(path: Path, last_line: int) -> tuple[list[dict], int]:
    """解析 Claude Code 的 JSONL 会话文件，返回 (messages, 新行数)。"""
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


def collect_claude_messages(state: dict, new_state: dict, cutoff: datetime) -> list[dict]:
    all_messages = []
    dirs = [
        Path.home() / ".claude" / "projects",
        Path.home() / ".claude-internal" / "projects",
    ]
    for base in dirs:
        if not base.exists():
            continue
        for project_dir in base.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl_file in project_dir.glob("*.jsonl"):
                if datetime.fromtimestamp(jsonl_file.stat().st_mtime) < cutoff:
                    continue
                key = str(jsonl_file)
                msgs, new_last = _parse_claude_jsonl(jsonl_file, state.get(key, 0))
                if msgs:
                    all_messages.extend(msgs)
                    print(f"  [claude] {jsonl_file.name[:36]} +{len(msgs)}")
                new_state[key] = new_last
    return all_messages


# ── 来源二：Codex（~/.codex/archived_sessions/*.jsonl） ────
def _parse_codex_jsonl(path: Path, last_line: int) -> tuple[list[dict], int]:
    """解析 Codex session 文件，只提取 response_item 里的 user/assistant 消息。"""
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


def collect_codex_messages(state: dict, new_state: dict, cutoff: datetime) -> list[dict]:
    all_messages = []
    sessions_dir = Path.home() / ".codex" / "archived_sessions"
    if not sessions_dir.exists():
        return all_messages
    for jsonl_file in sessions_dir.glob("*.jsonl"):
        if datetime.fromtimestamp(jsonl_file.stat().st_mtime) < cutoff:
            continue
        key = str(jsonl_file)
        msgs, new_last = _parse_codex_jsonl(jsonl_file, state.get(key, 0))
        if msgs:
            all_messages.extend(msgs)
            print(f"  [codex]  {jsonl_file.name[:36]} +{len(msgs)}")
        new_state[key] = new_last
    return all_messages


# ── 来源三：Hermes（~/.hermes/sessions/*.jsonl） ────────────
def _parse_hermes_jsonl(path: Path, last_line: int) -> tuple[list[dict], int]:
    """解析 Hermes session 文件，格式最干净：{role, content, timestamp}。"""
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


def collect_hermes_messages(state: dict, new_state: dict, cutoff: datetime) -> list[dict]:
    all_messages = []
    sessions_dir = Path.home() / ".hermes" / "sessions"
    if not sessions_dir.exists():
        return all_messages
    for jsonl_file in sessions_dir.glob("*.jsonl"):
        if datetime.fromtimestamp(jsonl_file.stat().st_mtime) < cutoff:
            continue
        key = str(jsonl_file)
        msgs, new_last = _parse_hermes_jsonl(jsonl_file, state.get(key, 0))
        if msgs:
            all_messages.extend(msgs)
            print(f"  [hermes] {jsonl_file.name[:36]} +{len(msgs)}")
        new_state[key] = new_last
    return all_messages


# ── 来源四：Clacky（~/.clacky/sessions/） ─────────────────
def _parse_clacky_json(path: Path, last_count: int) -> tuple[list[dict], int]:
    """解析 Clacky session JSON（未压缩时消息直接存在 messages 数组里）。"""
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


def _parse_clacky_chunk_md(path: Path) -> list[dict]:
    """解析 Clacky 压缩归档的 chunk markdown（## User / ## Assistant 分段）。"""
    messages = []
    try:
        content = path.read_text(encoding="utf-8")
        parts = re.split(r'\n## (User|Assistant)\n', content)
        # parts[0] = 文件头，之后奇数位=角色，偶数位=内容
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


def collect_clacky_messages(state: dict, new_state: dict, cutoff: datetime) -> list[dict]:
    all_messages = []
    sessions_dir = Path.home() / ".clacky" / "sessions"
    if not sessions_dir.exists():
        return all_messages

    # ① 普通 JSON session（消息未压缩，按 count 增量读取）
    for json_file in sessions_dir.glob("*.json"):
        if datetime.fromtimestamp(json_file.stat().st_mtime) < cutoff:
            continue
        key = str(json_file)
        msgs, new_count = _parse_clacky_json(json_file, state.get(key, 0))
        if msgs:
            all_messages.extend(msgs)
            print(f"  [clacky] {json_file.name[:36]} +{len(msgs)}")
        new_state[key] = new_count

    # ② chunk-N.md 压缩归档（不可变，处理一次后跳过）
    for md_file in sessions_dir.glob("*-chunk-*.md"):
        if datetime.fromtimestamp(md_file.stat().st_mtime) < cutoff:
            continue
        key = str(md_file)
        if state.get(key):
            continue
        msgs = _parse_clacky_chunk_md(md_file)
        if msgs:
            all_messages.extend(msgs)
            print(f"  [clacky] {md_file.name[:36]} +{len(msgs)}")
        new_state[key] = True

    return all_messages


# ── 提交到 Anda Formation API ─────────────────────────────
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


# ── 主流程 ────────────────────────────────────────────────
def main():
    start_time = time.time()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始海马体 Formation")

    state = load_state()
    new_state = dict(state)
    cutoff = datetime.now() - timedelta(hours=CUTOFF_HOURS)

    all_messages = []
    all_messages += collect_claude_messages(state, new_state, cutoff)
    all_messages += collect_codex_messages(state, new_state, cutoff)
    all_messages += collect_hermes_messages(state, new_state, cutoff)
    all_messages += collect_clacky_messages(state, new_state, cutoff)

    raw_count = len(all_messages)
    print(f"  收集到 {raw_count} 条原始消息")

    # 有效消息过滤
    all_messages = [m for m in all_messages if is_meaningful(m)]
    filtered_count = raw_count - len(all_messages)
    print(f"  过滤噪音：{filtered_count} 条 → 剩余 {len(all_messages)} 条有效消息")

    if not all_messages:
        print("  无有效消息，跳过")
        save_state(new_state)
        record_last_run("hippocampus_formation")
        record_metrics(
            "hippocampus_formation",
            total_messages=raw_count,
            meaningful_messages=0,
            filtered_messages=filtered_count,
            batches_success=0,
            batches_total=0,
            run_duration_seconds=round(time.time() - start_time, 1),
        )
        return

    batch_size = 40
    success = 0
    total_batches = (len(all_messages) + batch_size - 1) // batch_size
    for i in range(0, len(all_messages), batch_size):
        batch = all_messages[i:i + batch_size]
        ok = post_formation(batch)
        batch_num = i // batch_size + 1
        if ok:
            success += 1
            print(f"  批次 {batch_num}/{total_batches}：提交成功（{len(batch)} 条）")
        else:
            print(f"  批次 {batch_num}/{total_batches}：提交失败")
        time.sleep(1)

    if success == total_batches:
        save_state(new_state)
        print(f"[完成] 共提交 {success} 批，{len(all_messages)} 条消息")
    else:
        print(f"[警告] 有 {total_batches - success} 批失败，状态未保存（下次会重试）")

    # 记录指标
    record_last_run("hippocampus_formation")
    record_metrics(
        "hippocampus_formation",
        total_messages=raw_count,
        meaningful_messages=len(all_messages),
        filtered_messages=filtered_count,
        batches_success=success,
        batches_total=total_batches,
        run_duration_seconds=round(time.time() - start_time, 1),
    )


if __name__ == "__main__":
    main()
