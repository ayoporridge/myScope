#!/usr/bin/env python3
"""
layer1_rag.py
第一层：事实记忆（MacBook 端）
微信 + Obsidian → DeepSeek 切片 → Meilisearch memory_chunks
每天凌晨 5:00 运行
注：flomo 采集在 Mac mini 上独立运行（layer1_flomo.py）
"""

from __future__ import annotations

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
try:
    from _metrics import record_last_run, record_metrics
except ImportError:  # pragma: no cover - package import path for tests
    from scripts._metrics import record_last_run, record_metrics

load_dotenv(Path(__file__).parent.parent / ".env")

DEEPSEEK_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
MEMORY_URL   = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")

llm = OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_BASE_URL)

OPENCLI = "/Users/xz/.local/nodejs/bin/opencli"
OBSIDIAN_VAULT = Path(os.environ.get("OBSIDIAN_VAULT", str(Path.home() / "Desktop" / "obsidian-default"))).expanduser()
STATE_FILE = Path(__file__).parent.parent / "logs" / "layer1_state.json"

CHUNK_MAX = 200
MIN_SLICEABLE_TEXT_CHARS = 30
LLM_ERRORS: list[str] = []

# 敏感内容过滤：跳过疑似 token/key/密码的文本
SENSITIVE_PATTERNS = re.compile(
    r"(eyJ[A-Za-z0-9_-]{20,}|"     # JWT
    r"pat_[A-Za-z0-9]{20,}|"        # PAT token
    r"sk-[A-Za-z0-9]{20,}|"         # API key
    r"token[：:]\s*\S{30,}|"        # generic token
    r"password[：:]\s*\S{8,})",     # password
    re.IGNORECASE
)


def is_sensitive(text: str) -> bool:
    """检测文本是否包含敏感凭据"""
    return bool(SENSITIVE_PATTERNS.search(text))


def is_sliceable_input(text: str) -> bool:
    return len((text or "").strip()) >= MIN_SLICEABLE_TEXT_CHARS


# ── 状态管理 ────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


# ── 源一：微信（收藏 + 文件传输助手） ───────────────────────
def collect_wechat(state: dict) -> list[dict]:
    """通过 opencli wx 读取微信收藏和文件传输助手"""
    texts = []
    since = state.get("last_run_date", (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))

    # 微信收藏（文字类型）
    try:
        r = subprocess.run(
            [OPENCLI, "wx", "favorites", "--type", "text", "--limit", "100", "--json"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0 and r.stdout.strip():
            output = r.stdout.strip()
            start = output.find("[")
            end = output.rfind("]")
            if start != -1 and end != -1:
                items = json.loads(output[start:end + 1])
                for item in items:
                    # 从 preview 的 XML <desc> 标签中提取文本
                    preview = item.get("preview", "")
                    match = re.search(r"<desc>(.*?)</desc>", preview)
                    content = match.group(1) if match else ""
                    if is_sliceable_input(content):
                        texts.append({
                            "text": content[:2000],
                            "source": "wechat:favorites"
                        })
                print(f"  [微信收藏] {len(texts)} 条有效内容（共 {len(items)} 条）")
    except Exception as e:
        print(f"  [微信收藏] 失败: {e}")

    # 文件传输助手
    try:
        r = subprocess.run(
            [OPENCLI, "wx", "history", "文件传输助手",
             "--type", "text", "--since", since, "--limit", "200", "--json"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0 and r.stdout.strip():
            output = r.stdout.strip()
            start = output.find("[")
            end = output.rfind("]")
            if start != -1 and end != -1:
                messages = json.loads(output[start:end + 1])
                for msg in messages:
                    content = msg.get("content", "") or msg.get("text", "")
                    if is_sliceable_input(content) and not is_sensitive(content):
                        texts.append({
                            "text": content[:2000],
                            "source": "wechat:file_helper"
                        })
                print(f"  [文件传输助手] {len(messages)} 条")
    except Exception as e:
        print(f"  [文件传输助手] 失败: {e}")

    return texts


# ── 源三：Obsidian（最近修改的 .md 文件） ────────────────────
def collect_obsidian(state: dict) -> list[dict]:
    """读取 Obsidian vault 中最近修改的 markdown 文件"""
    texts = []
    last_run = state.get("last_run_ts", 0)
    # 如果从未运行过，取最近 24 小时
    if not last_run:
        last_run = (datetime.now() - timedelta(days=1)).timestamp()

    if not OBSIDIAN_VAULT.exists():
        print(f"  [obsidian] vault 不存在: {OBSIDIAN_VAULT}")
        return []

    count = 0
    for md_file in OBSIDIAN_VAULT.rglob("*.md"):
        # 跳过隐藏目录（.obsidian、.trash 等）
        if any(part.startswith(".") for part in md_file.parts):
            continue
        if md_file.stat().st_mtime <= last_run:
            continue
        try:
            content = md_file.read_text(encoding="utf-8").strip()
            if len(content) > 30:
                texts.append({
                    "text": content[:3000],
                    "source": f"obsidian:{md_file.relative_to(OBSIDIAN_VAULT)}"
                })
                count += 1
        except Exception:
            continue

    print(f"  [obsidian] {count} 个最近修改的文件")
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
    """用 DeepSeek 把长文本切成记忆碎片"""
    if not is_sliceable_input(text):
        return []
    if LLM_ERRORS:
        return []
    try:
        resp = llm.chat.completions.create(
            model=DEEPSEEK_MODEL,
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
        summary = str(e).strip().replace("\n", " ")[:300]
        LLM_ERRORS.append(summary)
        print(f"    [切片失败] {summary}")
        return []


# ── 写入 Meilisearch ────────────────────────────────────────
def push_chunks(docs: list[dict]):
    """通过 memory-api 写入 Meilisearch memory_chunks 索引"""
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
    print(f"  [memory-api] 共写入 {total} 条到 memory_chunks")


# ── 主流程 ────────────────────────────────────────────────
def main() -> int:
    start_time = time.time()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始第一层 RAG 切片")

    state = load_state()

    # 收集两源（flomo 在 Mac mini 独立运行）
    all_texts = []
    all_texts += collect_wechat(state)
    all_texts += collect_obsidian(state)

    print(f"  共收集 {len(all_texts)} 段原始文本")

    if not all_texts:
        print("  无新内容，跳过")
        save_state({
            "last_run_date": datetime.now().strftime("%Y-%m-%d"),
            "last_run_ts": datetime.now().timestamp(),
        })
        record_last_run("layer1_rag")
        record_metrics(
            "layer1_rag",
            raw_texts=0,
            chunks_produced=0,
            llm_errors=0,
            run_duration_seconds=round(time.time() - start_time, 1),
        )
        return 0

    # LLM 切片
    all_chunks = []
    for item in all_texts:
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
                "source": item["source"],
                "created_at": datetime.now().isoformat(),
            })
        time.sleep(0.5)  # LLM 限速

    print(f"  切片完成：{len(all_chunks)} 条碎片")

    # 写入 Meilisearch
    push_chunks(all_chunks)

    # LLM 失败时不推进 state/last_run，让后续夜间窗口可以重试同一批输入。
    if not LLM_ERRORS:
        save_state({
            "last_run_date": datetime.now().strftime("%Y-%m-%d"),
            "last_run_ts": datetime.now().timestamp(),
        })
        record_last_run("layer1_rag")
    record_metrics(
        "layer1_rag",
        raw_texts=len(all_texts),
        chunks_produced=len(all_chunks),
        llm_errors=len(LLM_ERRORS),
        llm_error_summary=LLM_ERRORS[0] if LLM_ERRORS else "",
        run_duration_seconds=round(time.time() - start_time, 1),
    )
    print(f"[完成] 共处理 {len(all_chunks)} 条记忆碎片")
    return 2 if LLM_ERRORS else 0


if __name__ == "__main__":
    raise SystemExit(main())
