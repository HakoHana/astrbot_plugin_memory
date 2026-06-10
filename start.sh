#!/bin/bash
PORT=8765

if curl -sf http://localhost:$PORT/health > /dev/null 2>&1; then
    echo "✅ memori 已在运行"
else
    echo "🚀 启动 memori..."
    cd "$(dirname "$0")"
    nohup python -m memori --port $PORT > /dev/null 2>&1 &
    disown
    for i in $(seq 1 15); do
        if curl -sf http://localhost:$PORT/health > /dev/null 2>&1; then
            echo "✅ memori 已就绪"
            break
        fi
        if [ $i -eq 15 ]; then echo "❌ 超时"; exit 1; fi
        sleep 1
    done
fi

xdg-open "http://localhost:$PORT/webui/dashboard/index.html"
