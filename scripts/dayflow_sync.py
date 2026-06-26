#!/usr/bin/env python3
"""
dayflow_sync.py
Dayflow observations → Meilisearch (memory_chunks) 增量同步
运行位置：MacBook（Dayflow 只装在 MacBook）
触发方式：开机自动运行（launchd RunAtLoad）

运行方式：
  python3 dayflow_sync.py          # 增量（只同步新增）
  python3 dayflow_sync.py --full   # 全量重导

状态文件：~/.dayflow_sync_state.json
  {"last_obs_id": 1044}  — 上次同步到的最大 observation id
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from _metrics import record_last_run, record_metrics

load_dotenv(Path(__file__).parent.parent / ".env")

DAYFLOW_DB   = Path.home() / "Library/Application Support/Dayflow/chunks.sqlite"
MEMORY_URL   = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")
INDEX        = "memory_chunks"
BATCH_SIZE   = 200
STATE_FILE   = Path.home() / ".dayflow_sync_state.json"


# ── 状态管理 ──────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_obs_id": 0}

def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Dayflow 查询 ──────────────────────────────────────────

def fetch_observations(since_id: int) -> list[dict]:
    """读取 id > since_id 的所有 observations"""
    conn = sqlite3.connect(str(DAYFLOW_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        """
        SELECT o.id, o.start_ts, o.end_ts, o.observation
        FROM observations o
        WHERE o.id > ?
        ORDER BY o.id ASC
        """,
        (since_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def obs_to_doc(row: dict) -> dict:
    """observation 行 → memory-api 文档"""
    ts = row["start_ts"]
    date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    return {
        "id":     f"dayflow_obs_{row['id']}",
        "text":   row["observation"],
        "source": "dayflow",
        "date":   date_str,
    }


# ── memory-api 写入 ───────────────────────────────────────

def ingest(docs: list[dict]) -> int:
    """批量写入，返回成功写入数"""
    headers = {
        "Authorization": f"Bearer {MEMORY_TOKEN}",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        f"{MEMORY_URL}/ingest",
        headers=headers,
        json={"index": INDEX, "documents": docs},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("count", len(docs))


# ── 主流程 ────────────────────────────────────────────────

def main():
    start_time = time.time()
    parser = argparse.ArgumentParser(description="Dayflow → Meilisearch 增量同步")
    parser.add_argument("--full", action="store_true", help="全量重导（忽略状态文件）")
    args = parser.parse_args()

    state    = load_state()
    since_id = 0 if args.full else state.get("last_obs_id", 0)

    ts_label = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts_label}] 增量同步 Dayflow → Meilisearch（since id={since_id}）")

    rows = fetch_observations(since_id)
    if not rows:
        print("  无新数据，退出。")
        record_last_run("dayflow_sync")
        record_metrics("dayflow_sync", observations_synced=0,
                       run_duration_seconds=round(time.time() - start_time, 1))
        return

    print(f"  发现 {len(rows)} 条新 observations（id {rows[0]['id']} ~ {rows[-1]['id']}）")

    total   = 0
    max_id  = since_id
    batch   = []

    for row in rows:
        batch.append(obs_to_doc(row))
        max_id = max(max_id, row["id"])

        if len(batch) >= BATCH_SIZE:
            written = ingest(batch)
            total  += written
            print(f"  写入 {written} 条 …")
            batch = []

    if batch:
        written = ingest(batch)
        total  += written

    save_state({"last_obs_id": max_id})

    # 记录指标
    record_last_run("dayflow_sync")
    record_metrics(
        "dayflow_sync",
        observations_synced=total,
        run_duration_seconds=round(time.time() - start_time, 1),
    )
    print(f"[完成] 共写入 {total} 条，last_obs_id 更新为 {max_id}")


if __name__ == "__main__":
    main()
