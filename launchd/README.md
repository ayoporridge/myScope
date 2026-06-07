# launchd 定时任务

## 两套任务

### macmini/ — 定时任务

每天凌晨自动运行，适合 Mac mini（24/7 开机）。

| 时间 | 脚本 | 说明 |
|------|------|------|
| 02:30 | layer3_index | FreshRSS → hubble_radius |
| 04:30 | layer1_flomo | flomo → memory_chunks |
| 05:30 | layer2_wiki | 跨层综合 → wiki_entries |
| 06:00 | hippocampus_formation | Codex/Hermes/Clacky → Anda |
| 06:30 | health_check | 健康检查 → 飞书告警 |

### macbook/ — 开机任务

登录时自动运行一次，脚本内部通过状态文件做增量，不会重复处理。

| 脚本 | 说明 |
|------|------|
| layer1_rag | 微信收藏 + 文件传输助手 + Obsidian → memory_chunks |
| layer3_wechat | 公众号文章 → hubble_radius |
| dayflow_sync | Dayflow 屏幕活动 → memory_chunks |
| dayflow_summary | Dayflow 日摘要 → Anda |
| hippocampus_formation | Claude/Clacky 会话 → Anda |

## 安装

```bash
# Mac mini 上
bash launchd/install.sh macmini

# MacBook 上
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

# 查看日志
tail -f ~/Documents/myScope/logs/layer1_rag.log
```
