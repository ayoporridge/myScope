#!/usr/bin/env python3
"""
layer2_wiki.py
第二层：结构记忆 (LLM Wiki)

输入：
  - Layer 1 memory_chunks（你知道的：事实碎片）
  - Layer 3 hubble_radius（你可能知道的：关注源中的相关内容）

输出：
  - wiki_entries（你应该知道的：结构化知识条目）

设计意图：Layer 2 是跨层综合器。它不只总结你已经知道的事实，
还会从哈勃半径里拉取与当前主题相关的内容，把「你可能知道但未消化的」
变成「你应该知道的」结构化知识。

每天凌晨 5:30 运行（第一层跑完后）
运行位置：Mac mini（只读 Meilisearch API，不依赖本地数据）
"""

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
from _metrics import record_last_run, record_metrics

load_dotenv(Path(__file__).parent.parent / ".env")

XIAOMI_KEY   = os.environ["XIAOMI_API_KEY"]
MEMORY_URL   = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "memory-api-token-2026")

HEADERS = {
    "Authorization": f"Bearer {MEMORY_TOKEN}",
    "Content-Type": "application/json",
}

llm = OpenAI(api_key=XIAOMI_KEY, base_url="https://token-plan-cn.xiaomimimo.com/v1")


# ── 从 Meilisearch 读取最近切片（Layer 1）───────────────────
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
        cutoff = (datetime.now().timestamp() - 25 * 3600)
        recent = []
        for item in results:
            created = item.get("created_at", "")
            if created:
                try:
                    ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                    if ts >= cutoff:
                        recent.append(item)
                        continue
                except (ValueError, TypeError):
                    pass
            recent.append(item)
        return recent
    except Exception as e:
        print(f"  [读取 memory_chunks 失败] {e}")
        return []


# ── 从 Layer 1 切片中提取关键词 ─────────────────────────────
def extract_topics(chunks: list[dict]) -> list[str]:
    """从今日切片中提取主题关键词，用于在哈勃半径中搜索相关内容"""
    # 收集所有 keywords
    all_keywords = []
    for c in chunks:
        kw = c.get("keywords", [])
        if isinstance(kw, list):
            all_keywords.extend(kw)
        # 也从 title/summary 中取
        title = c.get("title", c.get("summary", ""))
        if title:
            all_keywords.append(title)

    # 去重，取出现频率最高的关键词（最多 5 个搜索词）
    from collections import Counter
    counts = Counter(all_keywords)
    # 过滤太短或太通用的
    meaningful = [(w, n) for w, n in counts.items() if len(w) >= 2]
    top = sorted(meaningful, key=lambda x: -x[1])[:5]
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
        return [item.get("title", "") for item in results if item.get("title")]
    except Exception:
        return []


# ── LLM Wiki 归纳（跨层综合）───────────────────────────────
WIKI_PROMPT = """\
你是一个个人知识库维护助手。你的任务是把用户最近的事实碎片和信息视野中的相关内容，归纳成结构化的 Wiki 条目。

【今日新增碎片】（用户亲身经历/思考/记录的事实）
{chunks}

【哈勃半径相关内容】（用户关注源中与今日主题相关的文章/资讯，用户未必读过）
{hubble_context}

【现有 Wiki 条目】（仅标题，用于判断新建还是合并）
{existing_titles}

请输出一个 JSON 数组，每个对象代表一个 Wiki 条目：
- "title": 条目标题（简洁，5-15字）
- "content": 结构化内容（300字以内，散文或要点式）
- "tags": 标签列表（3-6个）
- "action": "create"（全新主题）或 "update"（与已有条目相关）
- "sources": 来源说明（如 "个人经验" 或 "个人经验+哈勃半径"）

原则：
- 以用户的事实碎片为主线，哈勃半径内容作为补充和印证
- 如果哈勃半径里有与用户当前关注高度相关的洞见，可以主动纳入 Wiki
- 不要照搬外部文章内容，只提炼与用户相关的部分
- 相关度高的碎片合并进同一条目
- 只产出有信息量的条目，不要凑数
- 只输出 JSON，不要其他文字
"""


def plan_wiki(chunks: list[dict], hubble_results: list[dict], existing_titles: list[str]) -> list[dict]:
    """用 LLM 跨层归纳 Wiki 条目"""
    chunks_text = "\n".join(
        f"- {c.get('content', c.get('text', ''))}"
        for c in chunks[:50]
    )

    # 格式化哈勃半径内容
    if hubble_results:
        hubble_lines = []
        for h in hubble_results:
            title = h.get("title", "")
            content = h.get("content", h.get("digest", ""))[:200]
            source = h.get("source", h.get("author", ""))
            hubble_lines.append(f"- [{source}] {title}: {content}")
        hubble_text = "\n".join(hubble_lines)
    else:
        hubble_text = "（今日无相关内容）"

    titles_text = "\n".join(f"- {t}" for t in existing_titles[:100]) or "（暂无）"

    try:
        resp = llm.chat.completions.create(
            model="mimo-v2.5",
            messages=[{
                "role": "user",
                "content": WIKI_PROMPT.format(
                    chunks=chunks_text,
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

    # Step 2: 提取今日主题，搜索哈勃半径
    topics = extract_topics(chunks)
    print(f"  今日主题关键词: {topics}")

    hubble_results = search_hubble_radius(topics)
    print(f"  哈勃半径匹配: {len(hubble_results)} 条相关内容")

    # Step 3: 获取已有 Wiki
    existing_titles = get_existing_wiki_titles()
    print(f"  现有 Wiki 条目：{len(existing_titles)} 条")

    # Step 4: LLM 跨层归纳
    operations = plan_wiki(chunks, hubble_results, existing_titles)
    print(f"  LLM 规划了 {len(operations)} 个 Wiki 条目")

    # Step 5: 写入
    push_wiki_entries(operations)

    # 记录指标
    record_last_run("layer2_wiki")
    record_metrics(
        "layer2_wiki",
        new_chunks_processed=len(chunks),
        hubble_results=len(hubble_results),
        topics_extracted=topics,
        wiki_entries_written=len(operations),
        run_duration_seconds=round(time.time() - start_time, 1),
    )
    print("[完成] Wiki 更新结束")


if __name__ == "__main__":
    main()
