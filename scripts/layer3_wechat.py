#!/usr/bin/env python3
"""
layer3_wechat.py
第三层补充：公众号文章 → Meilisearch hubble_radius
通过 opencli wx biz-articles 从本地微信缓存读取文章
每天凌晨 2:30 运行（RSS 同步之后）
"""

import os
import json
import hashlib
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
import requests
from _metrics import record_last_run, record_metrics

load_dotenv(Path(__file__).parent.parent / ".env")

MEMORY_URL   = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "memory-api-token-2026")
INDEX_NAME   = "hubble_radius"

OPENCLI = "/Users/xz/.local/nodejs/bin/opencli"
SUBS_PATH = Path(__file__).parent.parent / "subscriptions.yaml"

# 状态文件：记录上次拉取的时间戳
STATE_FILE = Path(__file__).parent.parent / "logs" / "layer3_wechat_state.json"


def load_accounts() -> list[str]:
    """从 subscriptions.yaml 读取公众号列表"""
    import yaml
    config = yaml.safe_load(SUBS_PATH.read_text())
    return config.get("wechat", {}).get("accounts", [])


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def fetch_articles(since: str, limit: int = 500) -> list[dict]:
    """调用 opencli wx biz-articles 获取文章列表"""
    cmd = [OPENCLI, "wx", "biz-articles", "--json", "--limit", str(limit), "--since", since]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"  [错误] opencli 返回 {result.returncode}: {result.stderr[:200]}")
            return []
        # 输出可能包含尾部的 "Update available" 提示，只取 JSON 部分
        output = result.stdout.strip()
        # 找到 JSON 数组的起止
        start = output.find("[")
        end = output.rfind("]")
        if start == -1 or end == -1:
            print("  [警告] 无有效 JSON 输出")
            return []
        return json.loads(output[start:end + 1])
    except subprocess.TimeoutExpired:
        print("  [超时] opencli 执行超过 60s")
        return []
    except Exception as e:
        print(f"  [异常] {e}")
        return []


def push_to_meilisearch(docs: list[dict]):
    """通过 memory-api 写入 Meilisearch"""
    if not docs:
        return
    headers = {
        "Authorization": f"Bearer {MEMORY_TOKEN}",
        "Content-Type": "application/json",
    }
    # 分批写入，每批 50 条
    batch_size = 50
    total = 0
    for i in range(0, len(docs), batch_size):
        batch = docs[i:i + batch_size]
        try:
            r = requests.post(
                f"{MEMORY_URL}/ingest",
                headers=headers,
                json={"index": INDEX_NAME, "documents": batch},
                timeout=30,
            )
            r.raise_for_status()
            count = r.json().get("count", len(batch))
            total += count
        except Exception as e:
            print(f"  [写入失败] batch {i//batch_size}: {e}")
        time.sleep(0.3)
    print(f"  [memory-api] 共写入 {total} 条")


def main():
    start_time = time.time()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始第三层 公众号文章索引")

    accounts = load_accounts()
    if not accounts:
        print("  无公众号配置，退出")
        record_last_run("layer3_wechat")
        record_metrics("layer3_wechat", articles_indexed=0,
                       run_duration_seconds=round(time.time() - start_time, 1))
        return
    print(f"  已配置 {len(accounts)} 个公众号")

    # 确定拉取时间范围：上次运行日期 或 昨天
    state = load_state()
    last_run = state.get("last_run_date")
    if last_run:
        since = last_run
    else:
        since = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"  拉取 {since} 以来的文章")

    # 拉取全量文章
    articles = fetch_articles(since=since, limit=1000)
    if not articles:
        print("  无新文章")
        save_state({"last_run_date": datetime.now().strftime("%Y-%m-%d")})
        record_last_run("layer3_wechat")
        record_metrics("layer3_wechat", articles_indexed=0,
                       run_duration_seconds=round(time.time() - start_time, 1))
        return

    print(f"  opencli 返回 {len(articles)} 篇文章")

    # 过滤：只保留订阅列表中的公众号
    accounts_set = set(accounts)
    filtered = [a for a in articles if a.get("account") in accounts_set]
    print(f"  匹配订阅列表：{len(filtered)} 篇")

    if not filtered:
        save_state({"last_run_date": datetime.now().strftime("%Y-%m-%d")})
        record_last_run("layer3_wechat")
        record_metrics("layer3_wechat", articles_indexed=0,
                       run_duration_seconds=round(time.time() - start_time, 1))
        return

    # 转换为 Meilisearch 文档格式
    docs = []
    for art in filtered:
        doc_id = hashlib.md5(art["url"].encode()).hexdigest()
        docs.append({
            "id": doc_id,
            "title": art.get("title", ""),
            "content": art.get("digest", ""),
            "url": art.get("url", ""),
            "source": f"wechat:{art.get('account', '')}",
            "author": art.get("account", ""),
            "published_at": art.get("time", ""),
            "indexed_at": datetime.now().isoformat(),
        })

    push_to_meilisearch(docs)

    # 更新状态
    save_state({"last_run_date": datetime.now().strftime("%Y-%m-%d")})

    # 记录指标
    record_last_run("layer3_wechat")
    record_metrics(
        "layer3_wechat",
        articles_indexed=len(docs),
        run_duration_seconds=round(time.time() - start_time, 1),
    )
    print(f"[完成] 索引了 {len(docs)} 篇公众号文章")


if __name__ == "__main__":
    main()
