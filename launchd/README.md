# launchd 定时任务

## 两套任务

### macmini/ — 定时任务

每天夜间/清晨自动运行，适合 Mac mini（24/7 开机）。

| 时间 | 脚本 | 说明 |
|------|------|------|
| 02:30 | layer3_index | FreshRSS → hubble_radius |
| 19:10 | layer1_flomo | flomo → memory_chunks（DeepSeek） |
| 06:00 | hippocampus_formation | Codex/Hermes/Clacky → Anda |
| 07:20 | layer2_wiki | 跨层综合 → wiki_entries（DeepSeek） |
| 06:30 | health_check | 健康检查 → 飞书告警 |

### macbook/ — catch-up runner

登录时运行，并每小时检查一次。实际执行由 `run_due_jobs.py --machine macbook` 判断，不依赖 launchd/cron 补偿错过的固定时间点。

DeepSeek 相关任务（`layer1_rag`、`layer1_flomo`、`layer2_wiki`）只会在 `19:00` 到次日 `08:00` 之间由 runner 启动。

| plist | 说明 |
|------|------|
| com.myscope.run-due-jobs | 统一检查并补跑 MacBook 到期任务 |

## 安装

```bash
# Mac mini 上
bash launchd/install.sh macmini

# MacBook 上（只安装 run-due-jobs runner）
bash launchd/install.sh macbook
```

脚本会自动：
1. 把 plist 中的 `MYSCOPE_DIR` 替换为实际项目路径
2. 复制到 `~/Library/LaunchAgents/`
3. 通过 `launchctl load` 注册

## 卸载

```bash
# 卸载所有 myscope 任务
for f in ~/Library/LaunchAgents/com.myscope.*.plist; do
  launchctl unload "$f" 2>/dev/null
  rm "$f"
done
```

## 查看状态

```bash
# 列出已注册任务
launchctl list | grep myscope

# 查看日志和统一状态
tail -f ~/Documents/myScope/logs/run_due_jobs.log
cat ~/Documents/myScope/logs/job_status.json
```
