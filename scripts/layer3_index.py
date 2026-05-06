#!/usr/bin/env python3
"""
layer3_index.py
第三层：哈勃半径
FreshRSS 新内容 → Meilisearch 索引
每天凌晨定时运行，也可手动执行
"""

import os
import json
import hashlib
import time
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env
load_dotenv(Path(__file__).parent.parent / ".env")

FRESHRSS_URL      = os.environ["FRESHRSS_URL"]
FRESHRSS_USERNAME = os.environ["FRESHRSS_USERNAME"]
FRESHRSS_API_PASS = os.environ["FRESHRSS_API_PASSWORD"]
MEILI_URL         = "http://localhost:7700"
MEILI_KEY         = os.environ["MEILI_MASTER_KEY"]
INDEX_NAME        = "hubble"

# ── Meilisearch 客户端 ────────────────────────────────────
def meili_headers():
    return {"Authorization": f"Bearer {MEILI_KEY}", "Content-Type": "application/json"}

def ensure_index():
    """确保索引存在，设置可搜索字段"""
    r = requests.get(f"{MEILI_URL}/indexes/{INDEX_NAME}", headers=meili_headers())
    if r.status_code == 404:
        requests.post(
            f"{MEILI_URL}/indexes",
            headers=meili_headers(),
            json={"uid": INDEX_NAME, "primaryKey": "id"}
        )
        # 配置可搜索字段和排序
        requests.patch(
            f"{MEILI_URL}/indexes/{INDEX_NAME}/settings",
            headers=meili_headers(),
            json={
                "searchableAttributes": ["title", "content", "feed_title", "author"],
                "filterableAttributes": ["feed_category", "feed_title", "published_at"],
                "sortableAttributes": ["published_at"],
                "rankingRules": [
                    "words", "typo", "proximity", "attribute", "sort", "exactness"
                ]
            }
        )
    print(f"[Meilisearch] 索引 '{INDEX_NAME}' 就绪")

def add_documents(docs):
    if not docs:
        return
    r = requests.post(
        f"{MEILI_URL}/indexes/{INDEX_NAME}/documents",
        headers=meili_headers(),
        json=docs
    )
    task_uid = r.json().get("taskUid")
    print(f"[Meilisearch] 写入 {len(docs)} 条，taskUid={task_uid}")


# ── FreshRSS GReader API ──────────────────────────────────
def freshrss_auth():
    """获取 FreshRSS GReader API token"""
    r = requests.post(
        f"{FRESHRSS_URL}/api/greader.php/accounts/ClientLogin",
        data={
            "Email": FRESHRSS_USERNAME,
            "Passwd": FRESHRSS_API_PASS,
        }
    )
    r.raise_for_status()
    for line in r.text.splitlines():
        if line.startswith("Auth="):
            return line[5:].strip()
    raise ValueError("FreshRSS 认证失败，请检查用户名和 API 密码")

def fetch_freshrss_items(auth_token, continuation=None, count=500):
    """拉取 FreshRSS 条目"""
    headers = {"Authorization": f"GoogleLogin auth={auth_token}"}
    params = {
        "output": "json",
        "n": count,
    }
    if continuation:
        params["c"] = continuation
    r = requests.get(
        f"{FRESHRSS_URL}/api/greader.php/reader/api/0/stream/contents/reading-list",
        headers=headers,
        params=params
    )
    r.raise_for_status()
    return r.json()

def item_to_doc(item):
    """把 FreshRSS 条目转换为 Meilisearch 文档"""
    # 用 URL 哈希作为稳定 ID
    url = item.get("canonical", [{}])[0].get("href", "")
    doc_id = hashlib.md5(url.encode()).hexdigest() if url else item.get("id", "")

    content = ""
    if item.get("summary"):
        content = item["summary"].get("content", "")
    # 去除 HTML 标签（简单版，生产可换 bleach）
    import re
    content = re.sub(r"<[^>]+>", " ", content).strip()
    content = re.sub(r"\s+", " ", content)[:2000]  # 限制长度

    published = item.get("published", 0)

    return {
        "id": doc_id,
        "title": item.get("title", ""),
        "content": content,
        "url": url,
        "author": item.get("author", ""),
        "feed_title": item.get("origin", {}).get("title", ""),
        "feed_category": "",   # FreshRSS 分类需额外 API，此处留空
        "published_at": published,
        "published_date": datetime.fromtimestamp(published).strftime("%Y-%m-%d") if published else "",
    }


# ── 状态持久化（记录上次同步位置）─────────────────────
STATE_FILE = Path(__file__).parent.parent / ".sync_state.json"

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── 主流程 ────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始同步 FreshRSS → Meilisearch")

    ensure_index()

    auth = freshrss_auth()
    state = load_state()
    continuation = state.get("freshrss_continuation")

    total = 0
    batch = []

    while True:
        data = fetch_freshrss_items(auth, continuation=continuation)
        items = data.get("items", [])
        if not items:
            break

        for item in items:
            doc = item_to_doc(item)
            if doc["title"] or doc["content"]:
                batch.append(doc)

        # 每 200 条写一次
        if len(batch) >= 200:
            add_documents(batch)
            total += len(batch)
            batch = []

        continuation = data.get("continuation")
        if not continuation:
            break

        time.sleep(0.2)  # 避免打爆 FreshRSS

    # 写剩余
    if batch:
        add_documents(batch)
        total += len(batch)

    # 保存状态（下次增量同步用）
    save_state({"freshrss_continuation": None, "last_sync": datetime.now().isoformat()})

    print(f"[完成] 共处理 {total} 条文档")


if __name__ == "__main__":
    main()
