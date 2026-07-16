#!/usr/bin/env python3
"""
run_due_jobs.py
按机器角色补跑到期任务。适合由 launchd RunAtLoad/StartInterval 或 cron 高频触发。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    from _metrics import JOB_STATUS_FILE, LAST_RUN_FILE, record_job_result, record_metrics
except ImportError:  # pragma: no cover - package import path for tests
    from scripts._metrics import JOB_STATUS_FILE, LAST_RUN_FILE, record_job_result, record_metrics


PROJECT_DIR = Path(__file__).parent.parent
PYTHON = sys.executable or "python3"
SYNC_DISABLED = os.environ.get("MYSCOPE_DASHBOARD_SYNC_DISABLED", "").lower() in {"1", "true", "yes"}
SYNC_PENDING_FILE = PROJECT_DIR / "logs" / "dashboard_state_sync.pending"
DEEPSEEK_JOBS = frozenset({"layer1_rag", "layer1_flomo", "layer2_wiki"})
DEEPSEEK_WINDOW_START = os.environ.get("MYSCOPE_DEEPSEEK_WINDOW_START", "19:00")
DEEPSEEK_WINDOW_END = os.environ.get("MYSCOPE_DEEPSEEK_WINDOW_END", "08:00")


@dataclass(frozen=True)
class Job:
    name: str
    command: tuple[str, ...]
    interval_hours: float
    timeout_seconds: int = 3600


MACHINE_JOBS: dict[str, tuple[Job, ...]] = {
    "macbook": (
        Job("dayflow_sync", (PYTHON, "scripts/dayflow_sync.py"), 6, 1800),
        Job("layer3_wechat", (PYTHON, "scripts/layer3_wechat.py"), 20, 1800),
        Job("layer1_rag", (PYTHON, "scripts/layer1_rag.py"), 20, 3600),
        Job("hippocampus_formation", (PYTHON, "scripts/hippocampus_formation.py"), 20, 3600),
        Job("dayflow_daily_summary", (PYTHON, "scripts/dayflow_daily_summary.py"), 20, 1800),
        Job("layer2_wiki", (PYTHON, "scripts/layer2_wiki.py"), 20, 3600),
    ),
    "macmini": (
        Job("layer3_index", (PYTHON, "scripts/layer3_index.py"), 20, 3600),
        Job("layer1_flomo", (PYTHON, "scripts/layer1_flomo.py"), 20, 3600),
        Job("layer2_wiki", (PYTHON, "scripts/layer2_wiki.py"), 20, 3600),
        Job("hippocampus_formation", (PYTHON, "scripts/hippocampus_formation.py"), 20, 3600),
        Job("health_check", (PYTHON, "scripts/health_check.py"), 20, 1200),
        Job("memory_smoke_test", (PYTHON, "scripts/memory_smoke_test.py"), 168, 1200),
        Job("source_audit", (PYTHON, "scripts/source_audit.py"), 720, 600),
    ),
}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def is_due(last_success_at: str | None, interval_hours: float, *, now: datetime | None = None) -> bool:
    if not last_success_at:
        return True
    now = now or datetime.now()
    try:
        last = datetime.fromisoformat(last_success_at)
    except (TypeError, ValueError):
        return True
    return (now - last).total_seconds() >= interval_hours * 3600


def _parse_hhmm(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (ValueError, AttributeError):
        raise ValueError(f"invalid time window value: {value!r}") from None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid time window value: {value!r}")
    return hour * 60 + minute


def is_deepseek_window(*, now: datetime | None = None) -> bool:
    """Return whether DeepSeek-backed jobs may start now."""
    now = now or datetime.now()
    current = now.hour * 60 + now.minute
    start = _parse_hhmm(DEEPSEEK_WINDOW_START)
    end = _parse_hhmm(DEEPSEEK_WINDOW_END)
    if start == end:
        return True
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _last_success(status: dict, hostname: str, job_name: str) -> str | None:
    host_jobs = status.get(hostname, {})
    rich = host_jobs.get(job_name, {})
    if isinstance(rich, dict) and rich.get("last_success_at"):
        return rich["last_success_at"]

    legacy = _read_json(LAST_RUN_FILE)
    return legacy.get(job_name)


def plan_due_jobs(
    machine: str,
    status: dict | None = None,
    *,
    hostname: str | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> list[Job]:
    if machine not in MACHINE_JOBS:
        raise ValueError(f"unknown machine: {machine}")
    status = status if status is not None else _read_json(JOB_STATUS_FILE)
    hostname = hostname or socket.gethostname().split(".")[0]
    now = now or datetime.now()

    planned = []
    for job in MACHINE_JOBS[machine]:
        if job.name in DEEPSEEK_JOBS and not force and not is_deepseek_window(now=now):
            continue
        if force or is_due(_last_success(status, hostname, job.name), job.interval_hours, now=now):
            planned.append(job)
    return planned


def _set_sync_pending(pending: bool):
    if pending:
        SYNC_PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        SYNC_PENDING_FILE.write_text(datetime.now().isoformat())
        return
    try:
        SYNC_PENDING_FILE.unlink()
    except FileNotFoundError:
        pass


def _sync_dashboard_state() -> tuple[str, int]:
    sync_cmd = [PYTHON, "scripts/sync_dashboard_state.py"]
    try:
        result = subprocess.run(sync_cmd, cwd=str(PROJECT_DIR), timeout=180)
        sync_exit_code = result.returncode
        sync_status = "success" if result.returncode == 0 else "failure"
    except subprocess.TimeoutExpired:
        sync_exit_code = 124
        sync_status = "timeout"
    if sync_status == "success":
        _set_sync_pending(False)
    else:
        _set_sync_pending(True)
        print(f"[run_due_jobs] dashboard state sync {sync_status}", file=sys.stderr)
    return sync_status, sync_exit_code


def run_due_jobs(
    machine: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    skip_noop_metrics: bool = False,
) -> int:
    hostname = socket.gethostname().split(".")[0]
    planned = plan_due_jobs(machine, hostname=hostname, force=force)
    print(json.dumps({
        "machine": machine,
        "hostname": hostname,
        "planned": [job.name for job in planned],
        "dry_run": dry_run,
        "force": force,
        "skip_noop_metrics": skip_noop_metrics,
    }, ensure_ascii=False), flush=True)

    if dry_run:
        return 0
    if not planned and skip_noop_metrics:
        if machine == "macbook" and not SYNC_DISABLED and SYNC_PENDING_FILE.exists():
            sync_status, sync_exit_code = _sync_dashboard_state()
            record_metrics(
                "dashboard_state_sync",
                machine=machine,
                status=sync_status,
                exit_code=sync_exit_code,
                pending_retry=True,
            )
        return 0

    failures = 0
    for job in planned:
        cmd = [
            PYTHON,
            "scripts/run_job.py",
            "--name",
            job.name,
            "--timeout",
            str(job.timeout_seconds),
            "--",
            *job.command,
        ]
        print(f"[run_due_jobs] running {job.name}", flush=True)
        result = subprocess.run(cmd, cwd=str(PROJECT_DIR))
        if result.returncode != 0:
            failures += 1

    record_metrics(
        "run_due_jobs",
        machine=machine,
        planned_jobs=len(planned),
        failures=failures,
    )
    record_job_result(
        "run_due_jobs",
        "success" if failures == 0 else "failure",
        output_count=len(planned),
        success_count=len(planned) - failures,
        error_summary="" if failures == 0 else f"{failures} job(s) failed",
        extra={"machine": machine},
    )
    sync_status = "disabled"
    sync_exit_code = 0
    if machine == "macbook" and not SYNC_DISABLED:
        sync_status, sync_exit_code = _sync_dashboard_state()
    record_metrics(
        "dashboard_state_sync",
        machine=machine,
        status=sync_status,
        exit_code=sync_exit_code,
    )
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Run due MyScope jobs for this machine")
    parser.add_argument("--machine", choices=sorted(MACHINE_JOBS), required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--skip-noop-metrics",
        action="store_true",
        help="Do not write run_due_jobs/dashboard sync metrics when no jobs are due.",
    )
    args = parser.parse_args()
    return run_due_jobs(
        args.machine,
        dry_run=args.dry_run,
        force=args.force,
        skip_noop_metrics=args.skip_noop_metrics,
    )


if __name__ == "__main__":
    raise SystemExit(main())
