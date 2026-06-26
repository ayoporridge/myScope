# myScope

个人三层记忆系统 + 海马体。Mac mini（24/7）+ MacBook（日间使用）双机协作。

```
第一层  事实记忆   微信/Obsidian/flomo/Dayflow → Xiaomi mimo 切片 → Meilisearch
第二层  结构记忆   Layer 1 切片 + Layer 3 半径 → Xiaomi mimo 跨层综合 → Meilisearch
第三层  半径记忆   FreshRSS + 公众号文章 → 直接索引 → Meilisearch
海马体  对话记忆   Claude/Codex/Hermes/Clacky 会话 → 过滤 → Anda 知识图谱
```

---

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│  Mac mini（24/7 开机）                                   │
│                                                         │
│  定时任务（凌晨）：                                       │
│    02:30  layer3_index.py       FreshRSS → hubble_radius│
│    04:30  layer1_flomo.py       flomo → memory_chunks   │
│    05:30  layer2_wiki.py        跨层综合 → wiki_entries  │
│    06:00  hippocampus_formation  Codex/Hermes → Anda    │
│    06:30  health_check.py       健康检查 → 飞书告警       │
│                                                         │
│  Docker：FreshRSS / Meilisearch / Memory API / Anda     │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  MacBook（日间使用，每天开机）                             │
│                                                         │
│  catch-up runner（登录时 + 每小时检查）：                    │
│    run_due_jobs.py --machine macbook                     │
│      dayflow_sync.py            屏幕活动 → memory_chunks  │
│      layer3_wechat.py           公众号文章 → hubble_radius│
│      layer1_rag.py              微信+Obsidian → memory_chunks│
│      hippocampus_formation.py   Claude/Clacky → Anda     │
│      dayflow_daily_summary.py   日摘要 → Anda            │
│      layer2_wiki.py             跨层综合 → wiki_entries  │
└─────────────────────────────────────────────────────────┘
```

## 实际技术栈

| 组件 | 服务 | 地址 |
|------|------|------|
| LLM | 小米 MiMo | `token-plan-cn.xiaomimimo.com` |
| 向量存储 | Memory API → Meilisearch | `memory.arjo.us.ci` |
| 海马体 | Anda Hippocampus | `hippocampus.arjo.us.ci` |
| RSS 聚合 | FreshRSS（Mac mini Docker） | `192.168.1.175:8080` |
| 公众号 | opencli wx biz-articles | MacBook 本地微信缓存 |
| 屏幕活动 | Dayflow | MacBook 本地 SQLite |

## 数据源归属

| 数据源 | 所在机器 | 原因 |
|--------|---------|------|
| 微信收藏 / 文件传输助手 | **MacBook** | 需要微信本地数据库 |
| 公众号文章 | **MacBook** | opencli wx 读本地微信缓存 |
| Obsidian 笔记 | **MacBook** | vault 在 MacBook 桌面 |
| Dayflow 屏幕活动 | **MacBook** | Dayflow 只装在 MacBook |
| flomo | **Mac mini** | 浏览器自动化采集 |
| FreshRSS | **Mac mini** | Docker 容器在 Mac mini |
| Codex / Hermes 会话 | **Mac mini** | Agent 运行在 Mac mini |
| Claude Code / Clacky 会话 | **MacBook** | 开发在 MacBook |

---

## 前置条件

**Mac mini**：
- Docker Desktop（FreshRSS / Meilisearch / RSSHub）
- Python 3.10+（`brew install python3`）
- opencli（flomo 采集用）
- Tailscale（可选，远程访问）

**MacBook**：
- Python 3.10+
- opencli（微信数据采集用）
- Dayflow（屏幕活动记录）
- Obsidian

**共享**：
- 小米 MiMo API Key
- Anda 空间 Token
- 飞书 Webhook（告警用）

---

## .env 配置

```bash
cp .env.example .env
nano .env
```

| 变量 | 说明 |
|------|------|
| `XIAOMI_API_KEY` | 小米 MiMo API Key |
| `MEMORY_API_URL` | `https://memory.arjo.us.ci` |
| `MEMORY_API_TOKEN` | Memory API Token |
| `ANDA_BASE_URL` | `https://hippocampus.arjo.us.ci` |
| `ANDA_SPACE_ID` | Anda 空间 ID |
| `ANDA_SPACE_TOKEN` | Anda 空间 Token |
| `FRESHRSS_URL` | `http://192.168.1.175:8080` |
| `FRESHRSS_USERNAME` | FreshRSS 用户名 |
| `FRESHRSS_API_PASSWORD` | FreshRSS API 密码 |
| `FEISHU_WEBHOOK_URL` | 飞书告警 Webhook（可选） |

---

## 安装部署

### Mac mini

```bash
git clone https://github.com/ayoporridge/myScope.git
cd myScope
bash setup.sh                    # 安装依赖 + Docker
bash launchd/install.sh macmini  # 注册定时任务
```

### MacBook

```bash
git clone https://github.com/ayoporridge/myScope.git
cd myScope
pip3 install -r requirements.txt
bash launchd/install.sh macbook  # 注册开机任务
```

安装后两台机器共用同一个 `.env`（手动复制或通过 git 同步后填写）。

---

## 定时任务时间表

### Mac mini（定时）

| 时间 | 脚本 | 数据源 → 目标索引 |
|------|------|------------------|
| 02:30 | `layer3_index.py` | FreshRSS → `hubble_radius` |
| 04:30 | `layer1_flomo.py` | flomo 网页 → `memory_chunks` |
| 05:30 | `layer2_wiki.py` | 跨层综合 → `wiki_entries` |
| 06:00 | `hippocampus_formation.py` | Codex/Hermes/Clacky → Anda |
| 06:30 | `health_check.py` | 存活性+质量 → 飞书告警 |

### MacBook（catch-up runner）

| 触发 | 脚本 | 说明 |
|------|------|------|
| 登录时 + 每小时 | `run_due_jobs.py --machine macbook` | 根据 `last_success_at` 补跑到期任务 |
| 到期时 | `dayflow_sync.py` | Dayflow → `memory_chunks` |
| 到期时 | `layer3_wechat.py` | 公众号文章 → `hubble_radius` |
| 到期时 | `layer1_rag.py` | 微信+Obsidian → `memory_chunks` |
| 到期时 | `hippocampus_formation.py` | Claude/Clacky → Anda |
| 到期时 | `dayflow_daily_summary.py` | Dayflow 日摘要 → Anda |
| 到期时 | `layer2_wiki.py` | 跨层综合 → `wiki_entries` |

---

## 查询接口

### CLI

```bash
# 海马体召回（Anda 知识图谱）
python3 scripts/hippocampus_recall.py --query "关键词"

# Meilisearch 搜索（memory_chunks / wiki_entries / hubble_radius）
curl "https://memory.arjo.us.ci/search?q=关键词&index=memory_chunks&limit=10" \
  -H "Authorization: Bearer $MEMORY_API_TOKEN"
```

### MCP Server

```bash
python3 scripts/memory_mcp_server.py
```

暴露两个 tool：`search_memory`（Meilisearch 关键词搜索）和 `recall_memory`（Anda 海马体召回）。

### Dashboard

```bash
python3 scripts/dashboard.py
```

Web UI，显示脚本运行状态、质量趋势、数据量。

---

## 手动触发

```bash
cd ~/Documents/myScope

# 第一层（微信 + Obsidian，需在 MacBook 上）
python3 scripts/layer1_rag.py

# 第一层（flomo，需在 Mac mini 上）
python3 scripts/layer1_flomo.py

# 第二层（跨层综合）
python3 scripts/layer2_wiki.py

# 第三层（RSS）
python3 scripts/layer3_index.py

# 第三层（公众号，需在 MacBook 上）
python3 scripts/layer3_wechat.py

# 海马体
python3 scripts/hippocampus_formation.py

# 健康检查
python3 scripts/health_check.py

# 检查并补跑当前机器到期任务
python3 scripts/run_due_jobs.py --machine macbook --dry-run
python3 scripts/run_due_jobs.py --machine macbook
```

---

## 文件结构

```
myScope/
├── .env.example                    # 环境变量模板
├── .env                            # 本地配置（不提交）
├── docker-compose.yml              # FreshRSS + Meilisearch
├── requirements.txt                # Python 依赖
├── setup.sh                        # 一键安装脚本
├── scripts/
│   ├── layer1_rag.py               # 第一层：微信+Obsidian → 切片（MacBook）
│   ├── layer1_flomo.py             # 第一层：flomo → 切片（Mac mini）
│   ├── layer2_wiki.py              # 第二层：跨层综合 → Wiki（Mac mini）
│   ├── layer3_index.py             # 第三层：RSS → 索引（Mac mini）
│   ├── layer3_wechat.py            # 第三层：公众号 → 索引（MacBook）
│   ├── dayflow_sync.py             # Dayflow → memory_chunks（MacBook）
│   ├── dayflow_daily_summary.py    # Dayflow 日摘要 → Anda（MacBook）
│   ├── hippocampus_formation.py    # 海马体：会话 → Anda（双机）
│   ├── hippocampus_recall.py       # 海马体：召回查询
│   ├── health_check.py             # 健康检查 → 飞书告警
│   ├── dashboard.py                # Web 状态面板
│   ├── memory_mcp_server.py        # MCP Server
│   ├── run_job.py                  # 统一任务包装器（锁/超时/状态）
│   ├── run_due_jobs.py             # 到期任务补跑器
│   ├── memory_smoke_test.py        # 记忆入口冒烟测试
│   ├── source_audit.py             # 哈勃半径源边界审计
│   ├── subscribe_podcasts.py       # 播客批量订阅工具
│   └── _metrics.py                 # 运行指标记录（内部模块）
├── launchd/
│   ├── install.sh                  # 安装脚本（参数：macmini / macbook）
│   ├── macmini/                    # Mac mini 定时任务 plist
│   │   ├── com.myscope.layer3-index.plist
│   │   ├── com.myscope.layer1-flomo.plist
│   │   ├── com.myscope.layer2-wiki.plist
│   │   ├── com.myscope.hippocampus-formation.plist
│   │   └── com.myscope.health-check.plist
│   └── macbook/                    # MacBook 开机任务 plist
│       └── com.myscope.run-due-jobs.plist
├── cloudflare-worker/
│   └── worker.js                   # Meilisearch API 安全中转
└── logs/                           # 运行日志 + 指标
```

---

## 同步方式

两台机器通过 Git 同步代码和运行指标：

```bash
git pull   # 拉取对方的更新
git add -A && git commit -m "..." && git push
```

`.env` 不提交（在 `.gitignore` 中），需要在两台机器上分别配置。
