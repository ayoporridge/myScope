#!/usr/bin/env python3
"""
dayflow_daily_summary.py
Dayflow 日摘要 → Anda Hippocampus Formation
运行位置：MacBook（Dayflow 只装在 MacBook）
触发方式：开机自动运行（launchd RunAtLoad），在 dayflow_sync 之后

功能：
  1. 从 Dayflow SQLite 读取昨天全天的 observations
  2. 按时间顺序拼成一段活动叙述
  3. 封装成 user+assistant 消息对，提交 Formation API

运行方式：
  python3 dayflow_daily_summary.py          # 昨天
  python3 dayflow_daily_summary.py --date 2026-05-14  # 指定日期
"""

import argparse
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

DAYFLOW_DB       = Path.home() / "Library/Application Support/Dayflow/chunks.sqlite"
ANDA_BASE_URL    = os.environ["ANDA_BASE_URL"]
ANDA_SPACE_ID    = os.environ["ANDA_SPACE_ID"]
ANDA_SPACE_TOKEN = os.environ["ANDA_SPACE_TOKEN"]

FORMATION_URL = f"{ANDA_BASE_URL}/v1/{ANDA_SPACE_ID}/formation"
HEADERS = {
    "Authorization": f"Bearer {ANDA_SPACE_TOKEN}",
    "Content-Type": "application/json",
}

# 每次 Formation 最多带多少字（避免 token 过大）
MAX_CHARS = 8000


def fetch_day_observations(date_str: str) -> list[str]:
    """读取指定日期（本地时区）的全部 observations，按时间排序。"""
    # 把日期转成 Unix 时间戳区间
    local_tz = datetime.now().astimezone().tzinfo
    day_start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=local_tz)
    day_end   = day_start + timedelta(days=1)

    start_ts = int(day_start.timestamp())
    end_ts   = int(day_end.timestamp())

    conn = sqlite3.connect(str(DAYFLOW_DB))
    cur  = conn.execute(
        """
        SELECT start_ts, observation
        FROM observations
        WHERE start_ts >= ? AND start_ts < ?
        ORDER BY start_ts ASC
        """,
        (start_ts, end_ts),
    )
    rows = cur.fetchall()
    conn.close()
    return rows  # [(ts, text), ...]


def build_summary_text(date_str: str, rows: list) -> str:
    """把 observations 拼成一段紧凑的日活动叙述。"""
    lines = [f"[{date_str} 活动记录，共 {len(rows)} 条]"]
    char_count = len(lines[0])

    for ts, obs in rows:
        time_label = datetime.fromtimestamp(ts).strftime("%H:%M")
        entry = f"\n{time_label} {obs}"
        if char_count + len(entry) > MAX_CHARS:
            lines.append(f"\n… (共 {len(rows)} 条，已截断)")
            break
        lines.append(entry)
        char_count += len(entry)

    return "".join(lines)


def post_formation(date_str: str, summary: str) -> bool:
    """把日摘要封装成对话对提交 Formation。"""
    messages = [
        {
            "role": "user",
            "content": f"请记录我 {date_str} 的活动摘要：\n\n{summary}",
        },
        {
            "role": "assistant",
            "content": (
                f"已记录 {date_str} 的活动摘要。"
                "主要内容包括你当天在屏幕上的工作和活动轨迹，"
                "已整合进长期记忆。"
            ),
        },
    ]

    payload = {
        "messages": messages,
        "context": {"counterparty": "xz", "source": "dayflow_daily_summary"},
        "timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    try:
        resp = requests.post(FORMATION_URL, headers=HEADERS, json=payload, timeout=60)
        resp.raise_for_status()
        return True
    except requests.HTTPError as e:
        print(f"  [Formation 失败] HTTP {e.response.status_code}: {e.response.text[:300]}")
        return False
    except Exception as e:
        print(f"  [Formation 失败] {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Dayflow 日摘要 → Hippocampus")
    parser.add_argument(
        "--date",
        default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="要处理的日期，格式 YYYY-MM-DD（默认昨天）",
    )
    args = parser.parse_args()
    date_str = args.date

    ts_label = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts_label}] Dayflow 日摘要 → Hippocampus（日期: {date_str}）")

    rows = fetch_day_observations(date_str)
    if not rows:
        print(f"  {date_str} 无 observations，跳过。")
        return

    print(f"  读取到 {len(rows)} 条 observations")
    summary = build_summary_text(date_str, rows)

    ok = post_formation(date_str, summary)
    if ok:
        print(f"[完成] {date_str} 日摘要已提交 Hippocampus（{len(summary)} 字）")
    else:
        print(f"[失败] {date_str} 日摘要提交失败")
        exit(1)


if __name__ == "__main__":
    main()
