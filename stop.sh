#!/bin/bash
PORT=8765
PID=$(lsof -ti:$PORT 2>/dev/null)
if [ -z "$PID" ]; then
  echo "memori 未在运行"
  exit 0
fi
echo "正在关闭 memori (PID $PID)..."
kill $PID
sleep 1
if lsof -ti:$PORT > /dev/null 2>&1; then
  echo "等待进程结束..."
  sleep 2
  kill -9 $PID 2>/dev/null
fi
echo "memori 已停止"
