#!/bin/bash
# memori 配置面板 — 一键启动 + 打开浏览器

PORT=8765

# 检查 memori 是否已在运行
if curl -sf http://localhost:$PORT/health > /dev/null 2>&1; then
    echo "✅ memori 已在运行"
else
    echo "🚀 启动 memori..."
    cd "$(dirname "$0")"
    python -m memori --port $PORT &
    sleep 3
fi

# 打开配置页面
xdg-open "http://localhost:$PORT/config"
echo "🌐 配置页面已打开"
