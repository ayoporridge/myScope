#!/usr/bin/env python3
"""
layer2_wiki.py
第二层：结构记忆 (LLM Wiki)
Meilisearch memory_chunks → Xiaomi mimo 归纳/结构化 → Meilisearch wiki_entries
每天凌晨 5:30 运行（第一层跑完后）
"""

import os
import json
import re
import hashlib
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import requests
from _metrics import record_last_run, record_metrics

load_dotenv(Path(__file__).parent.parent / ".env")

XIAOMI_KEY   = os.environ["XIAOMI_API_KEY"]
MEMORY_URL   = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "memory-api-token-2026")

HEADERS = {
    "Authorization": f"Bearer {MEMORY_TOKEN}",
    "Content-Type": "application/json",
}

llm = OpenAI(api_key=XIAOMI_KEY, base_url="https://token-plan-cn.xiaomimimo.com/v1")


# ── 从 Meilisearch 读取最近切片 ──────────────────────────────
def get_recent_chunks() -> list[dict]:
    """获取 memory_chunks 中最近 25 小时的切片（简单做法：搜索全量取最近的）"""
    try:
        # 用一个宽泛查询取最近文档（memory-api 应该支持按时间排序）
        # 如果 memory-api 不支持列举，用空查询
        r = requests.get(
            f"{MEMORY_URL}/search",
            params={"q": "", "index": "memory_chunks", "limit": 200},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        # 过滤最近 25 小时的（通过 created_at 字段）
        cutoff = (datetime.now().timestamp() - 25 * 3600)
        recent = []
        for item in results:
            created = item.get("created_at", "")
            if created:
                try:
                    ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                    if ts >= cutoff:
                        recent.append(item)
                        continue
                except (ValueError, TypeError):
                    pass
            # 如果没有时间戳字段，默认包含
            recent.append(item)
        return recent
    except Exception as e:
        print(f"  [读取 memory_chunks 失败] {e}")
        return []


# ── 获取已有 Wiki 条目 ───────────────────────────────────────
def get_existing_wiki_titles() -> list[str]:
    """获取现有 wiki_entries 的标题列表"""
    try:
        r = requests.get(
            f"{MEMORY_URL}/search",
            params={"q": "", "index": "wiki_entries", "limit": 200},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return [item.get("title", "") for item in results if item.get("title")]
    except Exception:
        return []


# ── LLM Wiki 归纳 ───────────────────────────────────────────
WIKI_PROMPT = """\
你是一个个人知识库维护助手。

下面是今天新增的知识碎片列表，以及已有的 Wiki 条目标题（用于判断是新建还是合并）。

【今日新增碎片】
{chunks}

【现有 Wiki 条目】（仅标题）
{existing_titles}

请分析这些碎片，输出一个 JSON 数组，每个对象代表一个 Wiki 条目：
- "title": 条目标题（简洁，5-15字）
- "content": 结构化内容（300字以内，散文或要点式）
- "tags": 标签列表（3-6个）
- "action": "create"（全新主题）或 "update"（与已有条目相关）

原则：
- 相关度高的碎片合并进同一条目
- 只产出有信息量的条目，不要凑数
- 只输出 JSON，不要其他文字
"""


def plan_wiki(chunks: list[dict], existing_titles: list[str]) -> list[dict]:
    chunks_text = "\n".join(f"- {c.get('content', c.get('text', ''))}" for c in chunks[:50])
    titles_text = "\n".join(f"- {t}" for t in existing_titles[:100]) or "（暂无）"

    try:
        resp = llm.chat.completions.create(
            model="mimo-v2.5",
            messages=[{
                "role": "user",
                "content": WIKI_PROMPT.format(chunks=chunks_text, existing_titles=titles_text)
            }],
            temperature=0.3,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        return json.loads(match.group())
    except Exception as e:
        print(f"  [LLM 归纳失败] {e}")
        return []


# ── 写入 Meilisearch wiki_entries ────────────────────────────
def push_wiki_entries(entries: list[dict]):
    """写入 wiki_entries 索引"""
    if not entries:
        return
    docs = []
    for entry in entries:
        title = entry.get("title", "")
        content = entry.get("content", "")
        if not title or not content:
            continue
        doc_id = hashlib.md5(title.encode()).hexdigest()
        docs.append({
            "id": doc_id,
            "title": title,
            "content": content,
            "tags": entry.get("tags", []),
            "updated_at": datetime.now().isoformat(),
        })

    if not docs:
        return

    try:
        r = requests.post(
            f"{MEMORY_URL}/ingest",
            headers=HEADERS,
            json={"index": "wiki_entries", "documents": docs},
            timeout=30,
        )
        r.raise_for_status()
        count = r.json().get("count", len(docs))
        print(f"  [memory-api] 写入 {count} 条到 wiki_entries")
    except Exception as e:
        print(f"  [写入 wiki_entries 失败] {e}")


# ── 主流程 ────────────────────────────────────────────────
def main():
    start_time = time.time()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始第二层 LLM Wiki 更新")

    chunks = get_recent_chunks()
    if not chunks:
        print("  没有新切片，跳过")
        record_last_run("layer2_wiki")
        record_metrics(
            "layer2_wiki",
            new_chunks_processed=0,
            wiki_entries_written=0,
            run_duration_seconds=round(time.time() - start_time, 1),
        )
        return
    print(f"  发现 {len(chunks)} 条新切片")

    existing_titles = get_existing_wiki_titles()
    print(f"  现有 Wiki 条目：{len(existing_titles)} 条")

    operations = plan_wiki(chunks, existing_titles)
    print(f"  LLM 规划了 {len(operations)} 个 Wiki 条目")

    push_wiki_entries(operations)

    # 记录指标
    record_last_run("layer2_wiki")
    record_metrics(
        "layer2_wiki",
        new_chunks_processed=len(chunks),
        wiki_entries_written=len(operations),
        run_duration_seconds=round(time.time() - start_time, 1),
    )
    print("[完成] Wiki 更新结束")


if __name__ == "__main__":
    main()
