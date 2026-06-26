#!/bin/bash
# myScope 每日采集任务 - 由 cron/launchd 触发
# 实际补跑逻辑交给 run_due_jobs.py，根据 last_success_at 判断该跑什么。

set -u

PROJECT_DIR="/Users/xz/Documents/myScope"
LOGS_DIR="$PROJECT_DIR/logs"
DATE=$(date +%Y-%m-%d)
LOG_PREFIX="[$DATE myscope-catchup]"

mkdir -p "$LOGS_DIR"
echo "$LOG_PREFIX 检查并补跑 MacBook 到期任务"

python3 "$PROJECT_DIR/scripts/run_due_jobs.py" --machine macbook >> "$LOGS_DIR/run_due_jobs.log" 2>&1
STATUS=$?

if [ "$STATUS" -eq 0 ]; then
  echo "$LOG_PREFIX 完成"
else
  echo "$LOG_PREFIX 有任务失败，exit=$STATUS"
fi

exit "$STATUS"
