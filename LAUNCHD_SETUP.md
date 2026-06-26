# launchd 定时任务安装说明

Formation 任务每天 06:00 在 **MacBook** 上自动运行，读取 Claude 对话日志并写入 Anda Hippocampus。

---

## 前提

- Python 环境已安装 `requests`、`python-dotenv`
- `~/Documents/myScope/.env` 中已配置 `ANDA_BASE_URL`、`ANDA_SPACE_ID`、`ANDA_SPACE_TOKEN`

---

## 安装步骤

**第一步：创建日志目录**

```bash
mkdir -p ~/Documents/myScope/logs
```

**第二步：生成 plist 文件**

```bash
MYSCOPE_DIR="$HOME/Documents/myScope"

sed "s|HERMES_DIR|$MYSCOPE_DIR|g" \
  "$MYSCOPE_DIR/launchd/com.hermes.hippocampus-formation.plist" \
  > ~/Library/LaunchAgents/com.myscope.hippocampus-formation.plist
```

**第三步：加载任务**

```bash
launchctl load ~/Library/LaunchAgents/com.myscope.hippocampus-formation.plist
```

**第四步：验证已加载**

```bash
launchctl list | grep myscope
```

有输出即为成功。

---

## 手动触发（测试用）

```bash
launchctl start com.myscope.hippocampus-formation
```

查看输出：

```bash
tail -f ~/Documents/myScope/logs/hippocampus_formation.log
tail -f ~/Documents/myScope/logs/hippocampus_formation_err.log
```

---

## 卸载

```bash
launchctl unload ~/Library/LaunchAgents/com.myscope.hippocampus-formation.plist
rm ~/Library/LaunchAgents/com.myscope.hippocampus-formation.plist
```

---

## 注意事项

- MacBook 需在 06:00 开机且未睡眠，任务才会触发
- 如果错过触发时间，launchd **不会**补跑，需手动执行
- 日志会持续追加，建议定期清理（超过 30 天的可删除）
