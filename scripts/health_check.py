#!/usr/bin/env python3
"""
health_check.py
MyScope 健康检查：存活性 + 质量性 + 飞书告警
每天 06:30 运行（在 hippocampus_formation 之后）

通用版：通过 .env 中 MONITORED_SCRIPTS 配置监控列表。
告警方式：优先 Webhook（最可靠），备选 lark-cli。
"""

import json
import os
import shutil
import socket
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests as http_requests
except ImportError:
    http_requests = None

from dotenv import load_dotenv
try:
    from _metrics import record_last_run, record_metrics
except ImportError:  # pragma: no cover - package import path for tests
    from scripts._metrics import record_last_run, record_metrics

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# 强制清除所有代理，确保飞书 Webhook 直连
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy", "ALL_PROXY_ENV", "no_proxy", "NO_PROXY"):
    os.environ.pop(k, None)

LOGS_DIR = Path(__file__).parent.parent / "logs"
LAST_RUN_FILE = LOGS_DIR / "last_run.json"
METRICS_FILE = LOGS_DIR / "metrics.jsonl"

# 跨机器共享指标目录（git 追踪）
METRICS_SHARED_DIR = Path(__file__).parent.parent / "data" / "metrics"
JOB_STATUS_FILE = LOGS_DIR / "job_status.json"
JOB_STATUS_SHARED_DIR = Path(__file__).parent.parent / "data" / "job_status"
LOCAL_HOSTNAME = socket.gethostname().split(".")[0]

# 飞书告警配置
FEISHU_WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK_URL", "")
FEISHU_ALERT_USER_ID = os.environ.get("FEISHU_ALERT_USER_ID", "")
LARK_CLI = shutil.which("lark-cli") or "/usr/local/bin/lark-cli"
LARK_ENV = dict(os.environ, PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", ""))

# 监控列表：从环境变量读取（逗号分隔），或使用默认值
_default_scripts = "hippocampus_formation,layer1_rag,layer2_wiki,layer3_wechat,dayflow_sync"
MONITORED_SCRIPTS = [
    s.strip() for s in
    os.environ.get("MONITORED_SCRIPTS", _default_scripts).split(",")
    if s.strip()
]

LIVENESS_THRESHOLD_HOURS = 25


def _parse_time(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _merge_local_last_run(host_status: dict, local_last_run: dict):
    local_jobs = host_status.setdefault(LOCAL_HOSTNAME, {})
    for script, last_run_str in local_last_run.items():
        last_run_at = _parse_time(last_run_str)
        if not last_run_at:
            continue

        current = local_jobs.get(script, {})
        current_success_at = _parse_time(current.get("last_success_at"))
        if current_success_at and current_success_at >= last_run_at:
            continue

        event_times = [
            parsed
            for parsed in (
                _parse_time(current.get(key))
                for key in ("last_finished_at", "last_failure_at", "last_started_at")
            )
            if parsed is not None
        ]
        latest_event_at = max(event_times) if event_times else None
        entry = {
            **current,
            "last_success_at": last_run_str,
        }
        if latest_event_at is None or last_run_at >= latest_event_at:
            entry["status"] = "success"
            entry["last_finished_at"] = last_run_str
            entry.pop("last_error_summary", None)
        local_jobs[script] = entry


def _latest_metrics_by_job(metrics: list[dict]) -> dict[tuple[str, str], dict]:
    by_job = {}
    for metric in metrics:
        key = (metric.get("hostname", "unknown"), metric.get("script", "unknown"))
        current = by_job.get(key)
        if not current or str(metric.get("timestamp", "")) > str(current.get("timestamp", "")):
            by_job[key] = metric
    return by_job


def _layer1_chunks(metric: dict) -> int:
    if metric.get("script") == "layer1_flomo":
        return int(metric.get("chunks", 0) or 0)
    return int(metric.get("chunks_produced", 0) or 0)


# ── 飞书告警推送 ─────────────────────────────────────────────
def send_feishu_alert(message: str):
    """推送飞书告警。优先 Webhook，备选 lark-cli。"""
    # 方式一：curl 子进程（最可靠，绕过 Python SSL 兼容性问题）
    if FEISHU_WEBHOOK_URL:
        try:
            import shlex
            card = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": "MyScope 健康检查"},
                        "template": "red" if "🔴" in message else "orange" if "🟡" in message else "green",
                    },
                    "elements": [
                        {"tag": "markdown", "content": message}
                    ],
                }
            }
            result = subprocess.run(
                ["curl", "-s", "-X", "POST", FEISHU_WEBHOOK_URL,
                 "-H", "Content-Type: application/json",
                 "-d", json.dumps(card, ensure_ascii=False),
                 "--connect-timeout", "10", "--max-time", "20"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0 and '"code":0' in result.stdout:
                print("  [飞书 Webhook] 告警已推送")
                return
            else:
                print(f"  [飞书 Webhook] 响应异常: {result.stdout[:200]}")
        except Exception as e:
            print(f"  [飞书 Webhook] 推送异常: {e}")

    # 方式二：lark-cli 备选
    if FEISHU_ALERT_USER_ID:
        try:
            result = subprocess.run(
                [LARK_CLI, "im", "+messages-send",
                 "--as", "bot",
                 "--user-id", FEISHU_ALERT_USER_ID,
                 "--markdown", message],
                capture_output=True, text=True, timeout=30, env=LARK_ENV,
            )
            if result.returncode == 0:
                print("  [飞书 CLI] 告警已推送")
            else:
                print(f"  [飞书 CLI] 推送失败: {result.stdout[:200]}")
        except Exception as e:
            print(f"  [飞书 CLI] 推送异常: {e}")
        return

    # 都没配置
    print(f"  [告警] 未配置推送方式，仅打印：")
    print(f"  {message}")


# ── 层级 1：存活性检查 ────────────────────────────────────────
def check_liveness() -> list[str]:
    """检查每个脚本的最后运行时间"""
    alerts = []
    host_status = {}

    if JOB_STATUS_SHARED_DIR.exists():
        for path in JOB_STATUS_SHARED_DIR.glob("*.json"):
            try:
                host_status[path.stem] = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
    if JOB_STATUS_FILE.exists():
        try:
            for host, jobs in json.loads(JOB_STATUS_FILE.read_text()).items():
                host_status[host] = {**host_status.get(host, {}), **jobs}
        except (json.JSONDecodeError, OSError):
            pass

    local_last_run = {}
    if LAST_RUN_FILE.exists():
        try:
            local_last_run = json.loads(LAST_RUN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            alerts.append("⚠️ `last_run.json` 解析失败")
            local_last_run = {}

    if local_last_run:
        _merge_local_last_run(host_status, local_last_run)

    now = datetime.now()
    threshold = timedelta(hours=LIVENESS_THRESHOLD_HOURS)

    for host, jobs in host_status.items():
        for script in MONITORED_SCRIPTS:
            info = jobs.get(script)
            if not info:
                continue
            last_run_str = info.get("last_success_at") or info.get("last_finished_at")
            if not last_run_str:
                alerts.append(f"🔴 `{host}` `{script}` 无成功运行记录")
                continue
            try:
                last_run = datetime.fromisoformat(last_run_str)
                gap = now - last_run
                if gap > threshold:
                    hours_ago = round(gap.total_seconds() / 3600, 1)
                    alerts.append(f"🔴 `{host}` `{script}` 已 {hours_ago}h 未成功运行（上次：{last_run_str}）")
                if info.get("status") == "failure":
                    err = info.get("last_error_summary", "")
                    alerts.append(f"🔴 `{host}` `{script}` 最近失败：{err}")
            except (ValueError, TypeError):
                alerts.append(f"⚠️ `{host}` `{script}` 时间格式异常：{last_run_str}")

    if not local_last_run:
        if host_status:
            return alerts
        return ["⚠️ `last_run.json` 不存在，所有脚本可能从未成功运行过"]

    for script in MONITORED_SCRIPTS:
        if host_status.get(LOCAL_HOSTNAME, {}).get(script):
            continue
        last_run_str = local_last_run.get(script)
        if not last_run_str:
            alerts.append(f"🔴 `{LOCAL_HOSTNAME}` `{script}` 从未记录运行时间")
            continue
        try:
            last_run = datetime.fromisoformat(last_run_str)
            gap = now - last_run
            if gap > threshold:
                hours_ago = round(gap.total_seconds() / 3600, 1)
                alerts.append(f"🔴 `{LOCAL_HOSTNAME}` `{script}` 已 {hours_ago}h 未运行（上次：{last_run_str}）")
        except (ValueError, TypeError):
            alerts.append(f"⚠️ `{LOCAL_HOSTNAME}` `{script}` 时间格式异常：{last_run_str}")

    return alerts


# ── 层级 2：质量性检查 ────────────────────────────────────────
def _load_metrics(days: int = 3) -> list[dict]:
    """从 data/metrics/*.jsonl（所有机器）和 logs/metrics.jsonl（本地）加载近期指标。"""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    seen_keys = set()  # (hostname, date, script, timestamp) 去重
    entries = []

    def _read_jsonl(path: Path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("date", "") < cutoff:
                            continue
                        key = (
                            entry.get("hostname", path.stem),
                            entry.get("date", ""),
                            entry.get("script", ""),
                            entry.get("timestamp", ""),
                        )
                        if key not in seen_keys:
                            seen_keys.add(key)
                            entries.append(entry)
                    except json.JSONDecodeError:
                        continue
        except OSError:
            pass

    # 1. 读所有机器的共享指标
    if METRICS_SHARED_DIR.exists():
        for f in METRICS_SHARED_DIR.glob("*.jsonl"):
            _read_jsonl(f)

    # 2. 读本机本地指标（兼容过渡期，去重后不会重复）
    _read_jsonl(METRICS_FILE)

    return entries


def check_quality() -> list[str]:
    """从多机器 metrics 检查近期质量指标"""
    alerts = []

    recent_metrics = _load_metrics(days=3)
    if not recent_metrics:
        return []

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # 检查 1：hippocampus formation 有效消息数（跨机器聚合）
    formation_metrics = [
        m for m in recent_metrics
        if m.get("script") == "hippocampus_formation"
        and m.get("date") in (today, yesterday)
    ]
    if formation_metrics:
        # 按机器汇总当天最新一条
        by_host = {}
        for m in formation_metrics:
            host = m.get("hostname", "unknown")
            by_host[host] = m  # 同机器同天多条，取最后一条
        total_meaningful = sum(
            m.get("meaningful_messages", 0) for m in by_host.values()
        )
        if total_meaningful < 5:
            detail = "、".join(
                f"{h}={m.get('meaningful_messages', 0)}"
                for h, m in by_host.items()
            )
            alerts.append(
                f"🟡 `hippocampus_formation` 有效消息仅 {total_meaningful} 条（阈值 5）[{detail}]"
            )
        # 检查各机器提交是否成功
        for host, m in by_host.items():
            batches_success = m.get("batches_success", 0)
            batches_total = m.get("batches_total", 0)
            if batches_total > 0 and batches_success < batches_total:
                alerts.append(
                    f"🔴 `{host}` hippocampus_formation 提交失败（{batches_success}/{batches_total} 批成功）"
                )

    # 检查 2：memory_chunks 新增（Layer 1 跨机器聚合）
    layer1_metrics = [
        m for m in recent_metrics
        if m.get("script") in ("layer1_rag", "layer1_flomo")
        and m.get("date") in (today, yesterday)
    ]
    if layer1_metrics:
        latest_by_job = _latest_metrics_by_job(layer1_metrics)
        total_chunks = sum(_layer1_chunks(m) for m in latest_by_job.values())
        if total_chunks < 3:
            detail = "、".join(
                f"{script}@{host}={_layer1_chunks(metric)}"
                for (host, script), metric in sorted(latest_by_job.items())
            )
            alerts.append(
                f"🟡 `layer1` 新增切片仅 {total_chunks} 条（阈值 3）[{detail}]"
            )

    # 检查 3：LLM provider 是否失败（余额、Key 或模型服务异常）
    llm_metrics = [
        m for m in recent_metrics
        if m.get("script") in ("layer1_rag", "layer1_flomo", "layer2_wiki")
        and m.get("date") in (today, yesterday)
        and (m.get("llm_errors", 0) or m.get("llm_error_summary"))
    ]
    if llm_metrics:
        by_job = {}
        for m in llm_metrics:
            key = (m.get("hostname", "unknown"), m.get("script", "unknown"))
            by_job[key] = m
        for (host, script), metric in by_job.items():
            summary = str(metric.get("llm_error_summary") or "LLM 调用失败").strip()
            alerts.append(f"🔴 `{host}` `{script}` LLM 调用失败：{summary[:180]}")

    collect_metrics = [
        m for m in recent_metrics
        if m.get("script") == "layer1_flomo"
        and m.get("date") in (today, yesterday)
        and (m.get("collect_errors", 0) or m.get("collect_error_summary"))
    ]
    if collect_metrics:
        by_host = {}
        for m in collect_metrics:
            by_host[m.get("hostname", "unknown")] = m
        for host, metric in by_host.items():
            summary = str(metric.get("collect_error_summary") or "flomo 采集失败").strip()
            alerts.append(f"🔴 `{host}` `layer1_flomo` 采集失败：{summary[:180]}")

    # 检查 4：wiki_entries 连续 3 天无新增（跨机器聚合）
    wiki_metrics = [
        m for m in recent_metrics
        if m.get("script") == "layer2_wiki"
    ]
    if wiki_metrics:
        recent_wiki_writes = sum(
            m.get("wiki_entries_written", 0) for m in wiki_metrics
        )
        if recent_wiki_writes == 0 and len(wiki_metrics) >= 3:
            alerts.append("🟡 `layer2_wiki` 连续 3 天无新增 Wiki 条目")

    return alerts


# ── 主流程 ────────────────────────────────────────────────
def main():
    started = time.time()
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] MyScope 健康检查")
    print(f"  监控列表: {MONITORED_SCRIPTS}")

    all_alerts = []

    liveness_alerts = check_liveness()
    if liveness_alerts:
        print(f"  存活性告警：{len(liveness_alerts)} 项")
        all_alerts.extend(liveness_alerts)
    else:
        print("  ✅ 存活性检查通过")

    quality_alerts = check_quality()
    if quality_alerts:
        print(f"  质量告警：{len(quality_alerts)} 项")
        all_alerts.extend(quality_alerts)
    else:
        print("  ✅ 质量检查通过")

    if all_alerts:
        header = f"**🏥 MyScope 健康检查** ({datetime.now().strftime('%m-%d %H:%M')})\n\n"
        body = "\n".join(f"- {a}" for a in all_alerts)
        send_feishu_alert(header + body)
    else:
        if datetime.now().weekday() == 0:  # Monday
            send_feishu_alert(
                f"**✅ MyScope 健康检查周报** ({datetime.now().strftime('%m-%d')})\n\n"
                "所有脚本正常运行，指标达标。"
            )
        else:
            print("  全部正常，无需推送")

    record_last_run("health_check")
    record_metrics(
        "health_check",
        alerts=len(all_alerts),
        liveness_alerts=len(liveness_alerts),
        quality_alerts=len(quality_alerts),
        run_duration_seconds=round(time.time() - started, 1),
    )
    print("[完成] 健康检查结束")


if __name__ == "__main__":
    main()
