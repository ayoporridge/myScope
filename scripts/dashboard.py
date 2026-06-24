#!/usr/bin/env python3
"""
dashboard.py
MyScope 实时仪表盘 — 零依赖 Web Server
端口：8095，通过 Cloudflare Tunnel 暴露为 dashboard.arjo.us.ci
"""

from __future__ import annotations

import ast
import json
import os
import re
import socket
import struct
import time
from collections import Counter
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests as http_req
from dotenv import load_dotenv

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover - exercised when dependency is missing in prod
    zstd = None

load_dotenv(Path(__file__).parent.parent / ".env")

PORT = int(os.environ.get("DASHBOARD_PORT", 8095))
LOGS_DIR = Path(__file__).parent.parent / "logs"
LAST_RUN_FILE = LOGS_DIR / "last_run.json"
JOB_STATUS_FILE = LOGS_DIR / "job_status.json"
METRICS_FILE = LOGS_DIR / "metrics.jsonl"
METRICS_SHARED_DIR = Path(__file__).parent.parent / "data" / "metrics"
JOB_STATUS_SHARED_DIR = Path(__file__).parent.parent / "data" / "job_status"
HTML_FILE = Path(__file__).parent / "dashboard_page.html"

MEMORY_URL = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")
MEILI_URL = os.environ.get("MEILI_URL", "http://localhost:7700")
MEILI_KEY = os.environ.get("MEILI_KEY", "memory-master-key-2026")
ANDA_BASE_URL = os.environ.get("ANDA_BASE_URL", "http://localhost:8090")
ANDA_SPACE_ID = os.environ.get("ANDA_SPACE_ID", "anda_main")
ANDA_SPACE_TOKEN = os.environ.get("ANDA_SPACE_TOKEN", "")
ANDA_DATA_DIR = Path(os.environ.get("ANDA_DATA_DIR", str(Path.home() / "anda-data")))
LOCAL_HOSTNAME = socket.gethostname().split(".")[0]
BROWSE_FETCH_PAGE_SIZE = 1000
BROWSE_FETCH_CAP = 20000

INDEX_META = {
    "memory_chunks": {
        "title": "事实碎片",
        "layer": "Layer 1",
        "summary": "这里保存的是较短的个人事实记录，适合回答“我最近做过什么、看过什么、讨论过什么”。",
        "sources": ["Dayflow", "微信", "企业微信", "Obsidian", "flomo"],
        "pipeline": "MacBook 的 dayflow_sync/layer1_rag 与 Mac mini 的 layer1_flomo 写入 memory_chunks。",
        "stat_logic": "总量来自 Meilisearch memory_chunks 文档数；每日吞吐中的第一层切片 = layer1_rag.chunks_produced + layer1_flomo.chunks。",
    },
    "wiki_entries": {
        "title": "Wiki 条目",
        "layer": "Layer 2",
        "summary": "这里保存的是 LLM 从事实碎片中归纳出的结构化知识条目，比事实碎片更像主题笔记。",
        "sources": ["memory_chunks", "hubble_radius", "Hippocampus recall"],
        "pipeline": "layer2_wiki 读取近期事实碎片，抽取主题，检索哈勃半径，并尝试召回 Hippocampus 背景后生成条目。",
        "stat_logic": "总量来自 Meilisearch wiki_entries 文档数；每日吞吐中的第二层 Wiki = layer2_wiki.wiki_entries_written。",
    },
    "hubble_radius": {
        "title": "哈勃半径",
        "layer": "Layer 3",
        "summary": "这里保存外部信息宇宙，包括 RSS、播客、公众号文章，适合回答“我关注的信息源里有没有相关内容”。",
        "sources": ["FreshRSS", "公众号文章", "播客/RSS"],
        "pipeline": "Mac mini 的 layer3_index 写入 FreshRSS/RSS，MacBook 的 layer3_wechat 写入公众号文章。",
        "stat_logic": "总量来自 Meilisearch hubble_radius 文档数；每日吞吐中的第三层文章 = layer3_wechat.articles_indexed + layer3_index.documents_indexed。",
    },
    "anda_concepts": {
        "title": "图谱概念",
        "layer": "Hippocampus",
        "summary": "这里展示 Anda 从 AI 对话记忆中抽取出的长期主题、项目、工具、偏好、经验教训等概念节点。",
        "sources": ["AI 对话", "Dayflow 日摘要", "Hippocampus formation"],
        "pipeline": "hippocampus_formation/dayflow_daily_summary 提交内容后，Anda 在本地图谱库中形成 concepts 节点。",
        "stat_logic": "总量来自 Anda 本地 concepts 图谱文件；详情页按节点元数据与更新时间排序。",
    },
    "anda_propositions": {
        "title": "图谱命题",
        "layer": "Hippocampus",
        "summary": "这里展示概念之间的关系、判断、偏好、决策线索，也就是图谱里的边或事实命题。",
        "sources": ["AI 对话", "Dayflow 日摘要", "Hippocampus formation"],
        "pipeline": "Anda 从对话记忆中抽取 subject、predicate、object，并写入 propositions。",
        "stat_logic": "总量来自 Anda 本地 propositions 图谱文件；详情页会把概念 ID 尽量解析成人可读名称。",
    },
}


def _read_last_run() -> dict:
    if not LAST_RUN_FILE.exists():
        return {}
    try:
        return json.loads(LAST_RUN_FILE.read_text())
    except Exception:
        return {}


def _last_run_jobs(data: dict) -> dict:
    jobs = {}
    for name, last_str in data.items():
        jobs[name] = {
            "status": "success",
            "last_success_at": last_str,
            "last_finished_at": last_str,
        }
    return jobs


def get_status() -> dict:
    """脚本存活状态"""
    now = datetime.now()
    hosts = {}
    if JOB_STATUS_SHARED_DIR.exists():
        for path in sorted(JOB_STATUS_SHARED_DIR.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                hosts[path.stem] = data
            except Exception:
                continue
    if JOB_STATUS_FILE.exists():
        try:
            local_hosts = json.loads(JOB_STATUS_FILE.read_text())
            for host, jobs in local_hosts.items():
                hosts[host] = {**hosts.get(host, {}), **jobs}
        except Exception:
            pass
    local_last_run = _read_last_run()
    if hosts and local_last_run:
        local_jobs = hosts.setdefault(LOCAL_HOSTNAME, {})
        for name, info in _last_run_jobs(local_last_run).items():
            local_jobs.setdefault(name, info)
    if hosts:
        scripts = []
        for host, jobs in hosts.items():
            for name, info in jobs.items():
                last_str = (
                    info.get("last_success_at")
                    or info.get("last_finished_at")
                    or info.get("last_failure_at")
                    or info.get("last_started_at")
                )
                hours_ago = -1
                color = "red" if info.get("status") == "failure" else "green"
                if last_str:
                    try:
                        last = datetime.fromisoformat(last_str)
                        hours_ago = round((now - last).total_seconds() / 3600, 1)
                        if info.get("status") == "failure":
                            color = "red"
                        elif hours_ago < 25:
                            color = "green"
                        elif hours_ago < 48:
                            color = "amber"
                        else:
                            color = "red"
                    except Exception:
                        color = "red"
                normalized = {
                    **info,
                    "host": host,
                    "name": name,
                    "last_run": last_str,
                    "hours_ago": hours_ago,
                    "status_color": color,
                }
                scripts.append(normalized)
        return {
            "hosts": hosts,
            "scripts": sorted(scripts, key=lambda s: (s["host"], s["name"])),
            "checked_at": now.isoformat(timespec="seconds"),
        }

    data = _read_last_run()

    scripts = []
    for name, last_str in data.items():
        try:
            last = datetime.fromisoformat(last_str)
            hours_ago = (now - last).total_seconds() / 3600
            if hours_ago < 25:
                status = "green"
            elif hours_ago < 48:
                status = "yellow"
            else:
                status = "red"
            scripts.append({
                "name": name,
                "last_run": last_str,
                "hours_ago": round(hours_ago, 1),
                "status": status,
            })
        except Exception:
            scripts.append({"name": name, "last_run": last_str, "hours_ago": -1, "status": "red"})

    return {"scripts": scripts, "checked_at": now.isoformat(timespec="seconds")}


def get_metrics(days: int = 7) -> list[dict]:
    """读取最近 N 天的 metrics"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    results = []
    seen = set()

    def read_jsonl(path: Path):
        try:
            f = open(path, "r", encoding="utf-8")
        except OSError:
            return
        with f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("date", "") >= cutoff:
                        key = (
                            entry.get("hostname", path.stem),
                            entry.get("date", ""),
                            entry.get("script", ""),
                            entry.get("timestamp", ""),
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(entry)
                except json.JSONDecodeError:
                    continue

    if METRICS_SHARED_DIR.exists():
        for path in sorted(METRICS_SHARED_DIR.glob("*.jsonl")):
            read_jsonl(path)
    read_jsonl(METRICS_FILE)
    return sorted(results, key=lambda row: row.get("timestamp", ""))


def get_indexes() -> dict:
    """实时查询各索引文档数（使用 Meilisearch stats API 获取真实数量）"""
    indexes = {}

    # Meilisearch: 直接用 stats API 获取精确文档数
    try:
        r = http_req.get(
            f"{MEILI_URL}/stats",
            headers={"Authorization": f"Bearer {MEILI_KEY}"},
            timeout=5,
        )
        if r.status_code == 200:
            stats = r.json().get("indexes", {})
            for idx in ["memory_chunks", "wiki_entries", "hubble_radius"]:
                indexes[idx] = stats.get(idx, {}).get("numberOfDocuments", -1)
        else:
            for idx in ["memory_chunks", "wiki_entries", "hubble_radius"]:
                indexes[idx] = -1
    except Exception:
        for idx in ["memory_chunks", "wiki_entries", "hubble_radius"]:
            indexes[idx] = -1

    # Anda concepts/propositions
    try:
        r = http_req.get(
            f"{ANDA_BASE_URL}/v1/{ANDA_SPACE_ID}/info",
            headers={"Authorization": f"Bearer {ANDA_SPACE_TOKEN}"},
            timeout=5,
        )
        if r.status_code == 200:
            info = r.json().get("result", {})
            indexes["anda_concepts"] = info.get("concepts", 0)
            indexes["anda_propositions"] = info.get("propositions", 0)
        else:
            indexes["anda_concepts"] = -1
            indexes["anda_propositions"] = -1
    except Exception:
        indexes["anda_concepts"] = -1
        indexes["anda_propositions"] = -1

    return indexes


def _parse_doc_time(value) -> datetime:
    if value is None:
        return datetime.min
    if isinstance(value, (int, float)):
        try:
            timestamp = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(timestamp)
        except (OSError, OverflowError, ValueError):
            return datetime.min
    text = str(value).strip()
    if not text:
        return datetime.min
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return datetime.min


def _doc_freshness(doc: dict) -> datetime:
    fields = (
        "updated_at",
        "created_at",
        "published_at",
        "indexed_at",
        "date",
        "timestamp",
        "end_ts",
        "start_ts",
    )
    candidates = [_parse_doc_time(doc.get(field)) for field in fields]
    metadata = doc.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(_parse_doc_time(metadata.get(field)) for field in fields)
    return max(candidates) if candidates else datetime.min


def _browse_meta(index: str) -> dict:
    return INDEX_META.get(index, {"title": index, "summary": "", "sources": [], "pipeline": "", "stat_logic": ""})


def _source_summary(docs: list[dict]) -> list[dict]:
    counter = Counter()
    for doc in docs:
        source = doc.get("source") or doc.get("sources") or "unknown"
        if isinstance(source, str) and source.startswith("[") and source.endswith("]"):
            try:
                source = ast.literal_eval(source)
            except (ValueError, SyntaxError):
                pass
        if isinstance(source, list):
            for item in source:
                counter[str(item or "unknown")] += 1
        else:
            counter[str(source or "unknown")] += 1
    return [{"source": name, "count": count} for name, count in counter.most_common(8)]


def _page_payload(index: str, source: str, total: int, docs: list[dict], limit: int, offset: int) -> dict:
    page = docs[offset:offset + limit]
    next_offset = offset + len(page)
    return {
        "ok": True,
        "index": index,
        "source": source,
        "meta": _browse_meta(index),
        "source_summary": _source_summary(docs),
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": next_offset,
        "has_more": next_offset < total,
        "results": page,
    }


def _fetch_meili_documents(index: str, headers: dict) -> tuple[list[dict], int] | None:
    docs = []
    total = None
    offset = 0
    while len(docs) < BROWSE_FETCH_CAP:
        page_limit = min(BROWSE_FETCH_PAGE_SIZE, BROWSE_FETCH_CAP - len(docs))
        r = http_req.get(
            f"{MEILI_URL}/indexes/{index}/documents",
            params={"limit": page_limit, "offset": offset},
            headers=headers,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        batch = data.get("results", data if isinstance(data, list) else [])
        if total is None:
            total = data.get("total", len(batch)) if isinstance(data, dict) else len(batch)
        if not batch:
            break
        docs.extend(batch)
        offset += len(batch)
        if len(docs) >= total:
            break
    return docs, total if total is not None else len(docs)


def _browse_meili_documents(index: str, query: str, limit: int, offset: int = 0) -> dict | None:
    headers = {"Authorization": f"Bearer {MEILI_KEY}", "Content-Type": "application/json"}
    try:
        if query.strip():
            r = http_req.post(
                f"{MEILI_URL}/indexes/{index}/search",
                headers=headers,
                json={"q": query, "limit": limit, "offset": offset, "attributesToRetrieve": ["*"]},
                timeout=10,
            )
            if r.status_code != 200:
                return None
            data = r.json()
            hits = data.get("hits", [])
            total = data.get("estimatedTotalHits", len(hits))
            next_offset = offset + len(hits)
            return {
                "ok": True,
                "index": index,
                "source": "meili",
                "meta": _browse_meta(index),
                "total": total,
                "limit": limit,
                "offset": offset,
                "next_offset": next_offset,
                "has_more": next_offset < total,
                "results": hits[:limit],
            }

        fetched = _fetch_meili_documents(index, headers)
        if not fetched:
            return None
        docs, total = fetched
        docs = sorted(docs, key=_doc_freshness, reverse=True)
        return _page_payload(index, "meili", total, docs, limit, offset)
    except Exception:
        return None


def _ms_to_iso(value) -> str:
    try:
        if value is None:
            return ""
        timestamp = float(value) / 1000 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


class _CborReader:
    """Tiny CBOR reader for Anda's plain map/list/string/number records."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _read(self, size: int) -> bytes:
        chunk = self.data[self.pos : self.pos + size]
        if len(chunk) != size:
            raise ValueError("truncated cbor")
        self.pos += size
        return chunk

    def _uint(self, arg: int) -> int:
        if arg < 24:
            return arg
        if arg == 24:
            return self._read(1)[0]
        if arg == 25:
            return int.from_bytes(self._read(2), "big")
        if arg == 26:
            return int.from_bytes(self._read(4), "big")
        if arg == 27:
            return int.from_bytes(self._read(8), "big")
        raise ValueError(f"unsupported cbor integer argument: {arg}")

    def read_value(self):
        first = self._read(1)[0]
        major = first >> 5
        arg = first & 0x1F

        if major == 0:
            return self._uint(arg)
        if major == 1:
            return -1 - self._uint(arg)
        if major == 2:
            return self._read(self._uint(arg))
        if major == 3:
            return self._read(self._uint(arg)).decode("utf-8", errors="replace")
        if major == 4:
            return [self.read_value() for _ in range(self._uint(arg))]
        if major == 5:
            result = {}
            for _ in range(self._uint(arg)):
                key = self.read_value()
                value = self.read_value()
                if isinstance(key, (dict, list, set)):
                    key = json.dumps(key, ensure_ascii=False, sort_keys=True)
                result[key] = value
            return result
        if major == 6:
            return {"tag": self._uint(arg), "value": self.read_value()}
        if major == 7:
            if arg == 20:
                return False
            if arg == 21:
                return True
            if arg in (22, 23):
                return None
            if arg == 24:
                return self._read(1)[0]
            if arg == 25:
                return struct.unpack(">e", self._read(2))[0]
            if arg == 26:
                return struct.unpack(">f", self._read(4))[0]
            if arg == 27:
                return struct.unpack(">d", self._read(8))[0]
        raise ValueError(f"unsupported cbor type: {major}/{arg}")


def _decode_anda_cbor(path: Path) -> dict:
    data = path.read_bytes()
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        if zstd is None:
            raise RuntimeError("zstandard is required to read Anda graph files")
        data = zstd.ZstdDecompressor().decompress(data)
    value = _CborReader(data).read_value()
    return value if isinstance(value, dict) else {}


def _field(row: dict, key: int, default=None):
    if key in row:
        return row.get(key, default)
    return row.get(str(key), default)


def _anda_data_root() -> Path | None:
    candidates = [
        ANDA_DATA_DIR / "data" / ANDA_SPACE_ID,
        ANDA_DATA_DIR / "data" / "anda_main",
        ANDA_DATA_DIR / "data" / "hermes_main",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def _compact_text(value, limit: int = 140) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _metadata_summary(attrs: dict, keys: list[str], limit: int = 260) -> str:
    parts = []
    for key in keys:
        value = attrs.get(key)
        if value:
            parts.append(str(value).strip())
    return _compact_text(" ".join(parts), limit)


def _list_summary(values, label: str, limit: int = 4) -> str:
    if not isinstance(values, list) or not values:
        return ""
    selected = [str(item).strip() for item in values[:limit] if str(item).strip()]
    if not selected:
        return ""
    suffix = " 等" if len(values) > limit else ""
    return f"{label}：" + "；".join(selected) + suffix


def _extract_dictish_text(text: str) -> str:
    pairs = re.findall(
        r"['\"]([^'\"]+)['\"]\s*:\s*(?:['\"]([^'\"]+)['\"]|\[([^\]]{1,180})\]|([^,}]{1,180}))",
        text,
    )
    if not pairs:
        return ""
    preferred = {
        "content_summary",
        "summary",
        "description",
        "project",
        "deliverable",
        "purpose",
        "key_quote",
        "trigger",
        "background",
        "session_type",
        "total_docs",
        "title_chosen",
        "aiyue_reason",
        "books_read",
        "categories",
        "livestream_transcripts",
    }
    chosen = []
    for key, quoted, list_value, bare in pairs:
        if "path" in key.lower() or "url" in key.lower():
            continue
        value = quoted or list_value or bare
        value = re.sub(r"['\"]", "", value).strip()
        if not value:
            continue
        score = 0 if key in preferred else 1
        chosen.append((score, key, value))
    chosen.sort(key=lambda row: row[0])
    return "；".join(value for _, _, value in chosen[:4])


def _slug_title(raw_name: str, concept_type: str) -> str:
    tail = raw_name.split(":")[-1] if raw_name else ""
    if tail:
        words = tail.replace("-", " ").replace("_", " ").strip()
        if words:
            return words
    labels = {
        "Event": "事件记录",
        "Preference": "偏好记录",
        "Insight": "经验洞察",
        "Observation": "观察记录",
    }
    return labels.get(concept_type, "图谱概念")


def _humanize_value(value, depth: int = 0) -> str:
    if depth > 2 or value in (None, "", [], {}):
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")) and text.endswith(("}", "]")):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return _extract_dictish_text(text) or ""
            return _humanize_value(parsed, depth + 1) or text
        return text
    if isinstance(value, list):
        parts = [_humanize_value(item, depth + 1) for item in value[:4]]
        parts = [part for part in parts if part]
        return "；".join(parts)
    if isinstance(value, dict):
        preferred = [
            "content_summary",
            "summary",
            "description",
            "project",
            "deliverable",
            "purpose",
            "key_quote",
            "trigger",
            "background",
            "session_type",
            "total_docs",
            "title_chosen",
        ]
        parts = []
        for key in preferred:
            if key in value:
                part = _humanize_value(value.get(key), depth + 1)
                if part:
                    parts.append(str(part))
        if not parts:
            for key, item in value.items():
                if key in ("avatar", "confidence") or "path" in str(key).lower() or "url" in str(key).lower():
                    continue
                part = _humanize_value(item, depth + 1)
                if part:
                    parts.append(f"{key}: {part}")
                if len(parts) >= 4:
                    break
        return "；".join(parts)
    return str(value)


def _fallback_attr_summary(attrs: dict) -> str:
    parts = [
        _list_summary(attrs.get("behavior_preferences"), "行为偏好"),
        _list_summary(attrs.get("interests"), "关注主题"),
        _list_summary(attrs.get("projects"), "相关项目"),
        _list_summary(attrs.get("goals"), "目标"),
    ]
    parts = [part for part in parts if part]
    if parts:
        return _compact_text(" ".join(parts), 260)

    readable = []
    for key, value in attrs.items():
        if key in ("avatar", "confidence") or value in (None, "", [], {}):
            continue
        part = _humanize_value(value)
        if part:
            readable.append(part)
        if len(readable) >= 3:
            break
    if readable:
        return _compact_text(" ".join(readable), 260)
    return _compact_text(json.dumps(attrs, ensure_ascii=False), 260)


def _normalize_confidence(value) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _predicate_label(predicate: str) -> str:
    labels = {
        "belongs_to_domain": "属于领域",
        "related_to": "相关",
        "depends_on": "依赖",
        "works_on": "正在推进",
        "uses": "使用",
        "prefers": "偏好",
        "has_goal": "目标",
        "has_decision": "决策",
        "learned_from": "经验来自",
        "learned": "学习到",
        "derived_from": "源自",
        "consolidated_to": "归并为",
        "assigned_to": "分配给",
        "mentions": "提到",
        "involves": "涉及",
    }
    return labels.get(predicate, predicate.replace("_", " "))


def _normalize_concept(path: Path) -> dict | None:
    decoded = _decode_anda_cbor(path)
    fields = decoded.get("f", decoded)
    if not isinstance(fields, dict):
        return None

    concept_id = _field(fields, 0)
    concept_type = str(_field(fields, 1, "") or "")
    name = str(_field(fields, 2, "") or "")
    if name.startswith(("SleepTask:", "Maintenance:", "SystemTask:")):
        return None
    attrs = _field(fields, 3, {}) or {}
    meta = _field(fields, 4, {}) or {}
    if not isinstance(attrs, dict):
        attrs = {}
    if not isinstance(meta, dict):
        meta = {}

    summary = _metadata_summary(
        attrs,
        ["description", "correction", "resolution", "context", "trigger", "summary", "notes"],
    )
    if not summary:
        summary = _fallback_attr_summary(attrs)

    title = name
    if name == "$self":
        title = "用户画像与长期偏好"
    if concept_type.lower() == "insight" and summary:
        title = _compact_text(summary, 46)
    elif name.startswith(("Event:", "Preference:", "Observation:")) and summary:
        title = _compact_text(summary, 46)
    if str(title).strip().startswith(("{", "[")):
        title = _slug_title(name, concept_type)
    if str(summary).strip().startswith(("{", "[")):
        summary = _slug_title(name, concept_type)

    tags = [tag for tag in [concept_type, attrs.get("insight_class"), attrs.get("resolution_date")] if tag]
    source = meta.get("source", "")
    observed_at = meta.get("observed_at", "")
    confidence = _normalize_confidence(attrs.get("confidence") or meta.get("confidence"))
    if confidence:
        tags.append(f"confidence {confidence}")

    return {
        "id": f"C:{concept_id}",
        "title": title or f"概念 {concept_id}",
        "content": summary or _compact_text(json.dumps(attrs, ensure_ascii=False), 260),
        "source": source,
        "updated_at": observed_at or datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "tags": tags,
        "raw_name": name,
        "kind": "concept",
    }


def _concept_name_map(root: Path) -> dict[str, str]:
    data_dir = root / "concepts" / "data"
    names = {}
    if not data_dir.exists():
        return names
    for path in sorted(data_dir.glob("*.cbor")):
        try:
            item = _normalize_concept(path)
        except Exception:
            continue
        if item:
            raw_name = item.get("raw_name") or ""
            if raw_name.startswith(("Insight:", "Event:", "Preference:", "Observation:")) or raw_name == "$self":
                names[item["id"]] = item["title"]
            else:
                names[item["id"]] = raw_name or item["title"]
    return names


def _normalize_proposition(path: Path, concept_names: dict[str, str]) -> dict | None:
    decoded = _decode_anda_cbor(path)
    fields = decoded.get("f", decoded)
    if not isinstance(fields, dict):
        return None

    prop_id = _field(fields, 0)
    subject_id = str(_field(fields, 1, "") or "")
    object_id = str(_field(fields, 2, "") or "")
    predicates = _field(fields, 3, []) or []
    details = _field(fields, 4, {}) or {}
    predicate = str(predicates[0] if predicates else "related_to")
    relation = details.get(predicate, {}) if isinstance(details, dict) else {}
    relation_meta = relation.get("m", {}) if isinstance(relation, dict) else {}
    if not isinstance(relation_meta, dict):
        relation_meta = {}

    subject_name = concept_names.get(subject_id, subject_id)
    object_name = concept_names.get(object_id, object_id)
    if (
        subject_name.startswith("C:")
        or object_name.startswith("C:")
        or subject_name == "$system"
        or object_name == "$system"
    ):
        return None
    if subject_name.startswith(("SleepTask:", "Maintenance:", "SystemTask:")) or object_name.startswith(("SleepTask:", "Maintenance:", "SystemTask:")):
        return None
    label = _predicate_label(predicate)
    confidence = _normalize_confidence(relation_meta.get("confidence"))
    source = relation_meta.get("source", "")
    observed_at = relation_meta.get("observed_at", "")
    content = f"{subject_name} {label} {object_name}。"
    if source:
        content += f" 来源：{source}。"
    if confidence:
        content += f" 置信度：{confidence}。"

    return {
        "id": f"P:{prop_id}",
        "title": _compact_text(f"{subject_name} → {label} → {object_name}", 96),
        "content": _compact_text(content, 260),
        "source": source,
        "updated_at": observed_at or datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "tags": [predicate],
        "kind": "proposition",
    }


def _browse_anda_graph(index: str, query: str, limit: int, offset: int) -> dict | None:
    root = _anda_data_root()
    if root is None:
        return None

    collection = "concepts" if index == "anda_concepts" else "propositions"
    data_dir = root / collection / "data"
    if not data_dir.exists():
        return None

    concept_names = _concept_name_map(root) if collection == "propositions" else {}
    results = []
    for path in sorted(data_dir.glob("*.cbor")):
        try:
            item = _normalize_concept(path) if collection == "concepts" else _normalize_proposition(path, concept_names)
        except Exception:
            continue
        if not item:
            continue
        haystack = " ".join([
            item.get("title", ""),
            item.get("content", ""),
            item.get("raw_name", ""),
            item.get("source", ""),
            " ".join(item.get("tags", [])),
        ]).lower()
        if query.strip() and query.strip().lower() not in haystack:
            continue
        results.append(item)

    results = sorted(results, key=_doc_freshness, reverse=True)
    total = len(results)
    page = results[offset : offset + limit]
    return {
        "ok": True,
        "index": index,
        "source": "anda-cbor",
        "meta": _browse_meta(index),
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + len(page),
        "has_more": offset + len(page) < total,
        "results": page,
    }


def _compact_anda_overview(info: dict, conversations: list[dict], limit: int) -> dict:
    formation_usage = info.get("formation_usage", {})
    recall_usage = info.get("recall_usage", {})
    recent = sorted(
        conversations,
        key=lambda row: row.get("created_at") or row.get("updated_at") or row.get("_id", 0),
        reverse=True,
    )[:limit]
    recent_batches = []
    for row in recent:
        recent_batches.append({
            "id": row.get("_id"),
            "label": row.get("label", "formation"),
            "status": row.get("status", ""),
            "created_at": _ms_to_iso(row.get("created_at")),
            "message_count": len(row.get("messages", [])) if isinstance(row.get("messages"), list) else 0,
            "summary": "一次 AI 对话记忆写入批次，已提交给 Hippocampus 做概念与关系抽取。",
        })
    return {
        "overview_cards": [
            {
                "label": "图谱概念",
                "value": info.get("concepts", 0),
                "description": "系统抽取出的长期主题、项目、工具、人物、偏好等节点。",
            },
            {
                "label": "图谱命题",
                "value": info.get("propositions", 0),
                "description": "节点之间的事实、关系、判断与决策线索。",
            },
            {
                "label": "写入批次",
                "value": info.get("conversations", 0),
                "description": "被 formation 提交过的 AI 对话记忆批次。",
            },
            {
                "label": "召回次数",
                "value": recall_usage.get("requests", 0),
                "description": "通过 Hippocampus 做深度上下文召回的次数。",
            },
        ],
        "sections": [
            {
                "title": "这层在做什么",
                "items": [
                    "把 AI 对话日志中的项目背景、偏好、决策、长期主题抽成图谱。",
                    "适合回答“我为什么这么决定”“这个项目长期背景是什么”这类需要上下文的问题。",
                    "当前 Anda API 只暴露统计与 conversation 批次，不暴露可直接浏览的概念/命题明细。",
                ],
            },
            {
                "title": "和 Wiki 的区别",
                "items": [
                    "Wiki 是可读条目，偏主题笔记；Hippocampus 是慢召回图谱，偏关系和背景。",
                    "图谱概念/命题不等于文章列表，直接显示原始批次会很碎，所以 Dashboard 只展示概览。",
                ],
            },
        ],
        "usage_rows": [
            {"label": "Formation", "requests": formation_usage.get("requests", 0), "input_tokens": formation_usage.get("input_tokens", 0)},
            {"label": "Recall", "requests": recall_usage.get("requests", 0), "input_tokens": recall_usage.get("input_tokens", 0)},
        ],
        "recent_batches": recent_batches,
    }


def browse_index(index: str, query: str = "", limit: int = 20, offset: int = 0) -> dict:
    """浏览某个索引的具体内容"""
    headers = {"Authorization": f"Bearer {MEMORY_TOKEN}", "Content-Type": "application/json"}
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    if index in ("anda_concepts", "anda_propositions"):
        graph_data = _browse_anda_graph(index, query, limit, offset)
        if graph_data:
            return graph_data
        return {"ok": False, "error": "Anda 图谱明细文件不可读，请检查 ANDA_DATA_DIR 或 zstandard 依赖。"}

    if index in ("memory_chunks", "wiki_entries", "hubble_radius"):
        meili_data = _browse_meili_documents(index, query, limit, offset)
        if meili_data:
            return meili_data
        try:
            r = http_req.get(
                f"{MEMORY_URL}/search",
                params={"q": query, "index": index, "limit": limit, "offset": offset},
                headers=headers,
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", [])
                total = data.get("total", len(results))
                next_offset = offset + len(results)
                return {
                    "ok": True,
                    "index": index,
                    "source": "memory-api",
                    "meta": _browse_meta(index),
                    "total": total,
                    "limit": limit,
                    "offset": offset,
                    "next_offset": next_offset,
                    "has_more": next_offset < total and len(results) > 0,
                    "results": results,
                }
            else:
                return {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    elif index == "anda":
        try:
            # Use info + conversations list (fast, no LLM call)
            r = http_req.get(
                f"{ANDA_BASE_URL}/v1/{ANDA_SPACE_ID}/info",
                headers={"Authorization": f"Bearer {ANDA_SPACE_TOKEN}"},
                timeout=5,
            )
            if r.status_code == 200:
                info = r.json().get("result", {})
                # Also try to get conversations list
                r2 = http_req.get(
                    f"{ANDA_BASE_URL}/v1/{ANDA_SPACE_ID}/conversations",
                    headers={"Authorization": f"Bearer {ANDA_SPACE_TOKEN}"},
                    timeout=5,
                )
                conversations = []
                if r2.status_code == 200:
                    conversations = r2.json().get("result", [])
                return {
                    "ok": True,
                    "index": "anda",
                    "source": "anda-info",
                    "results": {
                        **_compact_anda_overview(info, conversations, limit),
                    }
                }
            else:
                return {"ok": False, "error": f"HTTP {r.status_code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return {"ok": False, "error": f"unknown index: {index}"}


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._serve_html()
        elif path == "/api/status":
            self._json_response(get_status())
        elif path == "/api/metrics":
            days = int(params.get("days", [7])[0])
            self._json_response(get_metrics(days))
        elif path == "/api/indexes":
            self._json_response(get_indexes())
        elif path == "/api/browse":
            index = params.get("index", [""])[0]
            query = params.get("q", [""])[0]
            limit = int(params.get("limit", [20])[0])
            offset = int(params.get("offset", [0])[0])
            self._json_response(browse_index(index, query, limit, offset=offset))
        else:
            self.send_error(404)

    def _serve_html(self):
        try:
            content = HTML_FILE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self.send_error(500, "dashboard_page.html not found")

    def _json_response(self, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Suppress default access logs (too noisy for auto-refresh)
        pass


def main():
    server = HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"[MyScope Dashboard] listening on http://0.0.0.0:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Dashboard] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
