#!/usr/bin/env python3
"""
layer1_rag.py
第一层：事实记忆
Notion 日记/笔记 → DeepSeek 切片 → 写回 Notion RAG 数据库
每天凌晨 5:00 运行
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
from openai import OpenAI  # DeepSeek 兼容 OpenAI SDK

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_KEY     = os.environ["NOTION_API_KEY"]
RAG_DB_ID      = os.environ["NOTION_RAG_DB_ID"]
DEEPSEEK_KEY   = os.environ["DEEPSEEK_API_KEY"]

notion = Client(auth=NOTION_KEY)
llm    = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com")

# 每条切片的最大字符数
CHUNK_MAX = 200


# ── Notion 工具函数 ───────────────────────────────────────
def get_recent_pages(days=1):
    """获取最近 N 天修改的页面（日记库、笔记库等）"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat() + "Z"
    results = notion.search(
        filter={"value": "page", "property": "object"},
        sort={"direction": "descending", "timestamp": "last_edited_time"}
    ).get("results", [])
    return [p for p in results if p.get("last_edited_time", "") >= cutoff]

def page_to_text(page_id: str) -> str:
    """把 Notion 页面的块内容提取为纯文本"""
    blocks = notion.blocks.children.list(block_id=page_id).get("results", [])
    lines = []
    for block in blocks:
        t = block.get("type", "")
        rich = block.get(t, {}).get("rich_text", [])
        text = "".join(r.get("plain_text", "") for r in rich)
        if text.strip():
            lines.append(text.strip())
    return "\n".join(lines)

def rag_chunk_exists(chunk_id: str) -> bool:
    """检查 RAG DB 里是否已存在同 ID 的切片（避免重复写入）"""
    results = notion.databases.query(
        database_id=RAG_DB_ID,
        filter={"property": "chunk_id", "rich_text": {"equals": chunk_id}}
    ).get("results", [])
    return len(results) > 0

def write_chunk_to_notion(chunk: dict):
    """把一条切片写入 Notion RAG 数据库"""
    notion.pages.create(
        parent={"database_id": RAG_DB_ID},
        properties={
            "Name":       {"title": [{"text": {"content": chunk["summary"][:80]}}]},
            "chunk_id":   {"rich_text": [{"text": {"content": chunk["chunk_id"]}}]},
            "content":    {"rich_text": [{"text": {"content": chunk["content"]}}]},
            "keywords":   {"multi_select": [{"name": k} for k in chunk["keywords"][:5]]},
            "source":     {"rich_text": [{"text": {"content": chunk["source"]}}]},
            "created_at": {"date": {"start": chunk["created_at"]}},
        }
    )


# ── DeepSeek 切片 ─────────────────────────────────────────
SLICE_PROMPT = """\
你是一个个人知识管理助手。请将下面这段文字切分成若干独立的记忆碎片。

要求：
- 每条碎片聚焦一个事实、想法或事件，不超过 {max_chars} 字
- 输出 JSON 数组，每个对象包含：
  - "content": 碎片正文
  - "summary": 一句话摘要（20字以内）
  - "keywords": 关键词列表（3-5个）
- 过于零散或无意义的内容直接丢弃
- 只输出 JSON，不要其他文字

文字：
{text}
"""

def slice_text(text: str, source: str) -> list[dict]:
    """用 DeepSeek 把长文本切成记忆碎片"""
    if len(text) < 30:
        return []
    try:
        resp = llm.chat.completions.create(
            model="deepseek-chat",
            messages=[{
                "role": "user",
                "content": SLICE_PROMPT.format(text=text[:3000], max_chars=CHUNK_MAX)
            }],
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
        # 提取 JSON 数组
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        chunks = json.loads(match.group())
        now = datetime.now().isoformat()
        result = []
        for c in chunks:
            if not c.get("content"):
                continue
            chunk_id = hashlib.md5(c["content"].encode()).hexdigest()
            result.append({
                "chunk_id":   chunk_id,
                "content":    c["content"],
                "summary":    c.get("summary", ""),
                "keywords":   c.get("keywords", []),
                "source":     source,
                "created_at": now,
            })
        return result
    except Exception as e:
        print(f"  [DeepSeek 切片失败] {e}")
        return []


# ── 主流程 ────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始第一层 RAG 切片")

    pages = get_recent_pages(days=1)
    print(f"  发现 {len(pages)} 个最近修改的页面")

    total_chunks = 0
    for page in pages:
        page_id    = page["id"]
        page_title = page.get("properties", {}).get("title", {}).get("title", [])
        title      = page_title[0]["plain_text"] if page_title else page_id

        text = page_to_text(page_id)
        if not text.strip():
            continue

        print(f"  处理: {title[:40]}")
        chunks = slice_text(text, source=f"notion:{page_id}")

        written = 0
        for chunk in chunks:
            if not rag_chunk_exists(chunk["chunk_id"]):
                write_chunk_to_notion(chunk)
                written += 1
                time.sleep(0.3)  # Notion API 限速

        print(f"    写入 {written}/{len(chunks)} 条切片")
        total_chunks += written

    print(f"[完成] 共写入 {total_chunks} 条新切片")


if __name__ == "__main__":
    main()
