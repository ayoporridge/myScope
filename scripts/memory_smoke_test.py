#!/usr/bin/env python3
"""
memory_smoke_test.py
轻量验证 MyScope 的三个搜索入口和 Hippocampus recall 是否可用。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

try:
    from _metrics import record_job_result, record_last_run, record_metrics
except ImportError:  # pragma: no cover - package import path for tests
    from scripts._metrics import record_job_result, record_last_run, record_metrics


load_dotenv(Path(__file__).parent.parent / ".env")

MEMORY_URL = os.environ.get("MEMORY_API_URL", "https://memory.arjo.us.ci").rstrip("/")
MEMORY_TOKEN = os.environ.get("MEMORY_API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {MEMORY_TOKEN}"} if MEMORY_TOKEN else {}


def default_checks() -> list[dict]:
    return [
        {
            "name": "recent_personal_memory",
            "kind": "search",
            "index": "memory_chunks",
            "query": "我最近在做什么项目 myScope Dayflow",
        },
        {
            "name": "hubble_radius",
            "kind": "search",
            "index": "hubble_radius",
            "query": "AI Agent 公众号",
        },
        {
            "name": "wiki_entries",
            "kind": "search",
            "index": "wiki_entries",
            "query": "AI Agent myScope 知识管理",
        },
        {
            "name": "hippocampus_recall",
            "kind": "recall",
            "query": "我为什么决定做 myScope 个人 AI 记忆系统？",
        },
    ]


def run_check(check: dict) -> dict:
    start = time.time()
    if check["kind"] == "search":
        resp = requests.get(
            f"{MEMORY_URL}/search",
            params={"q": check["query"], "index": check["index"], "limit": 3},
            headers=HEADERS,
            timeout=20,
        )
    else:
        resp = requests.get(
            f"{MEMORY_URL}/recall",
            params={"q": check["query"]},
            headers=HEADERS,
            timeout=90,
        )
    latency = round((time.time() - start) * 1000)
    resp.raise_for_status()
    data = resp.json()
    if check["kind"] == "search":
        result_count = len(data.get("results", []))
        ok = result_count > 0
    else:
        content = data.get("result", {}).get("content", "") or data.get("content", "")
        result_count = 1 if content else 0
        ok = bool(content)
    return {
        "name": check["name"],
        "kind": check["kind"],
        "ok": ok,
        "result_count": result_count,
        "latency_ms": latency,
    }


def main() -> int:
    started_at = datetime.now().isoformat(timespec="seconds")
    start = time.time()
    results = []
    failures = []
    for check in default_checks():
        try:
            result = run_check(check)
        except Exception as exc:
            result = {
                "name": check["name"],
                "kind": check["kind"],
                "ok": False,
                "result_count": 0,
                "latency_ms": None,
                "error": str(exc)[:300],
            }
        results.append(result)
        if not result["ok"]:
            failures.append(result["name"])

    print(json.dumps({"checks": results}, ensure_ascii=False, indent=2))
    duration = round(time.time() - start, 1)
    record_metrics(
        "memory_smoke_test",
        checks=len(results),
        failures=len(failures),
        run_duration_seconds=duration,
        results=results,
    )
    if failures:
        record_job_result(
            "memory_smoke_test",
            "failure",
            started_at=started_at,
            duration_seconds=duration,
            output_count=len(results),
            success_count=len(results) - len(failures),
            error_summary=", ".join(failures),
        )
        return 1
    record_last_run("memory_smoke_test")
    record_job_result(
        "memory_smoke_test",
        "success",
        started_at=started_at,
        duration_seconds=duration,
        output_count=len(results),
        success_count=len(results),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
