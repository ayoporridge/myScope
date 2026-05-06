#!/usr/bin/env python3
"""
layer2_wiki.py
第二层：结构记忆 (LLM Wiki)
Notion 最近内容 → DeepSeek 归纳/结构化 → Notion Wiki 数据库
每天凌晨 5:30 运行（第一层跑完后）
"""

import os
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from notion_client import Client
from openai import OpenAI

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_KEY   = os.environ["NOTION_API_KEY"]
WIKI_DB_ID   = os.environ["NOTION_WIKI_DB_ID"]
RAG_DB_ID    = os.environ["NOTION_RAG_DB_ID"]
DEEPSEEK_KEY = os.environ["DEEPSEEK_API_KEY"]

notion = Client(auth=NOTION_KEY)
llm    = OpenAI(api_key=DEEPSEEK_KEY, base_url="https://api.deepseek.com")


# ── 从 RAG DB 拉取最近切片 ────────────────────────────────
def get_recent_chunks(hours=25) -> list[dict]:
    """获取最近 N 小时写入的 RAG 切片"""
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat() + "Z"
    results = notion.databases.query(
        database_id=RAG_DB_ID,
        filter={
            "property": "created_at",
            "date": {"on_or_after": cutoff}
        },
        sorts=[{"property": "created_at", "direction": "ascending"}]
    ).get("results", [])

    chunks = []
    for r in results:
        props = r.get("properties", {})
        content_rt = props.get("content", {}).get("rich_text", [])
        content = content_rt[0]["plain_text"] if content_rt else ""
        keywords = [k["name"] for k in props.get("keywords", {}).get("multi_select", [])]
        chunks.append({"content": content, "keywords": keywords})
    return chunks


# ── 查询已有 Wiki 条目 ────────────────────────────────────
def get_existing_wiki_entries() -> list[dict]:
    results = notion.databases.query(database_id=WIKI_DB_ID).get("results", [])
    entries = []
    for r in results:
        props = r.get("properties", {})
        title_rt = props.get("Name", {}).get("title", [])
        title = title_rt[0]["plain_text"] if title_rt else ""
        tags = [t["name"] for t in props.get("tags", {}).get("multi_select", [])]
        entries.append({"id": r["id"], "title": title, "tags": tags})
    return entries

def update_wiki_entry(page_id: str, new_content: str):
    """更新已有 Wiki 条目的正文"""
    notion.blocks.children.append(
        block_id=page_id,
        children=[{
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": f"\n---\n[{datetime.now().strftime('%Y-%m-%d')} 更新]\n{new_content[:1500]}"}
                }]
            }
        }]
    )

def create_wiki_entry(title: str, content: str, tags: list[str]):
    """创建新 Wiki 条目"""
    notion.pages.create(
        parent={"database_id": WIKI_DB_ID},
        properties={
            "Name": {"title": [{"text": {"content": title}}]},
            "tags": {"multi_select": [{"name": t} for t in tags[:8]]},
            "last_updated": {"date": {"start": datetime.now().isoformat()}},
        },
        children=[{
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": content[:2000]}}]
            }
        }]
    )


# ── DeepSeek Wiki 归纳 ────────────────────────────────────
WIKI_PROMPT = """\
你是一个个人知识库维护助手。

下面是今天新增的知识碎片列表，以及已有的 Wiki 条目标题（用于判断是新建还是合并）。

【今日新增碎片】
{chunks}

【现有 Wiki 条目】（仅标题）
{existing_titles}

请分析这些碎片，输出一个 JSON 数组，每个对象代表一个 Wiki 操作：
- "action": "create"（新建条目）或 "update"（更新已有条目）
- "title": 条目标题
- "content": 要写入的内容（结构化散文，最多300字）
- "tags": 标签列表（3-6个）
- "merge_with": 若 action=update，写已有条目的精确标题；若 action=create 则为 null

原则：
- 相关度高的碎片合并进同一条目
- 已有条目有新信息时 action=update
- 全新主题时 action=create
- 只输出 JSON，不要其他文字
"""

def plan_wiki_updates(chunks: list[dict], existing: list[dict]) -> list[dict]:
    chunks_text = "\n".join(f"- {c['content']}" for c in chunks[:50])
    titles_text = "\n".join(f"- {e['title']}" for e in existing[:100])

    resp = llm.chat.completions.create(
        model="deepseek-chat",
        messages=[{
            "role": "user",
            "content": WIKI_PROMPT.format(
                chunks=chunks_text,
                existing_titles=titles_text
            )
        }],
        temperature=0.3,
    )
    raw = resp.choices[0].message.content.strip()
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    return json.loads(match.group())


# ── 主流程 ────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始第二层 LLM Wiki 更新")

    chunks = get_recent_chunks(hours=25)
    if not chunks:
        print("  没有新切片，跳过")
        return
    print(f"  发现 {len(chunks)} 条新切片")

    existing = get_existing_wiki_entries()
    print(f"  现有 Wiki 条目：{len(existing)} 条")

    operations = plan_wiki_updates(chunks, existing)
    print(f"  DeepSeek 规划了 {len(operations)} 个 Wiki 操作")

    existing_map = {e["title"]: e["id"] for e in existing}

    for op in operations:
        action  = op.get("action")
        title   = op.get("title", "")
        content = op.get("content", "")
        tags    = op.get("tags", [])

        if not title or not content:
            continue

        if action == "update":
            merge_title = op.get("merge_with") or title
            page_id = existing_map.get(merge_title)
            if page_id:
                update_wiki_entry(page_id, content)
                print(f"  [更新] {merge_title[:40]}")
            else:
                # 找不到就新建
                create_wiki_entry(title, content, tags)
                print(f"  [新建(fallback)] {title[:40]}")
        elif action == "create":
            create_wiki_entry(title, content, tags)
            print(f"  [新建] {title[:40]}")

        time.sleep(0.5)  # Notion API 限速

    print("[完成] Wiki 更新结束")


if __name__ == "__main__":
    main()
