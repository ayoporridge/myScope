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
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

try:
    import requests as http_requests
except ImportError:
    http_requests = None

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

# 强制清除所有代理，确保飞书 Webhook 直连
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
          "ALL_PROXY", "all_proxy", "ALL_PROXY_ENV", "no_proxy", "NO_PROXY"):
    os.environ.pop(k, None)

LOGS_DIR = Path(__file__).parent.parent / "logs"
LAST_RUN_FILE = LOGS_DIR / "last_run.json"
METRICS_FILE = LOGS_DIR / "metrics.jsonl"

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

    if not LAST_RUN_FILE.exists():
        return ["⚠️ `last_run.json` 不存在，所有脚本可能从未成功运行过"]

    try:
        data = json.loads(LAST_RUN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return ["⚠️ `last_run.json` 解析失败"]

    now = datetime.now()
    threshold = timedelta(hours=LIVENESS_THRESHOLD_HOURS)

    for script in MONITORED_SCRIPTS:
        last_run_str = data.get(script)
        if not last_run_str:
            alerts.append(f"🔴 `{script}` 从未记录运行时间")
            continue
        try:
            last_run = datetime.fromisoformat(last_run_str)
            gap = now - last_run
            if gap > threshold:
                hours_ago = round(gap.total_seconds() / 3600, 1)
                alerts.append(f"🔴 `{script}` 已 {hours_ago}h 未运行（上次：{last_run_str}）")
        except (ValueError, TypeError):
            alerts.append(f"⚠️ `{script}` 时间格式异常：{last_run_str}")

    return alerts


# ── 层级 2：质量性检查 ────────────────────────────────────────
def check_quality() -> list[str]:
    """从 metrics.jsonl 检查昨日质量指标"""
    alerts = []

    if not METRICS_FILE.exists():
        return []  # 刚部署没数据，不告警

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    three_days_ago = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    recent_metrics = []
    try:
        with open(METRICS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("date", "") >= three_days_ago:
                        recent_metrics.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return ["⚠️ `metrics.jsonl` 读取失败"]

    if not recent_metrics:
        return []

    # 检查 1：hippocampus formation 有效消息数 < 5
    formation_metrics = [
        m for m in recent_metrics
        if m.get("script") == "hippocampus_formation"
        and m.get("date") in (today, yesterday)
    ]
    if formation_metrics:
        latest = formation_metrics[-1]
        meaningful = latest.get("meaningful_messages", 0)
        if meaningful < 5:
            alerts.append(
                f"🟡 `hippocampus_formation` 有效消息仅 {meaningful} 条（阈值 5）"
            )
        # 检查提交是否成功
        batches_success = latest.get("batches_success", 0)
        batches_total = latest.get("batches_total", 0)
        if batches_total > 0 and batches_success < batches_total:
            alerts.append(
                f"🔴 `hippocampus_formation` 提交失败（{batches_success}/{batches_total} 批成功）"
            )

    # 检查 2：memory_chunks 新增 < 3 条
    rag_metrics = [
        m for m in recent_metrics
        if m.get("script") == "layer1_rag"
        and m.get("date") in (today, yesterday)
    ]
    if rag_metrics:
        latest = rag_metrics[-1]
        chunks = latest.get("chunks_produced", 0)
        if chunks < 3:
            alerts.append(
                f"🟡 `layer1_rag` 新增切片仅 {chunks} 条（阈值 3）"
            )

    # 检查 3：wiki_entries 连续 3 天无新增
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

    print("[完成] 健康检查结束")


if __name__ == "__main__":
    main()
