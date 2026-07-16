"""
_metrics.py
公共指标工具：last_run 记录 + metrics.jsonl 追加
所有核心脚本在结束时调用这两个函数。

双机架构：
- logs/metrics.jsonl         → 本机本地指标（gitignored，仅供本机 health_check）
- data/metrics/<hostname>.jsonl → 跨机器共享指标（本地运行态，health_check 聚合所有机器）
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import time
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs"
LAST_RUN_FILE = LOGS_DIR / "last_run.json"
METRICS_FILE = LOGS_DIR / "metrics.jsonl"
JOB_STATUS_FILE = LOGS_DIR / "job_status.json"
JOB_EVENTS_FILE = LOGS_DIR / "job_events.jsonl"

# 跨机器共享指标目录（本地运行态，git 忽略）
METRICS_SHARED_DIR = Path(__file__).parent.parent / "data" / "metrics"
HOSTNAME = socket.gethostname().split(".")[0]  # e.g. "xizhouMINIdeMac-mini"
METRICS_SHARED_FILE = METRICS_SHARED_DIR / f"{HOSTNAME}.jsonl"
JOB_STATUS_SHARED_DIR = Path(__file__).parent.parent / "data" / "job_status"
JOB_STATUS_SHARED_FILE = JOB_STATUS_SHARED_DIR / f"{HOSTNAME}.json"


def record_last_run(script_name: str):
    """更新 logs/last_run.json 中该脚本的最后运行时间戳。
    使用原子写（tmp + rename）避免并发竞争。
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # 读取已有数据
    data = {}
    if LAST_RUN_FILE.exists():
        try:
            data = json.loads(LAST_RUN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}

    data[script_name] = datetime.now().isoformat(timespec="seconds")

    # 原子写
    fd, tmp_path = tempfile.mkstemp(dir=str(LOGS_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(LAST_RUN_FILE))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default


def _atomic_write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def record_job_result(
    script_name: str,
    status: str,
    *,
    started_at: str | None = None,
    finished_at: str | None = None,
    exit_code: int | None = None,
    duration_seconds: float | None = None,
    input_count: int | None = None,
    output_count: int | None = None,
    success_count: int | None = None,
    error_summary: str | None = None,
    extra: dict | None = None,
):
    """Record a job outcome for dashboard/health checks.

    `last_run.json` keeps backward-compatible success timestamps. The richer
    status file stores both last success and last failure per host/script.
    """
    if status not in {"running", "success", "failure", "skipped"}:
        raise ValueError(f"unknown job status: {status}")

    now = datetime.now().isoformat(timespec="seconds")
    finished_at = finished_at or now
    host_status = _read_json(JOB_STATUS_FILE, {})
    host_status.setdefault(HOSTNAME, {})

    current = host_status[HOSTNAME].get(script_name, {})
    entry = {
        **current,
        "status": status,
        "last_started_at": started_at or current.get("last_started_at") or finished_at,
        "last_finished_at": finished_at,
        "last_exit_code": exit_code,
        "last_duration_seconds": duration_seconds,
        "last_input_count": input_count,
        "last_output_count": output_count,
        "last_success_count": success_count,
    }
    if extra:
        entry["extra"] = extra

    if status == "success":
        entry["last_success_at"] = finished_at
        entry.pop("last_error_summary", None)
        record_last_run(script_name)
    elif status == "failure":
        entry["last_failure_at"] = finished_at
        entry["last_error_summary"] = (error_summary or "").strip()[:500]
    elif status == "running":
        entry["last_running_at"] = finished_at
    elif status == "skipped":
        entry["last_skipped_at"] = finished_at
        if error_summary:
            entry["last_skip_reason"] = error_summary[:500]

    host_status[HOSTNAME][script_name] = entry
    _atomic_write_json(JOB_STATUS_FILE, host_status)
    _atomic_write_json(JOB_STATUS_SHARED_FILE, host_status[HOSTNAME])

    event = {
        "timestamp": finished_at,
        "hostname": HOSTNAME,
        "script": script_name,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "exit_code": exit_code,
        "duration_seconds": duration_seconds,
        "input_count": input_count,
        "output_count": output_count,
        "success_count": success_count,
        "error_summary": (error_summary or "").strip()[:500],
        **(extra or {}),
    }
    JOB_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(JOB_EVENTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def record_metrics(script_name: str, **kwargs):
    """追加一行指标到 logs/metrics.jsonl 和 data/metrics/<hostname>.jsonl。
    自动添加 date、script、timestamp、hostname 字段。
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    METRICS_SHARED_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "script": script_name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "hostname": HOSTNAME,
        **kwargs,
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"

    # 写本地 logs/
    with open(METRICS_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    # 写共享 data/metrics/<hostname>.jsonl
    with open(METRICS_SHARED_FILE, "a", encoding="utf-8") as f:
        f.write(line)
