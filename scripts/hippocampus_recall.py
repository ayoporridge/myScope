#!/usr/bin/env python3
"""
hippocampus_recall.py
海马体 Recall：对话记忆召回
查询 Anda Hippocampus Recall API → 生成可注入下次对话的上下文摘要
用法：python3 scripts/hippocampus_recall.py [--query "关键词"] [--limit 10]
"""

import os
import sys
import json
import argparse
from datetime import datetime
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

ANDA_BASE_URL    = os.environ["ANDA_BASE_URL"]
ANDA_SPACE_ID    = os.environ["ANDA_SPACE_ID"]
ANDA_SPACE_TOKEN = os.environ["ANDA_SPACE_TOKEN"]

RECALL_URL = f"{ANDA_BASE_URL}/v1/{ANDA_SPACE_ID}/recall"
HEADERS = {
    "Authorization": f"Bearer {ANDA_SPACE_TOKEN}",
    "Content-Type": "application/json",
}


# ── 查询记忆 ──────────────────────────────────────────────
def recall(query: str, limit: int = 20) -> str:
    """返回 Anda 生成的 Markdown 字符串，失败返回空字符串。"""
    payload = {
        "query": query,
        "context": {"counterparty": "xz"},
        "top_k": limit,
    }
    try:
        resp = requests.post(RECALL_URL, headers=HEADERS, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        # Anda 返回格式：{"result": {"content": "<markdown>"}}
        return data.get("result", {}).get("content", "")
    except requests.HTTPError as e:
        print(f"[Recall 失败] HTTP {e.response.status_code}: {e.response.text[:200]}", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"[Recall 失败] {e}", file=sys.stderr)
        return ""


# ── 格式化输出 ────────────────────────────────────────────
def format_recall_context(content: str) -> str:
    if not content:
        return "（暂无相关记忆）"
    return f"## 关于用户的已知信息（海马体记忆）\n\n{content}\n\n_查询时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}_"


# ── CLI 入口 ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="海马体 Recall：召回对话记忆")
    parser.add_argument("--query",  "-q", help="查询语句（自然语言）", default="用户的偏好、项目背景和重要决策")
    parser.add_argument("--limit",  "-n", help="最多返回条数（默认20）", type=int, default=20)
    parser.add_argument("--json",         help="输出原始 JSON", action="store_true")
    args = parser.parse_args()

    content = recall(query=args.query, limit=args.limit)

    if args.json:
        print(json.dumps({"content": content}, ensure_ascii=False, indent=2))
    else:
        print(format_recall_context(content))


if __name__ == "__main__":
    main()
