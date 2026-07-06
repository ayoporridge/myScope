#!/usr/bin/env python3
"""
layer3_index.py
第三层：哈勃半径
FreshRSS 新内容 → Meilisearch 索引
运行位置：Mac mini（FreshRSS Docker 在 Mac mini 上）
触发方式：每天 02:30 定时运行
"""

import os
import json
import hashlib
import time
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
try:
    from _metrics import record_last_run, record_metrics
except ImportError:  # pragma: no cover - package import path for tests
    from scripts._metrics import record_last_run, record_metrics

# 加载 .env
load_dotenv(Path(__file__).parent.parent / ".env")

FRESHRSS_URL      = os.environ["FRESHRSS_URL"]
FRESHRSS_USERNAME = os.environ["FRESHRSS_USERNAME"]
FRESHRSS_API_PASS = os.environ["FRESHRSS_API_PASSWORD"]
MEMORY_URL        = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN      = os.environ.get("MEMORY_API_TOKEN", "")
INDEX_NAME        = "hubble_radius"

# ── memory-api 写入 ───────────────────────────────────────
def add_documents(docs):
    if not docs:
        return
    headers = {
        "Authorization": f"Bearer {MEMORY_TOKEN}",
        "Content-Type": "application/json",
    }
    r = requests.post(
        f"{MEMORY_URL}/ingest",
        headers=headers,
        json={"index": INDEX_NAME, "documents": docs},
        timeout=30,
    )
    r.raise_for_status()
    count = r.json().get("count", len(docs))
    print(f"[memory-api] 写入 {count} 条")


# ── FreshRSS GReader API ──────────────────────────────────
def freshrss_auth():
    """获取 FreshRSS GReader API token"""
    r = requests.post(
        f"{FRESHRSS_URL}/api/greader.php/accounts/ClientLogin",
        data={
            "Email": FRESHRSS_USERNAME,
            "Passwd": FRESHRSS_API_PASS,
        },
        timeout=10,
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
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _canonical_url(item):
    canonical = item.get("canonical") or []
    if canonical and isinstance(canonical[0], dict):
        return canonical[0].get("href", "")
    return ""


def item_to_doc(item):
    """把 FreshRSS 条目转换为 Meilisearch 文档"""
    # 用 URL 哈希作为稳定 ID
    url = _canonical_url(item)
    doc_id = hashlib.md5(url.encode()).hexdigest() if url else item.get("id", "")
    title = item.get("title", "")

    content = ""
    if item.get("summary"):
        content = item["summary"].get("content", "")
    # 去除 HTML 标签（简单版，生产可换 bleach）
    import re
    content = re.sub(r"<[^>]+>", " ", content).strip()
    content = re.sub(r"\s+", " ", content)[:2000]  # 限制长度

    published = item.get("published", 0)
    published_at = datetime.fromtimestamp(published).isoformat() if published else ""
    date = datetime.fromtimestamp(published).strftime("%Y-%m-%d") if published else ""

    return {
        "id":           doc_id,
        "title":        title,
        "content":      content,
        "text":         f"{title} {content}".strip(),
        "url":          url,
        "source":       item.get("origin", {}).get("title", "rss"),
        "date":         date,
        "published_at": published_at,
        "indexed_at":   datetime.now().isoformat(),
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
    started = time.time()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始同步 FreshRSS → Meilisearch")

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
            if doc["text"]:
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
    record_last_run("layer3_index")
    record_metrics(
        "layer3_index",
        documents_indexed=total,
        run_duration_seconds=round(time.time() - started, 1),
    )

    print(f"[完成] 共处理 {total} 条文档")


if __name__ == "__main__":
    main()
