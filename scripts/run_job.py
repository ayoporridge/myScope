#!/usr/bin/env python3
"""
run_job.py
统一运行包装器：加锁、超时、记录 job_status/job_events。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from _metrics import record_job_result, record_metrics
except ImportError:  # pragma: no cover - package import path for tests
    from scripts._metrics import record_job_result, record_metrics


PROJECT_DIR = Path(__file__).parent.parent
LOGS_DIR = PROJECT_DIR / "logs"
LOCKS_DIR = LOGS_DIR / "locks"


def acquire_lock(name: str) -> Path | None:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = LOCKS_DIR / f"{name}.lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{os.getpid()}\n{datetime.now().isoformat(timespec='seconds')}\n")
        return lock_path
    except FileExistsError:
        return None


def release_lock(lock_path: Path | None):
    if not lock_path:
        return
    try:
        lock_path.unlink()
    except OSError:
        pass


def run_job(name: str, command: list[str], timeout: int, cwd: Path) -> int:
    start = time.time()
    started_at = datetime.now().isoformat(timespec="seconds")
    lock_path = acquire_lock(name)
    if lock_path is None:
        message = "job already running"
        print(f"[run_job] {name}: {message}", file=sys.stderr)
        record_job_result(name, "skipped", started_at=started_at, exit_code=75, error_summary=message)
        return 75

    record_job_result(name, "running", started_at=started_at)
    try:
        result = subprocess.run(command, cwd=str(cwd), timeout=timeout)
        duration = round(time.time() - start, 1)
        status = "success" if result.returncode == 0 else "failure"
        error_summary = "" if result.returncode == 0 else f"exit code {result.returncode}"
        record_job_result(
            name,
            status,
            started_at=started_at,
            exit_code=result.returncode,
            duration_seconds=duration,
            error_summary=error_summary,
        )
        record_metrics(
            "job_runner",
            job=name,
            status=status,
            exit_code=result.returncode,
            run_duration_seconds=duration,
        )
        return result.returncode
    except subprocess.TimeoutExpired:
        duration = round(time.time() - start, 1)
        error_summary = f"timeout after {timeout}s"
        record_job_result(
            name,
            "failure",
            started_at=started_at,
            exit_code=124,
            duration_seconds=duration,
            error_summary=error_summary,
        )
        record_metrics(
            "job_runner",
            job=name,
            status="failure",
            exit_code=124,
            run_duration_seconds=duration,
        )
        print(f"[run_job] {name}: {error_summary}", file=sys.stderr)
        return 124
    finally:
        release_lock(lock_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a MyScope job with status tracking")
    parser.add_argument("--name", required=True)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--cwd", default=str(PROJECT_DIR))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("command is required after --")

    return run_job(args.name, command, args.timeout, Path(args.cwd))


if __name__ == "__main__":
    raise SystemExit(main())
