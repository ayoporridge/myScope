# Hermes Setup

个人 AI 三层记忆系统的完整部署方案。复刻自「哈勃半径」体系。

```
第一层  事实记忆   Notion 日记/笔记 → DeepSeek 切片 → Notion RAG DB
第二层  结构记忆   RAG 切片 → DeepSeek 归纳 → Notion LLM Wiki
第三层  半径记忆   全量订阅源 → FreshRSS → Meilisearch（哈勃半径）
海马体  对话记忆   Claude 日志 → DeepSeek 提取 → Notion 海马体 DB
```

---

## 前置条件

- Mac mini 已连接网络，24/7 开机
- 已安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- 已安装 Python 3.10+（`brew install python3`）
- 已有 Notion 账号 + Notion AI 订阅
- 已有 DeepSeek API Key
- 已有 Cloudflare 账号（免费）

---

## 一、Mac mini 部署

### 1. 克隆仓库

```bash
git clone https://github.com/ayoporridge/myScope.git
cd myScope
```

### 2. 一键安装

```bash
bash setup.sh
```

脚本会自动：
- 检查依赖
- 创建 `.env` 文件（第一次会暂停让你填写）
- 安装 Python 依赖
- 启动 Docker 服务（FreshRSS / Meilisearch / RSSHub）
- 注册 launchd 定时任务

---

## 二、.env 配置说明

```bash
cp .env.example .env
nano .env   # 或用任意编辑器
```

| 变量 | 获取方式 |
|------|---------|
| `MEILI_MASTER_KEY` | 自己随机生成，例如 `openssl rand -hex 32` |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com → API Keys |
| `NOTION_API_KEY` | https://www.notion.so/my-integrations → 新建 Integration → 复制 Token |
| `NOTION_RAG_DB_ID` | 见下方「Notion 数据库配置」 |
| `NOTION_WIKI_DB_ID` | 见下方「Notion 数据库配置」 |
| `NOTION_HIPPO_DB_ID` | 见下方「Notion 数据库配置 → 海马体数据库」 |
| `FRESHRSS_API_PASSWORD` | FreshRSS 设置 → 个人资料 → API 管理 → 设置 API 密码 |

---

## 三、Notion 数据库配置

### RAG 数据库（第一层）

新建一个 Notion 数据库，添加以下属性：

| 属性名 | 类型 |
|--------|------|
| Name | 标题 |
| chunk_id | 文本 |
| content | 文本 |
| keywords | 多选 |
| source | 文本 |
| created_at | 日期 |

### Wiki 数据库（第二层）

新建一个 Notion 数据库，添加以下属性：

| 属性名 | 类型 |
|--------|------|
| Name | 标题 |
| tags | 多选 |
| last_updated | 日期 |

### 海马体数据库（对话记忆）

新建一个 Notion 数据库，添加以下属性：

| 属性名 | 类型 |
|--------|------|
| Name | 标题 |
| memory_id | 文本 |
| memory_type | 单选（preference / decision / project / failure / personal） |
| content | 文本 |
| keywords | 多选 |
| confidence | 数字 |
| created_at | 日期 |

将 Database ID 填入 `.env` 的 `NOTION_HIPPO_DB_ID`。

### 获取数据库 ID

打开数据库页面，URL 格式如下：
```
https://www.notion.so/你的工作空间/xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?v=...
```
`?v=` 前面那串 32 位字符就是 Database ID。

**重要**：还需要把你的 Integration 授权给这两个数据库（数据库页面右上角 `...` → 连接到 → 选择你的 Integration）。

---

## 四、FreshRSS 初始化

1. 浏览器访问 `http://localhost:8080`
2. 按安装向导完成设置（设置管理员账号）
3. 进入「设置」→「个人资料」→「API 管理」→ 启用 API，设置一个 API 密码
4. 把这个密码填入 `.env` 的 `FRESHRSS_API_PASSWORD`

### 添加订阅源

#### 普通 RSS（直接填 URL）
- 博客、播客一般有原生 RSS，直接填地址

#### 公众号订阅
通过 RSSHub 转换，格式：`http://localhost:1200/wechat/mp/...`

常用 RSSHub 路由：
```
# 微信公众号（需要配置 cookie，见 RSSHub 文档）
http://localhost:1200/wechat/mp/profile_ext?id=公众号ID

# 即刻
http://localhost:1200/jike/user/用户ID

# X (Twitter)
http://localhost:1200/twitter/user/用户名

# 小红书（需要 cookie）
http://localhost:1200/xiaohongshu/user/用户ID
```

完整路由列表：https://docs.rsshub.app

---

## 五、Cloudflare Worker 部署

### 1. 安装 Wrangler

```bash
npm install -g wrangler
wrangler login
```

### 2. 创建 Worker

```bash
cd cloudflare-worker
wrangler init hermes-search --no-bundle
# 把 worker.js 内容复制到生成的 index.js
```

或直接在 Cloudflare Dashboard 新建 Worker，粘贴 `cloudflare-worker/worker.js` 内容。

### 3. 配置环境变量

在 Cloudflare Dashboard → Workers → hermes-search → Settings → Variables：

| 变量名 | 值 |
|--------|---|
| `WORKER_SECRET` | 与 `.env` 里 `CLOUDFLARE_WORKER_SECRET` 相同 |
| `MEILI_HOST` | `http://你的Mac公网IP或DDNS:7700` |
| `MEILI_MASTER_KEY` | 与 `.env` 里 `MEILI_MASTER_KEY` 相同 |

### 4. 开放 Mac mini 的 7700 端口

需要在家里的路由器上做**端口映射**：
- 外网端口 7700 → Mac mini 内网 IP:7700

Mac mini 的内网 IP 建议在路由器里设为静态（DHCP 绑定）。

### 5. 处理动态公网 IP（推荐）

国内家宽 IP 经常变，建议用 DDNS：
- **推荐服务**：阿里云 DNS（免费）、Cloudflare DDNS、No-IP
- Mac mini 上安装 DDNS 客户端，定时更新域名解析
- 然后 `MEILI_HOST` 填你的域名而不是 IP

---

## 六、外网访问 Mac mini（SSH）

### 方案 A：家宽端口映射（简单）

路由器端口映射：外网 22 → Mac mini 内网 IP:22

Mac mini 开启 SSH：系统设置 → 通用 → 共享 → 远程登录

```bash
ssh 你的用户名@你的公网IP
```

### 方案 B：Tailscale（推荐，更安全）

```bash
# Mac mini 上
brew install tailscale
tailscale up

# 任意设备上
tailscale ssh mac-mini
```

Tailscale 免费版支持 100 台设备，不需要暴露任何端口。

---

## 七、日常使用

### 手动触发同步

```bash
cd ~/myScope

# 第三层：FreshRSS → Meilisearch
python3 scripts/layer3_index.py

# 第一层：Notion → RAG 切片
python3 scripts/layer1_rag.py

# 第二层：RAG → LLM Wiki
python3 scripts/layer2_wiki.py

# 海马体：Claude 日志 → Notion 海马体
python3 scripts/hippocampus_formation.py
```

### 查看定时任务日志

```bash
tail -f ~/myScope/logs/layer3.log
tail -f ~/myScope/logs/layer1.log
tail -f ~/myScope/logs/layer2.log
tail -f ~/myScope/logs/hippocampus_formation.log
```

### 更新代码

```bash
cd ~/myScope
git pull
# 如果有 Python 依赖变化
pip3 install -r requirements.txt -q
# 如果有 launchd 变化，重新运行
bash setup.sh
```

### 在 Meilisearch UI 里查看索引

浏览器访问 `http://localhost:7700`（需要输入 Master Key）

---

## 八、定时任务时间表

| 时间 | 任务 | 脚本 |
|------|------|------|
| 04:30 | FreshRSS → Meilisearch | layer3_index.py |
| 05:00 | Notion → RAG 切片 | layer1_rag.py |
| 05:30 | RAG → LLM Wiki | layer2_wiki.py |
| 06:00 | Claude 日志 → Notion 海马体 | hippocampus_formation.py |

---

## 九、海马体记忆召回

`hippocampus_recall.py` 查询 Notion 海马体数据库，生成可注入对话的上下文摘要。

### 基本用法

```bash
# 召回所有记忆（默认最近 20 条）
python3 scripts/hippocampus_recall.py

# 按关键词过滤
python3 scripts/hippocampus_recall.py --query "Python"

# 按记忆类型过滤
python3 scripts/hippocampus_recall.py --type preference
python3 scripts/hippocampus_recall.py --type decision
python3 scripts/hippocampus_recall.py --type project
python3 scripts/hippocampus_recall.py --type failure
python3 scripts/hippocampus_recall.py --type personal

# 限制返回条数
python3 scripts/hippocampus_recall.py --limit 10
```

### 在 Claude 对话中使用

召回结果可直接粘贴到对话开头作为背景上下文，格式如下：

```
[记忆召回]
- (preference) 偏好使用 Python + Notion 构建个人工具 [confidence: 0.9]
- (decision)   2024-03 决定将所有笔记迁移到 Notion [confidence: 0.85]
...
```

---

## 十、接入 Claude Code（可选）

部署完 Cloudflare Worker 后，可以把哈勃半径接入 Claude Code 作为搜索工具。

在 Claude Code 对话中直接提问时，告知：
- Worker URL: `https://你的worker.workers.dev/search`
- Bearer Token: 你的 `CLOUDFLARE_WORKER_SECRET`

示例请求：
```bash
curl "https://你的worker.workers.dev/search?q=AI记忆层&limit=5" \
  -H "Authorization: Bearer 你的secret"
```

---

## 文件结构

```
myScope/
├── .env.example          # 环境变量模板
├── .env                  # 本地配置（不提交 git）
├── docker-compose.yml    # FreshRSS + Meilisearch + RSSHub
├── requirements.txt      # Python 依赖
├── setup.sh              # 一键安装脚本
├── scripts/
│   ├── layer1_rag.py              # 第一层：Notion → RAG 切片
│   ├── layer2_wiki.py             # 第二层：RAG → LLM Wiki
│   ├── layer3_index.py            # 第三层：FreshRSS → Meilisearch
│   ├── hippocampus_formation.py   # 海马体：Claude 日志 → Notion 海马体
│   └── hippocampus_recall.py      # 海马体：查询 → 对话上下文摘要
├── launchd/
│   ├── com.hermes.layer1-rag.plist
│   ├── com.hermes.layer2-wiki.plist
│   ├── com.hermes.layer3-index.plist
│   └── com.hermes.hippocampus-formation.plist
├── cloudflare-worker/
│   └── worker.js         # Meilisearch API 安全中转
└── logs/                 # 运行日志（自动创建）
```
