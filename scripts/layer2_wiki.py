#!/usr/bin/env python3
"""
layer2_wiki.py
第二层：结构记忆 (LLM Wiki)
输入：
  - Layer 1 memory_chunks（你知道的：事实碎片）
  - Layer 3 hubble_radius（你可能知道的：关注源中的相关内容）
  - 海马体 Anda 图谱（你讨论过的：对话记忆中的决策/偏好/思考）

输出：
  - wiki_entries（你应该知道的：结构化知识条目）

设计意图：Layer 2 是跨层综合器。它以第一层切片为线索，
从哈勃半径拉取相关内容，再从海马体召回相关对话记忆，
把「你可能知道但未消化的」和「你讨论过但未沉淀的」
变成「你应该知道的」结构化知识。

每天凌晨 5:30 运行（第一层跑完后）
"""

from __future__ import annotations

import os
import json
import re
import hashlib
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import requests
try:
    from _metrics import record_last_run, record_metrics
except ImportError:  # pragma: no cover - package import path for tests
    from scripts._metrics import record_last_run, record_metrics

load_dotenv(Path(__file__).parent.parent / ".env")

XIAOMI_KEY   = os.environ["XIAOMI_API_KEY"]
MEMORY_URL   = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")

# Anda 海马体
ANDA_BASE_URL    = os.environ.get("ANDA_BASE_URL", "https://hippocampus.arjo.us.ci")
ANDA_SPACE_ID    = os.environ.get("ANDA_SPACE_ID", "hermes_main")
ANDA_SPACE_TOKEN = os.environ.get("ANDA_SPACE_TOKEN", "")
ANDA_HEADERS = {
    "Authorization": f"Bearer {ANDA_SPACE_TOKEN}",
    "Content-Type": "application/json",
}

HEADERS = {
    "Authorization": f"Bearer {MEMORY_TOKEN}",
    "Content-Type": "application/json",
}

llm = OpenAI(api_key=XIAOMI_KEY, base_url="https://token-plan-cn.xiaomimimo.com/v1")


# ── 从 Meilisearch 读取最近切片（Layer 1）───────────────────
def parse_doc_datetime(item: dict) -> datetime | None:
    """Best-effort timestamp parsing across old/new memory schemas."""
    for key in ("created_at", "updated_at", "indexed_at", "published_at", "date", "timestamp"):
        value = item.get(key)
        if not value:
            continue
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(value)
            except (OSError, ValueError):
                continue
        text = str(value).strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(text[:10], fmt)
            except ValueError:
                continue
    return None


def filter_recent_chunks(chunks: list[dict], *, now: datetime | None = None, hours: int = 25) -> list[dict]:
    """Keep recent chunks; retain unknown-date docs as a compatibility fallback."""
    now = now or datetime.now()
    cutoff = now.timestamp() - hours * 3600
    recent = []
    for item in chunks:
        dt = parse_doc_datetime(item)
        if dt is None or dt.timestamp() >= cutoff:
            recent.append(item)
    return recent


def get_recent_chunks() -> list[dict]:
    """获取 memory_chunks 中最近 25 小时的切片"""
    try:
        r = requests.get(
            f"{MEMORY_URL}/search",
            params={"q": "", "index": "memory_chunks", "limit": 200},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        return filter_recent_chunks(results)
    except Exception as e:
        print(f"  [读取 memory_chunks 失败] {e}")
        return []


# ── 从 Layer 1 切片中提取关键词 ─────────────────────────────
def extract_topics(chunks: list[dict]) -> list[str]:
    """从今日切片中提取主题关键词，用于在哈勃半径中搜索相关内容"""
    all_keywords = []
    stopwords = {
        "今天", "继续", "需要", "相关", "内容", "系统", "用户", "进行", "查看",
        "the", "and", "for", "with", "from", "that", "this", "need",
    }
    for c in chunks:
        kw = c.get("keywords", [])
        if isinstance(kw, list):
            all_keywords.extend(kw)
        for key in ("title", "summary", "source"):
            value = c.get(key, "")
            if value:
                all_keywords.append(str(value))
        text = c.get("content") or c.get("text") or c.get("digest") or ""
        all_keywords.extend(re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text))
        all_keywords.extend(re.findall(r"[\u4e00-\u9fff]{2,8}", text))

    from collections import Counter
    normalized = []
    for word in all_keywords:
        word = str(word).strip().strip("：:，,。.!?、[]()（）").lower()
        if len(word) < 2 or word in stopwords:
            continue
        normalized.append(word)
    counts = Counter(normalized)
    meaningful = [(w, n) for w, n in counts.items() if len(w) >= 2]
    top = sorted(
        meaningful,
        key=lambda x: (
            -(
                x[1]
                + (3 if any(term in x[0] for term in ("myscope", "hubble", "哈勃", "dashboard", "hippocampus", "海马")) else 0)
            ),
            x[0],
        ),
    )[:5]
    return [w for w, _ in top]


# ── 从哈勃半径搜索相关内容（Layer 3）──────────────────────
def search_hubble_radius(topics: list[str]) -> list[dict]:
    """用今日主题关键词搜索哈勃半径，找到信息宇宙中的相关内容"""
    if not topics:
        return []

    all_results = []
    seen_ids = set()

    for topic in topics:
        try:
            r = requests.get(
                f"{MEMORY_URL}/search",
                params={"q": topic, "index": "hubble_radius", "limit": 10},
                headers=HEADERS,
                timeout=10,
            )
            r.raise_for_status()
            results = r.json().get("results", [])
            for item in results:
                item_id = item.get("id", "")
                if item_id not in seen_ids:
                    seen_ids.add(item_id)
                    all_results.append(item)
        except Exception as e:
            print(f"    [hubble 搜索失败] topic='{topic}': {e}")
            continue
        time.sleep(0.2)

    # 最多返回 15 条，避免 prompt 过长
    return all_results[:15]


# ── 从海马体召回相关对话记忆（Anda）────────────────────────
def recall_hippocampus(topics: list[str]) -> str:
    """用今日主题关键词向 Anda 海马体召回相关对话记忆，返回 Markdown 文本。"""
    if not topics or not ANDA_SPACE_TOKEN:
        return ""

    recall_url = f"{ANDA_BASE_URL}/v1/{ANDA_SPACE_ID}/recall"
    query = "、".join(topics)
    payload = {
        "query": f"用户最近关于 {query} 的对话、决策、偏好和思考",
        "context": {"counterparty": "xz"},
        "top_k": 15,
    }
    try:
        resp = requests.post(recall_url, headers=ANDA_HEADERS, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("result", {}).get("content", "")
        if content:
            print(f"  海马体召回: {len(content)} 字")
        return content
    except Exception as e:
        print(f"  [海马体召回失败] {e}")
        return ""


# ── 获取已有 Wiki 条目 ───────────────────────────────────────
def get_existing_wiki_titles() -> list[str]:
    """获取现有 wiki_entries 的标题列表"""
    try:
        r = requests.get(
            f"{MEMORY_URL}/search",
            params={"q": "", "index": "wiki_entries", "limit": 200},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return extract_wiki_titles(results)
    except Exception:
        return []


def extract_wiki_titles(results: list[dict]) -> list[str]:
    """Extract titles from rich or flattened wiki search results."""
    titles = []
    seen = set()
    for item in results:
        raw = item.get("title") or item.get("summary") or item.get("text") or item.get("content") or ""
        title = str(raw).strip().splitlines()[0].strip()
        title = re.split(r"[：:]", title, maxsplit=1)[0].strip()
        if title and title not in seen:
            seen.add(title)
            titles.append(title[:40])
    return titles


# ── LLM Wiki 归纳（跨层综合）───────────────────────────────
WIKI_PROMPT = """\
你是一个个人知识库维护助手。你的任务是把用户最近的事实碎片、对话记忆和信息视野中的相关内容，归纳成结构化的 Wiki 条目。

【今日新增碎片】（用户亲身经历/思考/记录的事实）
{chunks}

【海马体对话记忆】（用户近期与 AI 的对话中提取的决策、偏好、思考）
{hippocampus_context}

【哈勃半径相关内容】（用户关注源中与今日主题相关的文章/资讯，用户未必读过）
{hubble_context}

【现有 Wiki 条目】（仅标题，用于判断新建还是合并）
{existing_titles}

请输出一个 JSON 数组，每个对象代表一个 Wiki 条目：
- "title": 条目标题（简洁，5-15字）
- "content": 结构化内容（300字以内，散文或要点式）
- "tags": 标签列表（3-6个）
- "action": "create"（全新主题）或 "update"（与已有条目相关）
- "sources": 来源说明（如 "个人经验" 或 "对话记忆+哈勃半径" 等）

原则：
- 以用户的事实碎片和对话记忆为主线，哈勃半径内容作为补充和印证
- 如果海马体里有用户近期的决策或偏好变化，优先纳入 Wiki
- 如果哈勃半径里有与用户当前关注高度相关的洞见，可以主动纳入 Wiki
- 不要照搬外部文章内容，只提炼与用户相关的部分
- 相关度高的碎片合并进同一条目
- 只产出有信息量的条目，不要凑数
- 只输出 JSON，不要其他文字
"""


def plan_wiki(chunks: list[dict], hubble_results: list[dict], existing_titles: list[str], hippocampus_text: str = "") -> list[dict]:
    """用 LLM 跨层归纳 Wiki 条目"""
    chunks_text = "\n".join(
        f"- {c.get('content') or c.get('text') or c.get('digest') or ''}"
        for c in chunks[:50]
    )

    # 格式化哈勃半径内容
    if hubble_results:
        hubble_lines = []
        for h in hubble_results:
            title = h.get("title") or ""
            content = (h.get("content") or h.get("digest") or h.get("text") or "")[:200]
            source = h.get("source", h.get("author", ""))
            if not title:
                title = content[:40]
            hubble_lines.append(f"- [{source}] {title}: {content}")
        hubble_text = "\n".join(hubble_lines)
    else:
        hubble_text = "（今日无相关内容）"

    # 海马体对话记忆
    if not hippocampus_text:
        hippocampus_text = "（暂无相关对话记忆）"

    titles_text = "\n".join(f"- {t}" for t in existing_titles[:100]) or "（暂无）"

    try:
        resp = llm.chat.completions.create(
            model="mimo-v2.5",
            messages=[{
                "role": "user",
                "content": WIKI_PROMPT.format(
                    chunks=chunks_text,
                    hippocampus_context=hippocampus_text,
                    hubble_context=hubble_text,
                    existing_titles=titles_text,
                )
            }],
            temperature=0.3,
        )
        raw = resp.choices[0].message.content.strip()
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []
        return json.loads(match.group())
    except Exception as e:
        print(f"  [LLM 归纳失败] {e}")
        return []


# ── 写入 Meilisearch wiki_entries ────────────────────────────
def push_wiki_entries(entries: list[dict]):
    """写入 wiki_entries 索引"""
    if not entries:
        return
    docs = []
    for entry in entries:
        title = entry.get("title", "")
        content = entry.get("content", "")
        if not title or not content:
            continue
        doc_id = hashlib.md5(title.encode()).hexdigest()
        docs.append({
            "id": doc_id,
            "title": title,
            "content": content,
            "tags": entry.get("tags", []),
            "sources": entry.get("sources", ""),
            "updated_at": datetime.now().isoformat(),
        })

    if not docs:
        return

    try:
        r = requests.post(
            f"{MEMORY_URL}/ingest",
            headers=HEADERS,
            json={"index": "wiki_entries", "documents": docs},
            timeout=30,
        )
        r.raise_for_status()
        count = r.json().get("count", len(docs))
        print(f"  [memory-api] 写入 {count} 条到 wiki_entries")
    except Exception as e:
        print(f"  [写入 wiki_entries 失败] {e}")


# ── 主流程 ────────────────────────────────────────────────
def main():
    start_time = time.time()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始第二层 LLM Wiki 更新")

    # Step 1: 获取 Layer 1 新增切片
    chunks = get_recent_chunks()
    if not chunks:
        print("  没有新切片，跳过")
        record_last_run("layer2_wiki")
        record_metrics(
            "layer2_wiki",
            new_chunks_processed=0,
            hubble_results=0,
            wiki_entries_written=0,
            run_duration_seconds=round(time.time() - start_time, 1),
        )
        return
    print(f"  发现 {len(chunks)} 条 Layer 1 新切片")

    # Step 2: 提取今日主题，搜索哈勃半径 + 海马体
    topics = extract_topics(chunks)
    print(f"  今日主题关键词: {topics}")

    hubble_results = search_hubble_radius(topics)
    print(f"  哈勃半径匹配: {len(hubble_results)} 条相关内容")

    hippocampus_text = recall_hippocampus(topics)

    # Step 3: 获取已有 Wiki
    existing_titles = get_existing_wiki_titles()
    print(f"  现有 Wiki 条目：{len(existing_titles)} 条")

    # Step 4: LLM 跨层归纳（第一层 + 第三层 + 海马体）
    operations = plan_wiki(chunks, hubble_results, existing_titles, hippocampus_text)
    print(f"  LLM 规划了 {len(operations)} 个 Wiki 条目")

    # Step 5: 写入
    push_wiki_entries(operations)

    # 记录指标
    record_last_run("layer2_wiki")
    record_metrics(
        "layer2_wiki",
        new_chunks_processed=len(chunks),
        hubble_results=len(hubble_results),
        topics_extracted_count=len(topics),
        topics_extracted=topics,
        hippocampus_context_chars=len(hippocampus_text or ""),
        wiki_create_count=sum(1 for op in operations if op.get("action") == "create"),
        wiki_update_count=sum(1 for op in operations if op.get("action") == "update"),
        personal_hubble_count=sum(1 for op in operations if "hubble" in str(op.get("sources", "")).lower() or "哈勃" in str(op.get("sources", ""))),
        personal_only_count=sum(1 for op in operations if "hubble" not in str(op.get("sources", "")).lower() and "哈勃" not in str(op.get("sources", ""))),
        wiki_entries_written=len(operations),
        run_duration_seconds=round(time.time() - start_time, 1),
    )
    print("[完成] Wiki 更新结束")


if __name__ == "__main__":
    main()
