#!/usr/bin/env python3
"""
ingest_wechat_url.py
手动收录单篇微信公众号文章 → Meilisearch hubble_radius
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent.parent
load_dotenv(PROJECT_DIR / ".env")

MEMORY_URL = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")
INDEX_NAME = "hubble_radius"


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def js_unescape(value: str) -> str:
    value = html.unescape(value)
    if "\\x" in value or "\\u" in value:
        try:
            return bytes(value, "utf-8").decode("unicode_escape")
        except UnicodeDecodeError:
            return value
    return value


def first_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.S)
        if match:
            return clean_text(js_unescape(match.group(1)))
    return ""


def soup_text(soup: BeautifulSoup, selector: str) -> str:
    node = soup.select_one(selector)
    if not node:
        return ""
    return clean_text(node.get_text(" ", strip=True))


def canonical_url(original_url: str, page_text: str) -> str:
    biz = first_match(page_text, [r'biz:\s*"([^"]+)"', r"var biz = '([^']+)'", r"__biz=([^&\"\\]+)"])
    mid = first_match(page_text, [r'mid:\s*"([^"]+)"', r"var mid = '([^']+)'", r"mid=([^&\"\\]+)"])
    idx = first_match(page_text, [r'idx:\s*"([^"]+)"', r"var idx = '([^']+)'", r"idx=([^&\"\\]+)"])
    sn = first_match(page_text, [r'sn:\s*"([^"]+)"', r"var sn = '([^']+)'", r"sn=([^&\"\\]+)"])
    if biz and mid and idx and sn:
        return f"https://mp.weixin.qq.com/s?__biz={biz}&mid={mid}&idx={idx}&sn={sn}#rd"
    return original_url


def fetch_page(url: str) -> tuple[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
        ),
        "Referer": "https://mp.weixin.qq.com/",
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.url, response.text


def extract_doc(url: str, page_url: str, page_text: str, content_limit: int = 12000) -> dict:
    soup = BeautifulSoup(page_text, "html.parser")
    title = (
        first_match(page_text, [r"var msg_title = '([^']*)'", r'var msg_title = "([^"]*)"'])
        or soup_text(soup, "#activity-name")
        or first_match(page_text, [r'<meta property="og:title" content="([^"]*)"'])
    )
    author = (
        soup_text(soup, "#js_name")
        or first_match(page_text, [r"var nickname = '([^']*)'", r'var nickname = "([^"]*)"'])
        or "微信公众号"
    )
    digest = (
        first_match(page_text, [r"var msg_desc = '([^']*)'", r'var msg_desc = "([^"]*)"'])
        or first_match(page_text, [r'<meta property="og:description" content="([^"]*)"'])
    )
    published_at = (
        soup_text(soup, "#publish_time")
        or first_match(page_text, [r"var publish_time = '([^']*)'", r'var publish_time = "([^"]*)"'])
        or first_match(page_text, [r"create_time:\s*'(\d{4}-\d{2}-\d{2}[^']*)'", r'create_time:\s*"(\d{4}-\d{2}-\d{2}[^"]*)"'])
    )
    content_node = soup.select_one("#js_content")
    body = clean_text(content_node.get_text(" ", strip=True)) if content_node else ""
    content = body or digest
    canonical = canonical_url(page_url or url, page_text)
    doc_id = hashlib.md5(canonical.encode()).hexdigest()
    return {
        "id": doc_id,
        "title": title,
        "content": content[:content_limit],
        "url": canonical,
        "source": f"wechat:{author}",
        "author": author,
        "published_at": published_at,
        "indexed_at": datetime.now().isoformat(),
        "digest": digest,
        "original_url": url,
        "ingest_mode": "manual_url",
    }


def push_doc(doc: dict) -> int:
    if not MEMORY_TOKEN:
        raise RuntimeError("缺少 MEMORY_API_TOKEN")
    response = requests.post(
        f"{MEMORY_URL}/ingest",
        headers={
            "Authorization": f"Bearer {MEMORY_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"index": INDEX_NAME, "documents": [doc]},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("count", 1)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="手动收录单篇微信公众号文章")
    parser.add_argument("url", help="微信公众号文章 URL")
    parser.add_argument("--dry-run", action="store_true", help="只解析并打印，不写入")
    parser.add_argument("--content-limit", type=int, default=12000, help="写入正文字符上限")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    page_url, page_text = fetch_page(args.url)
    doc = extract_doc(args.url, page_url, page_text, content_limit=args.content_limit)
    print(json.dumps({
        "title": doc["title"],
        "source": doc["source"],
        "published_at": doc["published_at"],
        "url": doc["url"],
        "content_chars": len(doc["content"]),
    }, ensure_ascii=False, indent=2))
    if args.dry_run:
        return
    count = push_doc(doc)
    print(f"[完成] 已写入 {INDEX_NAME}: {count} 条")


if __name__ == "__main__":
    main()
