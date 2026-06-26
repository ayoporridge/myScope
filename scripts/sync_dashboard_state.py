#!/usr/bin/env python3
"""
sync_dashboard_state.py
把当前机器写出的 Dashboard 共享状态同步到 Dashboard 所在主机。
"""

from __future__ import annotations

import argparse
import os
import shlex
import socket
import subprocess
from pathlib import Path


PROJECT_DIR = Path(__file__).parent.parent
HOSTNAME = socket.gethostname().split(".")[0]
DEFAULT_TARGET = os.environ.get("MYSCOPE_DASHBOARD_SYNC_TARGET", "macmini:/Users/xizhoumini/myScope")


def _split_target(target: str) -> tuple[str, str]:
    if ":" not in target:
        raise ValueError("target must look like host:/absolute/path")
    host, remote_root = target.split(":", 1)
    if not host or not remote_root.startswith("/"):
        raise ValueError("target must look like host:/absolute/path")
    return host, remote_root.rstrip("/")


def _run(cmd: list[str], *, timeout: int):
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{cmd[0]} exited {result.returncode}: {detail[:500]}")


def sync_dashboard_state(target: str = DEFAULT_TARGET) -> list[str]:
    host, remote_root = _split_target(target)
    files = [
        (PROJECT_DIR / "data" / "job_status" / f"{HOSTNAME}.json", "data/job_status"),
        (PROJECT_DIR / "data" / "metrics" / f"{HOSTNAME}.jsonl", "data/metrics"),
        (PROJECT_DIR / "data" / "formation_quality" / f"{HOSTNAME}.json", "data/formation_quality"),
    ]
    existing = [(path, subdir) for path, subdir in files if path.exists()]
    if not existing:
        raise FileNotFoundError(f"no dashboard state files found for {HOSTNAME}")

    remote_dirs = " ".join(shlex.quote(f"{remote_root}/{subdir}") for _, subdir in existing)
    _run(["ssh", host, f"mkdir -p {remote_dirs}"], timeout=30)

    synced = []
    for path, subdir in existing:
        _run(["rsync", "-az", str(path), f"{host}:{remote_root}/{subdir}/"], timeout=120)
        synced.append(str(path.relative_to(PROJECT_DIR)))
    return synced


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync local Dashboard state files to the dashboard host")
    parser.add_argument("--target", default=DEFAULT_TARGET)
    args = parser.parse_args()

    synced = sync_dashboard_state(args.target)
    print(f"synced {len(synced)} file(s): {', '.join(synced)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
