#!/bin/bash
PORT=8765

if curl -sf http://localhost:$PORT/health > /dev/null 2>&1; then
    echo "memori 已在运行"
    xdg-open "http://localhost:$PORT/webui/dashboard/index.html"
    exit 0
fi

cd "$(dirname "$0")"
echo "启动 memori..."
(sleep 2 && xdg-open "http://localhost:$PORT/webui/dashboard/index.html") &
python -m memori --port $PORT
