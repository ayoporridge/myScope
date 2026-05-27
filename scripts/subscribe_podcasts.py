#!/usr/bin/env python3
"""
批量订阅播客到 FreshRSS
使用 iTunes Search API 搜索播客（免费、无需登录）→ 直接订阅原始 RSS feed

用法：
  python3 subscribe_podcasts.py              # 订阅所有
  python3 subscribe_podcasts.py --dry-run    # 只搜索预览，不实际订阅
"""

import os
import sys
import time
import argparse
import yaml
import requests
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import quote

# 加载 .env
load_dotenv(Path(__file__).parent.parent / ".env")

FRESHRSS_URL = os.environ["FRESHRSS_URL"]
FRESHRSS_USERNAME = os.environ["FRESHRSS_USERNAME"]
FRESHRSS_API_PASSWORD = os.environ["FRESHRSS_API_PASSWORD"]


# ── iTunes 搜索 ──────────────────────────────────────────────────

def search_podcast(name: str, country: str = "CN") -> dict | None:
    """用 iTunes Search API 搜索播客，返回 {title, feedUrl, artistName}"""
    url = (
        f"https://itunes.apple.com/search"
        f"?term={quote(name)}&media=podcast&country={country}&limit=5"
    )
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if not results:
            return None
        # 优先找名字完全匹配的
        for r in results:
            if r.get("trackName", "").lower() == name.lower():
                return {
                    "title": r["trackName"],
                    "feedUrl": r.get("feedUrl"),
                    "artist": r.get("artistName", ""),
                }
        # 否则取第一个
        r = results[0]
        return {
            "title": r["trackName"],
            "feedUrl": r.get("feedUrl"),
            "artist": r.get("artistName", ""),
        }
    except Exception as e:
        print(f"  ⚠️  搜索失败: {e}")
        return None


# ── FreshRSS 认证 ────────────────────────────────────────────────

def freshrss_auth() -> tuple[dict, str]:
    """登录 FreshRSS，返回 (headers, t_token)"""
    base = f"{FRESHRSS_URL}/api/greader.php"
    resp = requests.post(
        f"{base}/accounts/ClientLogin",
        data={"Email": FRESHRSS_USERNAME, "Passwd": FRESHRSS_API_PASSWORD},
        timeout=10,
    )
    resp.raise_for_status()
    auth = {
        k: v
        for k, v in (
            line.split("=", 1)
            for line in resp.text.strip().splitlines()
            if "=" in line
        )
    }
    headers = {"Authorization": f"GoogleLogin auth={auth['Auth']}"}
    t_token = requests.get(
        f"{base}/reader/api/0/token", headers=headers, timeout=10
    ).text.strip()
    return headers, t_token


def freshrss_subscribe(feed_url: str, folder: str, headers: dict, t_token: str) -> bool:
    """订阅一个 feed，成功返回 True"""
    base = f"{FRESHRSS_URL}/api/greader.php"
    resp = requests.post(
        f"{base}/reader/api/0/subscription/edit",
        headers=headers,
        data={
            "ac": "subscribe",
            "s": f"feed/{feed_url}",
            "a": f"user/-/label/{folder}",
            "T": t_token,
        },
        timeout=10,
    )
    # 200 OK = 订阅成功；400 Bad Request = 已订阅过，也视为成功
    return resp.status_code in (200, 400)


# ── 主流程 ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只搜索预览，不订阅")
    args = parser.parse_args()

    config_path = Path(__file__).parent.parent / "subscriptions.yaml"
    config = yaml.safe_load(config_path.read_text())

    headers, t_token = None, None
    if not args.dry_run:
        print("🔑 登录 FreshRSS...")
        headers, t_token = freshrss_auth()
        print("   ✅ 登录成功\n")

    ok_list, fail_list = [], []

    # ── 博客 RSS ──────────────────────────────────────────
    blogs_section = config.get("blogs", {})
    if blogs_section:
        blogs_folder = blogs_section.get("folder", "Blogs")
        feeds = blogs_section.get("feeds", [])
        print(f"📰 博客 RSS（{len(feeds)} 个） → 分组: {blogs_folder}\n")
        for item in feeds:
            name = item["name"]
            feed_url = item.get("feed_url")
            if not feed_url or item.get("skip"):
                print(f"⏭️  跳过: {name}\n")
                continue
            print(f"  ✅ {name}")
            print(f"  🔗 {feed_url}")
            if args.dry_run:
                print()
                ok_list.append(name)
            else:
                ok = freshrss_subscribe(feed_url, blogs_folder, headers, t_token)
                print(f"  {'📥 已订阅' if ok else '❌ 订阅失败'}\n")
                (ok_list if ok else fail_list).append(name)
                time.sleep(0.3)

    # ── 小宇宙播客 ─────────────────────────────────────────
    section = config.get("xiaoyuzhou", {})
    folder = section.get("folder", "Podcasts")
    podcasts = section.get("podcasts", [])

    print(f"📻 小宇宙播客（{len(podcasts)} 个） → 分组: {folder}\n")

    for item in podcasts:
        name = item["name"]
        feed_url = item.get("feed_url")  # 允许手动预填 feed URL

        if item.get("skip"):
            print(f"⏭️  跳过（待补充）: {name}\n")
            continue

        if not feed_url:
            print(f"🔍 搜索: {name}")
            found = search_podcast(name)
            if not found or not found.get("feedUrl"):
                print(f"  ❌ 未找到\n")
                fail_list.append(name)
                continue
            feed_url = found["feedUrl"]
            print(f"  ✅ {found['title']}  —  {found['artist']}")
            print(f"  🔗 {feed_url}")
        else:
            print(f"  ✅ {name}（feed 已指定）")
            print(f"  🔗 {feed_url}")

        if args.dry_run:
            print()
            ok_list.append(name)
        else:
            ok = freshrss_subscribe(feed_url, folder, headers, t_token)
            if ok:
                print(f"  📥 已订阅\n")
                ok_list.append(name)
            else:
                print(f"  ❌ 订阅失败\n")
                fail_list.append(name)
            time.sleep(0.3)

    print("─" * 40)
    print(f"✅ 成功: {len(ok_list)} 个")
    if fail_list:
        print(f"❌ 失败: {len(fail_list)} 个")
        for name in fail_list:
            print(f"   - {name}")
    if args.dry_run:
        print("\n（dry-run 模式，未实际订阅）")


if __name__ == "__main__":
    main()
