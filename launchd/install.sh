#!/bin/bash
# install.sh — 注册 launchd 定时任务
# 用法：
#   bash launchd/install.sh macmini   # 在 Mac mini 上运行
#   bash launchd/install.sh macbook   # 在 MacBook 上运行

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MACHINE="${1:-}"

if [[ -z "$MACHINE" || ! "$MACHINE" =~ ^(macmini|macbook)$ ]]; then
  echo "用法: bash launchd/install.sh [macmini|macbook]"
  exit 1
fi

PLIST_DIR="$SCRIPT_DIR/$MACHINE"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

echo "=== 安装 $MACHINE 定时任务 ==="
echo "项目目录: $PROJECT_DIR"
echo ""

# 先卸载旧任务（如果有）
for plist in "$PLIST_DIR"/*.plist; do
  label=$(grep -A1 '<key>Label</key>' "$plist" | grep '<string>' | sed 's/.*<string>\(.*\)<\/string>/\1/')
  if launchctl list "$label" &>/dev/null; then
    echo "卸载旧任务: $label"
    launchctl unload "$LAUNCH_AGENTS/$(basename "$plist")" 2>/dev/null || true
  fi
done

# 复制并替换 MYSCOPE_DIR
for plist in "$PLIST_DIR"/*.plist; do
  filename=$(basename "$plist")
  target="$LAUNCH_AGENTS/$filename"

  # 替换占位符
  sed "s|MYSCOPE_DIR|$PROJECT_DIR|g" "$plist" > "$target"

  # 加载
  label=$(grep -A1 '<key>Label</key>' "$target" | grep '<string>' | sed 's/.*<string>\(.*\)<\/string>/\1/')
  echo "加载: $label → $target"
  launchctl load "$target"
done

echo ""
echo "=== 完成 ==="
echo ""
echo "已注册的任务："
launchctl list | grep com.myscope || echo "（无）"
