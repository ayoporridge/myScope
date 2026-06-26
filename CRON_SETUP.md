# myScope 采集任务配置

**更新日期**: 2026-06-24
**触发方式**: `run_due_jobs.py` catch-up runner（launchd/cron 均可触发）
**推荐频率**: MacBook 登录时 + 每小时检查一次

---

## 为什么不用单纯 cron？

**问题**: launchd 的 `StartCalendarInterval` 在 MacBook 合盖/睡眠时不会触发任务，导致 6/11-6/18 整整 8 天没有采集。

**修正后的方案**: 不再依赖 cron 自己补跑。由 `scripts/run_due_jobs.py` 读取 `logs/job_status.json` / `logs/last_run.json`，判断哪些任务超过间隔未成功运行，再逐个补跑。

cron 或 launchd 只负责“经常唤起检查器”，真正的补跑判断由 runner 完成。

---

## 采集脚本

### 执行顺序

`run_due_jobs.py --machine macbook` 会按依赖顺序执行到期任务：

- `layer1_rag.py` — 微信收藏 + Obsidian → memory_chunks
- `layer3_wechat.py` — 公众号文章 → hubble_radius
- `dayflow_sync.py` — Dayflow 屏幕活动 → memory_chunks
- `hippocampus_formation.py` — 对话记忆 → Anda 图谱
- `dayflow_daily_summary.py` — Dayflow 日摘要 → Anda
- `layer2_wiki.py` — 读取 memory_chunks + hubble_radius → wiki_entries

---

## 文件位置

- **主脚本**: `/Users/xz/Documents/myScope/scripts/daily_collect.sh`
- **补跑器**: `/Users/xz/Documents/myScope/scripts/run_due_jobs.py`
- **包装器**: `/Users/xz/Documents/myScope/scripts/run_job.py`
- **日志目录**: `/Users/xz/Documents/myScope/logs/`
- **状态文件**: `logs/job_status.json`、`data/job_status/<hostname>.json`
- **指标文件**: `logs/metrics.jsonl`、`data/metrics/<hostname>.jsonl`

---

## 手动运行

```bash
# 检查哪些 MacBook 任务到期，不实际运行
python3 /Users/xz/Documents/myScope/scripts/run_due_jobs.py --machine macbook --dry-run

# 立即补跑到期任务
/Users/xz/Documents/myScope/scripts/daily_collect.sh

# 强制执行 MacBook 全链路
python3 /Users/xz/Documents/myScope/scripts/run_due_jobs.py --machine macbook --force

# 查看最近日志
tail -f /Users/xz/Documents/myScope/logs/run_due_jobs.log
```

---

## 监控

```bash
# 查看统一 job 状态
cat /Users/xz/Documents/myScope/logs/job_status.json

# 查看 dashboard/health_check 使用的共享状态
ls /Users/xz/Documents/myScope/data/job_status/

# 兼容旧脚本的 last_run
cat /Users/xz/Documents/myScope/logs/last_run.json
```

---

## 推荐安装

```bash
bash /Users/xz/Documents/myScope/launchd/install.sh macbook
```

MacBook 现在默认只安装 `com.myscope.run-due-jobs`，登录时运行一次，并每小时检查一次是否需要补跑。
