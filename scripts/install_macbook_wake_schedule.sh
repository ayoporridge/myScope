#!/bin/bash
# Install a daily MacBook wake event so MyScope can catch up before health check.

set -euo pipefail

WAKE_TIME="${MYSCOPE_MACBOOK_WAKE_TIME:-06:05:00}"
WAKE_DAYS="${MYSCOPE_MACBOOK_WAKE_DAYS:-MTWRFSU}"

if [[ ! "$WAKE_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$ ]]; then
  echo "无效唤醒时间: $WAKE_TIME，应为 HH:MM:SS"
  exit 1
fi

if [[ ! "$WAKE_DAYS" =~ ^[MTWRFSU]+$ ]]; then
  echo "无效重复日期: $WAKE_DAYS，应由 MTWRFSU 组成"
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "安装 macOS 唤醒计划需要管理员权限，请执行："
  echo "  sudo bash scripts/install_macbook_wake_schedule.sh"
  exit 3
fi

existing_repeat="$(
  pmset -g sched | awk '
    /^Repeating power events:/ { flag = 1; next }
    /^Scheduled power events:/ { flag = 0 }
    flag && NF { print }
  '
)"

if [[ -n "$existing_repeat" && "${MYSCOPE_WAKE_FORCE:-}" != "1" ]]; then
  echo "已存在重复电源计划，未覆盖："
  echo "$existing_repeat"
  echo ""
  echo "如确认要改为 MyScope 唤醒计划，请执行："
  echo "  sudo env MYSCOPE_WAKE_FORCE=1 bash scripts/install_macbook_wake_schedule.sh"
  exit 2
fi

echo "安装 MyScope MacBook 唤醒计划：$WAKE_DAYS $WAKE_TIME"
pmset repeat wakeorpoweron "$WAKE_DAYS" "$WAKE_TIME"
pmset -g sched
