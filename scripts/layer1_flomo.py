#!/usr/bin/env python3
"""
layer1_flomo.py
第一层补充：flomo 采集（Mac mini 端）
opencli browser 自动化 → Xiaomi mimo 切片 → Meilisearch memory_chunks
每天凌晨 4:30 运行（MacBook L1 之前）
部署位置：Mac mini ~/Documents/myScope/scripts/
"""

import os
import json
import re
import hashlib
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import requests

load_dotenv(Path(__file__).parent.parent / ".env")

XIAOMI_KEY   = os.environ["XIAOMI_API_KEY"]
MEMORY_URL   = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "memory-api-token-2026")

llm = OpenAI(api_key=XIAOMI_KEY, base_url="https://token-plan-cn.xiaomimimo.com/v1")

# Mac mini 上 opencli 路径（根据实际安装位置调整）
OPENCLI = os.environ.get("OPENCLI_PATH", "/usr/local/bin/opencli")
STATE_FILE = Path(__file__).parent.parent / "logs" / "layer1_flomo_state.json"

CHUNK_MAX = 200


# ── 状态管理 ────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── flomo 采集（opencli browser 自动化） ────────────────────
def collect_flomo() -> list[dict]:
    """通过 opencli browser 从 flomo 网页提取 memo"""
    print("  [flomo] 开始浏览器自动化...")
    texts = []

    try:
        # 打开 flomo
        r = subprocess.run(
            [OPENCLI, "browser", "flomo-scrape", "open", "https://v.flomoapp.com/mine",
             "--window", "background"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            print(f"  [flomo] 打开失败: {r.stderr[:200]}")
            return []

        # 等待页面加载
        subprocess.run(
            [OPENCLI, "browser", "flomo-scrape", "wait", "time", "5"],
            capture_output=True, text=True, timeout=15
        )

        # 检查是否需要登录
        r = subprocess.run(
            [OPENCLI, "browser", "flomo-scrape", "state"],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and "login" in r.stdout.lower():
            print("  [flomo] 未登录，跳过（请先在 Mac mini 的 Chrome 中登录 flomo）")
            subprocess.run(
                [OPENCLI, "browser", "flomo-scrape", "close"],
                capture_output=True, text=True, timeout=10
            )
            return []

        # 滚动加载更多 memo（加载最近几天的内容）
        for i in range(5):
            subprocess.run(
                [OPENCLI, "browser", "flomo-scrape", "scroll", "down"],
                capture_output=True, text=True, timeout=10
            )
            time.sleep(1.5)

        # 提取页面内容
        r = subprocess.run(
            [OPENCLI, "browser", "flomo-scrape", "extract",
             "--selector", ".memo-list,.richtext,.memo,.note-list"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            # fallback: 提取整页
            r = subprocess.run(
                [OPENCLI, "browser", "flomo-scrape", "extract"],
                capture_output=True, text=True, timeout=30
            )

        if r.returncode == 0 and r.stdout.strip():
            raw = r.stdout.strip()
            # flomo memo 通常以日期或分隔线区分
            memos = re.split(r'\n---\n|\n#{1,3}\s+\d{4}[-/]\d{2}[-/]\d{2}', raw)
            for memo in memos:
                memo = memo.strip()
                if len(memo) > 20:
                    texts.append({
                        "text": memo[:2000],
                        "source": "flomo"
                    })
            print(f"  [flomo] 提取到 {len(texts)} 条 memo")
        else:
            print(f"  [flomo] 提取失败或无内容")

        # 关闭浏览器 session
        subprocess.run(
            [OPENCLI, "browser", "flomo-scrape", "close"],
            capture_output=True, text=True, timeout=10
        )

    except subprocess.TimeoutExpired:
        print("  [flomo] 超时")
    except FileNotFoundError:
        print(f"  [flomo] opencli 未找到: {OPENCLI}")
    except Exception as e:
        print(f"  [flomo] 异常: {e}")

    return texts


# ── LLM 切片 ────────────────────────────────────────────────
SLICE_PROMPT = """\
你是一个个人知识管理助手。请将下面这段文字切分成若干独立的记忆碎片。

要求：
- 每条碎片聚焦一个事实、想法或事件，不超过 {max_chars} 字
- 输出 JSON 数组，每个对象包含：
  - "content": 碎片正文
  - "summary": 一句话摘要（20字以内）
  - "keywords": 关键词列表（3-5个）
- 过于零散或无意义的内容直接丢弃
- 只输出 JSON，不要其他文字

文字：
{text}
"""


def slice_text(text: str) -> list[dict]:
    if len(text) < 30:
        return []
    try:
        resp = llm.chat.completions.create(
            model="mimo-v2.5",
            messages=[{
                "role": "user",
                "content": SLICE_PROMPT.format(text=text[:3000], max_chars=CHUNK_MAX)
            }],
            temperature=0.2,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        return json.loads(match.group())
    except Exception as e:
        print(f"    [切片失败] {e}")
        return []


# ── 写入 Meilisearch ────────────────────────────────────────
def push_chunks(docs: list[dict]):
    if not docs:
        return
    headers = {
        "Authorization": f"Bearer {MEMORY_TOKEN}",
        "Content-Type": "application/json",
    }
    batch_size = 50
    total = 0
    for i in range(0, len(docs), batch_size):
        batch = docs[i:i + batch_size]
        try:
            r = requests.post(
                f"{MEMORY_URL}/ingest",
                headers=headers,
                json={"index": "memory_chunks", "documents": batch},
                timeout=30,
            )
            r.raise_for_status()
            total += r.json().get("count", len(batch))
        except Exception as e:
            print(f"    [写入失败] batch {i//batch_size}: {e}")
        time.sleep(0.3)
    print(f"  [memory-api] 写入 {total} 条到 memory_chunks")


# ── 主流程 ────────────────────────────────────────────────
def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始 flomo 采集（Mac mini）")

    state = load_state()
    texts = collect_flomo()

    if not texts:
        print("  无新 memo，跳过")
        save_state({"last_run": datetime.now().isoformat()})
        return

    print(f"  共收集 {len(texts)} 条 memo，开始切片...")

    all_chunks = []
    for item in texts:
        chunks = slice_text(item["text"])
        for c in chunks:
            if not c.get("content"):
                continue
            chunk_id = hashlib.md5(c["content"].encode()).hexdigest()
            all_chunks.append({
                "id": chunk_id,
                "title": c.get("summary", ""),
                "content": c["content"],
                "keywords": c.get("keywords", []),
                "source": "flomo",
                "created_at": datetime.now().isoformat(),
            })
        time.sleep(0.5)

    print(f"  切片完成：{len(all_chunks)} 条碎片")
    push_chunks(all_chunks)

    save_state({"last_run": datetime.now().isoformat()})
    print(f"[完成] flomo 采集 {len(all_chunks)} 条记忆碎片")


if __name__ == "__main__":
    main()
