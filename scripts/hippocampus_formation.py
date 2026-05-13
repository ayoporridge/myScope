#!/usr/bin/env python3
"""
hippocampus_formation.py
海马体 Formation：对话记忆提取
读取 Claude Code 对话日志 → DeepSeek 提取实体/偏好/决策 → 写入 Notion 海马体数据库
每天凌晨 06:00 运行（三层记忆跑完后）
"""

import os
import json
import re
import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from notion_client import Client
from openai import OpenAI

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_KEY    = os.environ["NOTION_API_KEY"]
HIPPO_DB_ID   = os.environ["NOTION_HIPPO_DB_ID"]
DEEPSEEK_KEY  = os.environ["DEEPSEEK_API_KEY"]

notion = Client(auth=NOTION_KEY)
llm    = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com")

# Claude Code 对话日志路径
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# 状态文件：记录已处理的日志行位置
STATE_FILE = Path(__file__).parent.parent / ".hippo_state.json"


# ── 状态持久化 ────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── 读取 Claude 对话日志 ──────────────────────────────────
def extract_messages_from_jsonl(path: Path, last_line: int = 0) -> tuple[list[dict], int]:
    """
    读取 .jsonl 文件，从 last_line 行开始。
    返回 (messages, new_last_line)
    messages 是 {role, content} 列表
    """
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
                # Claude Code jsonl 格式：obj.message.role / obj.message.content
                msg = obj.get("message", {})
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                content_raw = msg.get("content", "")
                # content 可能是字符串或列表
                if isinstance(content_raw, list):
                    text_parts = []
                    for part in content_raw:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text_parts.append(part.get("text", ""))
                    content = "\n".join(text_parts)
                else:
                    content = str(content_raw)
                content = content.strip()
                if content:
                    messages.append({"role": role, "content": content[:1000]})
    except Exception as e:
        print(f"  [读取失败] {path}: {e}")
    return messages, line_count


def collect_new_messages(state: dict) -> list[dict]:
    """
    扫描所有 Claude 项目目录，收集自上次同步以来的新消息。
    只处理最近 24 小时内有修改的文件。
    """
    all_messages = []
    cutoff = datetime.now() - timedelta(hours=25)
    new_state = dict(state)

    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl_file in project_dir.glob("*.jsonl"):
            mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime)
            if mtime < cutoff:
                continue
            file_key = str(jsonl_file)
            last_line = state.get(file_key, 0)
            messages, new_last = extract_messages_from_jsonl(jsonl_file, last_line)
            if messages:
                all_messages.extend(messages)
                print(f"  [{jsonl_file.name[:30]}] +{len(messages)} 条消息")
            new_state[file_key] = new_last

    save_state(new_state)
    return all_messages


# ── DeepSeek 提取记忆 ─────────────────────────────────────
FORMATION_PROMPT = """\
你是一个个人记忆提取助手。请从下面的对话片段中提取值得长期记住的信息。

关注以下类型：
1. **偏好/习惯**：用户明确表达的喜好、不喜欢的东西、工作方式
2. **决策**：用户做出的重要选择，以及选择原因
3. **项目背景**：正在做的项目、技术栈、当前状态
4. **失败经验**：尝试过但失败的方案（避免下次重复建议）
5. **个人信息**：姓名、职业、技能等基本信息

不要提取：
- 具体的代码细节（除非是架构级决策）
- 临时性问题（如"这个 bug 怎么修"）
- 通用知识讨论（无关用户个人的内容）

输出 JSON 数组，每个对象：
- "memory_type": "preference" | "decision" | "project" | "failure" | "personal"
- "content": 一句话描述（≤80字）
- "keywords": 关键词列表（2-4个）
- "confidence": 0.0-1.0（提取的把握程度）

只输出 JSON，不要其他文字。

【对话片段】
{conversation}
"""

def extract_memories(messages: list[dict]) -> list[dict]:
    """用 DeepSeek 从消息列表提取记忆"""
    if not messages:
        return []

    # 把消息拼成对话格式，限制总长度
    conv_lines = []
    total_chars = 0
    for m in messages:
        line = f"[{m['role']}]: {m['content']}"
        total_chars += len(line)
        if total_chars > 6000:
            break
        conv_lines.append(line)
    conversation = "\n".join(conv_lines)

    try:
        resp = llm.chat.completions.create(
            model="deepseek-chat",
            messages=[{
                "role": "user",
                "content": FORMATION_PROMPT.format(conversation=conversation)
            }],
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        memories = json.loads(match.group())
        # 过滤低置信度
        return [m for m in memories if m.get("confidence", 0) >= 0.6]
    except Exception as e:
        print(f"  [DeepSeek 提取失败] {e}")
        return []


# ── 写入 Notion 海马体数据库 ──────────────────────────────
def memory_exists(memory_id: str) -> bool:
    results = notion.databases.query(
        database_id=HIPPO_DB_ID,
        filter={"property": "memory_id", "rich_text": {"equals": memory_id}}
    ).get("results", [])
    return len(results) > 0

def write_memory(memory: dict):
    content = memory.get("content", "")
    memory_id = hashlib.md5(content.encode()).hexdigest()
    if memory_exists(memory_id):
        return False

    notion.pages.create(
        parent={"database_id": HIPPO_DB_ID},
        properties={
            "Name":        {"title": [{"text": {"content": content[:80]}}]},
            "memory_id":   {"rich_text": [{"text": {"content": memory_id}}]},
            "memory_type": {"select": {"name": memory.get("memory_type", "personal")}},
            "content":     {"rich_text": [{"text": {"content": content}}]},
            "keywords":    {"multi_select": [{"name": k} for k in memory.get("keywords", [])[:4]]},
            "confidence":  {"number": memory.get("confidence", 0.8)},
            "created_at":  {"date": {"start": datetime.now().isoformat()}},
        }
    )
    return True


# ── 主流程 ────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始海马体 Formation")

    state = load_state()
    messages = collect_new_messages(state)
    print(f"  收集到 {len(messages)} 条新消息")

    if not messages:
        print("  无新消息，跳过")
        return

    # 每 30 条消息一批提取（避免 token 过长）
    batch_size = 30
    total_written = 0
    for i in range(0, len(messages), batch_size):
        batch = messages[i:i + batch_size]
        memories = extract_memories(batch)
        print(f"  批次 {i//batch_size + 1}：提取 {len(memories)} 条记忆")
        for mem in memories:
            if write_memory(mem):
                total_written += 1
                time.sleep(0.3)

    print(f"[完成] 写入 {total_written} 条新记忆")


if __name__ == "__main__":
    main()
