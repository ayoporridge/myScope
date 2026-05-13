#!/bin/bash
# setup.sh
# 在 Mac mini 上 git clone 后，运行此脚本完成所有初始化
# 用法: bash setup.sh

set -e
HERMES_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "=============================="
echo " Hermes Setup"
echo " 目录: $HERMES_DIR"
echo "=============================="

# ── 1. 检查依赖 ──────────────────────────────────────────
echo ""
echo "[1/6] 检查依赖..."

if ! command -v python3 &>/dev/null; then
  echo "  ❌ 未找到 python3，请先安装：https://brew.sh 然后 brew install python3"
  exit 1
fi
echo "  ✓ python3: $(python3 --version)"

if ! command -v docker &>/dev/null; then
  echo "  ❌ 未找到 docker，请先安装 Docker Desktop: https://www.docker.com/products/docker-desktop/"
  exit 1
fi
echo "  ✓ docker: $(docker --version | head -1)"

# ── 2. 创建 .env ─────────────────────────────────────────
echo ""
echo "[2/6] 配置环境变量..."
if [ ! -f "$HERMES_DIR/.env" ]; then
  cp "$HERMES_DIR/.env.example" "$HERMES_DIR/.env"
  echo "  ✓ 已创建 .env（请编辑填入真实值）"
  echo ""
  echo "  ⚠️  请先编辑 $HERMES_DIR/.env 填入所有 API Key 后，再继续运行："
  echo "      nano $HERMES_DIR/.env"
  echo ""
  read -p "  填好后按回车继续..." _
else
  echo "  ✓ .env 已存在"
fi

# ── 3. 安装 Python 依赖 ──────────────────────────────────
echo ""
echo "[3/6] 安装 Python 依赖..."
pip3 install -r "$HERMES_DIR/requirements.txt" -q
echo "  ✓ Python 依赖安装完成"

# ── 4. 创建日志目录 ──────────────────────────────────────
echo ""
echo "[4/6] 创建日志目录..."
mkdir -p "$HERMES_DIR/logs"
echo "  ✓ logs/ 目录已创建"

# ── 5. 启动 Docker 服务 ──────────────────────────────────
echo ""
echo "[5/6] 启动 Docker 服务（FreshRSS / Meilisearch / RSSHub）..."
cd "$HERMES_DIR"
docker compose up -d
echo "  ✓ Docker 服务已启动"
echo ""
echo "  服务地址："
echo "    FreshRSS    → http://localhost:8080"
echo "    Meilisearch → http://localhost:7700"
echo "    RSSHub      → http://localhost:1200"

# ── 6. 安装 launchd 定时任务 ─────────────────────────────
echo ""
echo "[6/6] 安装 launchd 定时任务..."
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"
mkdir -p "$LAUNCH_AGENTS_DIR"

for plist in "$HERMES_DIR/launchd/"*.plist; do
  name=$(basename "$plist")
  dest="$LAUNCH_AGENTS_DIR/$name"
  # 替换占位符
  sed "s|HERMES_DIR|$HERMES_DIR|g" "$plist" > "$dest"
  # 卸载旧版（忽略错误）
  launchctl unload "$dest" 2>/dev/null || true
  # 加载新版
  launchctl load "$dest"
  echo "  ✓ 已加载: $name"
done

echo ""
echo "=============================="
echo " 安装完成 🎉"
echo "=============================="
echo ""
echo "接下来需要手动完成的步骤："
echo ""
echo "1. 初始化 FreshRSS："
echo "   浏览器打开 http://localhost:8080 → 按向导完成设置"
echo "   设置完后，在「个人资料」→「API 管理」里启用 API，记录 API 密码填入 .env"
echo ""
echo "2. 导入你的订阅源："
echo "   FreshRSS 设置 → 订阅管理 → 导入 OPML，或手动添加 RSS 地址"
echo "   RSSHub 路由示例见 README"
echo ""
echo "3. 部署 Cloudflare Worker："
echo "   见 README → Cloudflare Worker 章节"
echo ""
echo "4. 手动测试同步脚本："
echo "   python3 scripts/layer3_index.py"
echo "   python3 scripts/layer1_rag.py"
echo "   python3 scripts/layer2_wiki.py"
echo ""
echo "定时任务每日执行时间："
echo "  04:30  layer3_index            （FreshRSS → Meilisearch）"
echo "  05:00  layer1_rag              （Notion   → RAG 切片）"
echo "  05:30  layer2_wiki             （RAG      → LLM Wiki）"
echo "  06:00  hippocampus-formation   （Claude 日志 → Notion 海马体）"
