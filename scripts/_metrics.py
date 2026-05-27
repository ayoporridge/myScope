"""
_metrics.py
公共指标工具：last_run 记录 + metrics.jsonl 追加
所有核心脚本在结束时调用这两个函数。
"""

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "logs"
LAST_RUN_FILE = LOGS_DIR / "last_run.json"
METRICS_FILE = LOGS_DIR / "metrics.jsonl"


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


def record_metrics(script_name: str, **kwargs):
    """追加一行指标到 logs/metrics.jsonl。
    自动添加 date、script、timestamp 字段。
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    entry = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "script": script_name,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        **kwargs,
    }

    with open(METRICS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
