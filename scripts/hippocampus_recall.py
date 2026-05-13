#!/usr/bin/env python3
"""
hippocampus_recall.py
海马体 Recall：对话记忆召回
查询 Notion 海马体数据库 → 生成可注入下次对话的上下文摘要
用法：python3 scripts/hippocampus_recall.py [--query "关键词"] [--type preference]
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from notion_client import Client

load_dotenv(Path(__file__).parent.parent / ".env")

NOTION_KEY  = os.environ["NOTION_API_KEY"]
HIPPO_DB_ID = os.environ["NOTION_HIPPO_DB_ID"]

notion = Client(auth=NOTION_KEY)

MEMORY_TYPES = ["preference", "decision", "project", "failure", "personal"]


# ── 查询记忆 ──────────────────────────────────────────────
def query_memories(
    keywords: list[str] = None,
    memory_type: str = None,
    limit: int = 20,
) -> list[dict]:
    """从 Notion 查询记忆条目"""
    filters = []

    if memory_type and memory_type in MEMORY_TYPES:
        filters.append({
            "property": "memory_type",
            "select": {"equals": memory_type}
        })

    if keywords:
        # 对每个关键词搜索 keywords 字段
        kw_filters = [
            {"property": "keywords", "multi_select": {"contains": kw}}
            for kw in keywords[:3]
        ]
        if len(kw_filters) == 1:
            filters.extend(kw_filters)
        else:
            filters.append({"or": kw_filters})

    query_params = {
        "database_id": HIPPO_DB_ID,
        "sorts": [{"property": "created_at", "direction": "descending"}],
        "page_size": limit,
    }
    if filters:
        query_params["filter"] = {"and": filters} if len(filters) > 1 else filters[0]

    results = notion.databases.query(**query_params).get("results", [])
    memories = []
    for r in results:
        props = r.get("properties", {})
        content_rt = props.get("content", {}).get("rich_text", [])
        content = content_rt[0]["plain_text"] if content_rt else ""
        mtype_sel = props.get("memory_type", {}).get("select")
        mtype = mtype_sel["name"] if mtype_sel else "personal"
        keywords_ms = [k["name"] for k in props.get("keywords", {}).get("multi_select", [])]
        conf_num = props.get("confidence", {}).get("number", 0.8)
        date_obj = props.get("created_at", {}).get("date")
        created = date_obj["start"][:10] if date_obj else ""
        memories.append({
            "content": content,
            "type": mtype,
            "keywords": keywords_ms,
            "confidence": conf_num,
            "created_at": created,
        })
    return memories


# ── 生成注入摘要 ──────────────────────────────────────────
TYPE_LABELS = {
    "preference": "偏好/习惯",
    "decision":   "历史决策",
    "project":    "项目背景",
    "failure":    "失败经验",
    "personal":   "个人信息",
}

def format_recall_context(memories: list[dict]) -> str:
    """把记忆列表格式化成可直接粘贴进对话的上下文块"""
    if not memories:
        return "（暂无相关记忆）"

    grouped: dict[str, list[str]] = {}
    for m in memories:
        t = m["type"]
        grouped.setdefault(t, []).append(m["content"])

    lines = ["## 关于用户的已知信息（海马体记忆）\n"]
    for mtype in MEMORY_TYPES:
        if mtype not in grouped:
            continue
        lines.append(f"### {TYPE_LABELS.get(mtype, mtype)}")
        for item in grouped[mtype]:
            lines.append(f"- {item}")
        lines.append("")

    lines.append(f"_（共 {len(memories)} 条，最新更新：{datetime.now().strftime('%Y-%m-%d')}）_")
    return "\n".join(lines)


# ── CLI 入口 ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="海马体 Recall：召回对话记忆")
    parser.add_argument("--query",  "-q", help="关键词（空格分隔多个）", default="")
    parser.add_argument("--type",   "-t", help=f"记忆类型：{' | '.join(MEMORY_TYPES)}", default="")
    parser.add_argument("--limit",  "-n", help="最多返回条数（默认20）", type=int, default=20)
    parser.add_argument("--json",         help="输出原始 JSON", action="store_true")
    args = parser.parse_args()

    keywords = args.query.split() if args.query else None
    memory_type = args.type if args.type in MEMORY_TYPES else None

    memories = query_memories(keywords=keywords, memory_type=memory_type, limit=args.limit)

    if args.json:
        print(json.dumps(memories, ensure_ascii=False, indent=2))
    else:
        print(format_recall_context(memories))


if __name__ == "__main__":
    main()
