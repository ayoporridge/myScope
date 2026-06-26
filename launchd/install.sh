#!/bin/bash
# install.sh — 注册 launchd 定时任务
# 用法：
#   bash launchd/install.sh macmini   # 在 Mac mini 上运行
#   bash launchd/install.sh macbook   # 在 MacBook 上运行

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MACHINE="${1:-}"
PYTHON_BIN="$(command -v python3)"
PATH_VALUE="$HOME/.local/bin:$HOME/.local/nodejs/bin:$HOME/.nvm/versions/node/v20.20.1/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [[ -z "$MACHINE" || ! "$MACHINE" =~ ^(macmini|macbook)$ ]]; then
  echo "用法: bash launchd/install.sh [macmini|macbook]"
  exit 1
fi

PLIST_DIR="$SCRIPT_DIR/$MACHINE"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

echo "=== 安装 $MACHINE 定时任务 ==="
echo "项目目录: $PROJECT_DIR"
echo "Python: $PYTHON_BIN"
echo "PATH: $PATH_VALUE"
echo ""

# 选择要安装的 plist。MacBook 现在使用 run_due_jobs 统一补跑，
# 旧的 per-script plist 保留作参考，但默认不安装。
PLISTS=("$PLIST_DIR"/*.plist)
if [[ "$MACHINE" == "macbook" && -f "$PLIST_DIR/com.myscope.run-due-jobs.plist" ]]; then
  PLISTS=("$PLIST_DIR/com.myscope.run-due-jobs.plist")
fi

# 先卸载旧任务（如果有）。MacBook 现在使用 run_due_jobs 统一补跑，
# 这里清理历史 per-script LaunchAgent，避免重复采集。
for loaded in "$LAUNCH_AGENTS"/com.myscope.*.plist; do
  [[ -e "$loaded" ]] || continue
  old_label=$(grep -A1 '<key>Label</key>' "$loaded" | grep '<string>' | sed 's/.*<string>\(.*\)<\/string>/\1/')
  if [[ -n "$old_label" ]]; then
    echo "卸载已存在任务: $old_label"
    launchctl unload "$loaded" 2>/dev/null || true
  fi
done

for plist in "${PLISTS[@]}"; do
  label=$(grep -A1 '<key>Label</key>' "$plist" | grep '<string>' | sed 's/.*<string>\(.*\)<\/string>/\1/')
  if launchctl list "$label" &>/dev/null; then
    echo "卸载旧任务: $label"
    launchctl unload "$LAUNCH_AGENTS/$(basename "$plist")" 2>/dev/null || true
  fi
done

# 复制并替换 MYSCOPE_DIR
for plist in "${PLISTS[@]}"; do
  filename=$(basename "$plist")
  target="$LAUNCH_AGENTS/$filename"

  # 替换占位符
  sed \
    -e "s|MYSCOPE_DIR|$PROJECT_DIR|g" \
    -e "s|PYTHON_BIN|$PYTHON_BIN|g" \
    -e "s|PATH_VALUE|$PATH_VALUE|g" \
    "$plist" > "$target"

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
