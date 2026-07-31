#!/bin/bash
# Relay (kanban-system) 一键安装脚本
# 功能：创建 venv 并安装依赖、部署 skill 与 rules 到 ~/.qoder/、打印 MCP 注册配置
# 用法：./install.sh [--launchd | --rules-only]
#   --launchd     额外安装开机常驻的 Web UI 服务（仅 macOS）
#   --rules-only  仅部署三条 always-on rules 到 ~/.qoder/rules/（ACA 插件用户补装用，不创建 venv、不装依赖、不部署 skill）
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
QODER_DIR="$HOME/.qoder"
VENV_DIR="$REPO_DIR/.venv"

# ---------- 参数解析 ----------
RULES_ONLY=false
for arg in "$@"; do
  case "$arg" in
    --rules-only) RULES_ONLY=true ;;
    --launchd) ;; # launchd 在后面按 $1 单独判断
    *) echo "⚠️  未知参数：$arg" ;;
  esac
done

# ---------- 1. Python 环境 ----------
find_python() {
  for py in python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1; then
      ver=$("$py" -c 'import sys; print(sys.version_info >= (3,10))' 2>/dev/null)
      if [ "$ver" = "True" ]; then echo "$py"; return 0; fi
    fi
  done
  return 1
}

if [ "$RULES_ONLY" = true ]; then
  echo "ℹ️  --rules-only 模式：跳过 venv 与依赖安装"
else
  if [ -x "$VENV_DIR/bin/python" ]; then
    echo "⏭️  已存在 venv：$VENV_DIR，跳过创建"
  elif command -v uv >/dev/null 2>&1; then
    echo "📦 使用 uv 创建 venv（Python 3.12）..."
    uv venv --python 3.12 "$VENV_DIR"
  elif PY=$(find_python); then
    echo "📦 使用 $PY 创建 venv..."
    "$PY" -m venv "$VENV_DIR"
  else
    echo "❌ 未找到 Python >= 3.10。请安装 uv（brew install uv）或 Python 3.10+ 后重试"
    exit 1
  fi

  echo "📦 安装依赖..."
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV_DIR/bin/python" "mcp>=1.0.0" "uvicorn>=0.30" "starlette>=0.37" "python-frontmatter>=1.1.0"
  else
    "$VENV_DIR/bin/pip" install --quiet "mcp>=1.0.0" "uvicorn>=0.30" "starlette>=0.37" "python-frontmatter>=1.1.0"
  fi
  echo "✅ 依赖安装完成"
fi

# ---------- 2. 部署 skill ----------
if [ "$RULES_ONLY" = true ]; then
  echo "ℹ️  --rules-only 模式：跳过 skill 部署（ACA 插件已提供）"
else
  mkdir -p "$QODER_DIR/skills/kanban"
  cp "$REPO_DIR/skills/kanban/SKILL.md" "$QODER_DIR/skills/kanban/SKILL.md"
  echo "✅ Skill 已部署到 $QODER_DIR/skills/kanban/"
fi

# ---------- 3. 部署 rules（已存在则跳过，避免覆盖本地修改） ----------
mkdir -p "$QODER_DIR/rules"
for rule in kanban-writeback.md kanban-capture.md kanban-boot.md; do
  if [ -f "$QODER_DIR/rules/$rule" ]; then
    echo "⏭️  rules/$rule 已存在，跳过"
  else
    cp "$REPO_DIR/rules/$rule" "$QODER_DIR/rules/$rule"
    echo "✅ 规则 $rule 已部署到 $QODER_DIR/rules/"
  fi
done

# ---------- 4. 初始化数据目录 ----------
mkdir -p "$HOME/.kanban/tasks" "$HOME/.kanban/archive"
[ -f "$HOME/.kanban/counter.txt" ] || echo "0" > "$HOME/.kanban/counter.txt"
echo "✅ 数据目录 ~/.kanban/ 就绪"

# ---------- 5. 可选：launchd 常驻 Web UI（macOS） ----------
if [ "$RULES_ONLY" = true ] && [ "$1" = "--launchd" ]; then
  echo "⚠️  --rules-only 与 --launchd 不可同时使用，跳过 launchd"
fi
if [ "$1" = "--launchd" ] && [ "$RULES_ONLY" = false ]; then
  if [ "$(uname)" != "Darwin" ]; then
    echo "⚠️  --launchd 仅支持 macOS，跳过"
  else
    PLIST="$HOME/Library/LaunchAgents/com.relay.kanban.plist"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.relay.kanban</string>
  <key>ProgramArguments</key><array>
    <string>$VENV_DIR/bin/python</string>
    <string>-m</string><string>server.main</string><string>--web-only</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO_DIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "✅ Web UI 已注册为常驻服务（http://localhost:7654）"
  fi
fi

# ---------- 6. 打印 MCP 注册配置 ----------
if [ "$RULES_ONLY" = true ]; then
  echo ""
  echo "✅ --rules-only 部署完成：三条 always-on rules 已就位于 $QODER_DIR/rules/"
  echo "   Skill 与 MCP 服务未变动（由已装的 ACA 插件或您之前的完整安装提供）"
  exit 0
fi

echo ""
echo "🎉 安装完成！最后一步：手动注册 MCP 服务（Qoder 设置 → MCP → 添加），配置如下："
echo ""
cat <<EOF
{
  "mcpServers": {
    "kanban": {
      "command": "$VENV_DIR/bin/python",
      "args": ["-m", "server.main"],
      "cwd": "$REPO_DIR"
    }
  }
}
EOF
echo ""
echo "说明："
echo "  - MCP 服务启动时会同进程拉起 Web UI（http://localhost:7654）"
echo "  - 仅想要 Web UI 时可运行：$VENV_DIR/bin/python -m server.main --web-only"
echo "  - 钉钉决策升级为可选功能，配置方法见 README"
