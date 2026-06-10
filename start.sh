#!/bin/bash
PORT=8765

if curl -sf http://localhost:$PORT/health > /dev/null 2>&1; then
    echo "✅ memori 已在运行"
    xdg-open "http://localhost:$PORT/webui/dashboard/index.html"
    exit 0
fi

echo "🚀 启动 memori..."
echo "🌐 自动打开 Dashboard"
echo "🛑 按 Ctrl+C 停止服务"
echo ""

cd "$(dirname "$0")"

# 在后台打开浏览器（等几秒让服务先就绪）
(sleep 2 && xdg-open "http://localhost:$PORT/webui/dashboard/index.html") &

# 前台运行 — 终端保持打开，显示日志，Ctrl+C 停止
python -m memori --port $PORT
