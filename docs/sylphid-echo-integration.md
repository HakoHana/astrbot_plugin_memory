# Memori × Sylphid-Echo 集成指南

将 memori 记忆服务接入 Sylphid-Echo 统一消息管道。

---

## 架构

```
平台消息 → Adapter → UnifiedMessage → Dispatcher
                                            │
                              ┌─────────────┴─────────────┐
                              │                           │
                          LLM 处理                  memori 记忆服务
                          (回复)                POST /api/v1/events
                                                │
                                          ┌─────┴─────┐
                                          │   召回     │  存储
                                          │  记忆注入   │  整理
                                          └───────────┘
```

memori 以独立 HTTP 服务运行，Sylphid-Echo 通过 `MemoriClient` 与之通信。

---

## 集成代码

### `src/services/memory.py`

```python
"""Memori 记忆服务客户端 — HTTP API 封装"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MemoryAtom:
    """记忆原子"""
    content: str
    type: str = "unknown"       # episodic / factual / preference / planned
    importance: float = 0.5
    date: str | None = None


@dataclass
class EventResult:
    """事件处理结果"""
    modified_text: str | None = None
    injected_count: int = 0
    recalled_count: int = 0


class MemoriClient:
    """memori HTTP API 客户端

    用法:
        memori = MemoriClient(base_url="http://localhost:8765")
        result = await memori.process_message(
            user_id="qq:123456",
            text="今天测试辛苦了",
            sender_name="Hako",
        )
        memories = await memori.search("测试", uid="qq:123456")
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8765",
        timeout: float = 30.0,
        api_key: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"

    # ── 健康检查 ──

    async def health(self) -> dict[str, Any] | None:
        """检查 memori 服务状态"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/health")
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"[Memori] 健康检查失败: {e}")
            return None

    # ── 核心：事件处理 ──

    async def process_message(
        self,
        user_id: str,
        text: str,
        sender_name: str = "",
        session_id: str = "",
        system_prompt: str = "",
    ) -> EventResult | None:
        """提交消息 → 召回记忆 + 注入上下文

        这是最核心的接口。Sylphid-Echo 的 Dispatcher 在每次
        UnifiedMessage 到来时调用此方法。
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/api/v1/events",
                    headers=self.headers,
                    json={
                        "user_id": user_id,
                        "text": text,
                        "sender_name": sender_name,
                        "session_id": session_id,
                        "system_prompt": system_prompt,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("ok"):
                    return EventResult(
                        modified_text=data.get("modified_text"),
                        injected_count=data.get("injected_count", 0),
                        recalled_count=data.get("recalled_count", 0),
                    )
                logger.warning(f"[Memori] 事件处理返回异常: {data}")
                return None
        except httpx.RequestError as e:
            logger.error(f"[Memori] 连接失败 (memori 服务未运行?): {e}")
            return None
        except Exception as e:
            logger.error(f"[Memori] 事件处理异常: {e}")
            return None

    # ── 记忆检索 ──

    async def search(
        self,
        query: str,
        uid: str = "",
        k: int = 5,
    ) -> list[MemoryAtom]:
        """搜索相关记忆"""
        try:
            params = {"q": query, "k": k}
            if uid:
                params["uid"] = uid
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/memories",
                    headers=self.headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
                return [
                    MemoryAtom(
                        content=r["content"],
                        type=r.get("type", "unknown"),
                        importance=r.get("importance", 0.5),
                        date=r.get("date"),
                    )
                    for r in data.get("results", [])
                ]
        except Exception as e:
            logger.warning(f"[Memori] 检索失败: {e}")
            return []

    # ── 系统统计 ──

    async def stats(self) -> dict[str, int] | None:
        """获取记忆系统统计"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/stats",
                    headers=self.headers,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception:
            return None
```

### `src/services/__init__.py`

```python
from .memory import MemoriClient, MemoryAtom, EventResult

__all__ = ["MemoriClient", "MemoryAtom", "EventResult"]
```

---

## 在 Dispatcher 中集成

### `src/dispatcher.py`（关键改动点）

```python
# 在消息分发的关键节点调用 memori

from src.services import MemoriClient

class Dispatcher:
    def __init__(self):
        self.memori = MemoriClient(
            base_url="http://localhost:8765",
            timeout=10,
        )

    async def dispatch(self, msg: UnifiedMessage):
        # 1. （可选）先等待 memori 就绪
        if not await self.memori.health():
            logger.warning("memori 服务未就绪，跳过记忆处理")

        # 2. 调用 memori 处理消息（存储 + 召回）
        #    只有文本类消息需要记忆
        if msg.message_type in ("text", "emote") and msg.content:
            result = await self.memori.process_message(
                user_id=f"{msg.platform}:{msg.user_id}",
                text=msg.content,
                sender_name=msg.user_name,
                session_id=msg.room_id or msg.group_id or msg.user_id,
            )

            if result and result.modified_text:
                # modified_text 包含了注入的记忆内容
                # 用修改后的文本替代原始消息去 LLM
                msg.content = result.modified_text

        # 3. 继续原有分发逻辑
        await self._route_to_llm(msg)
```

---

## 启动步骤

### 1. 启动 memori 服务

```bash
# 终端 1：启动 memori（独立进程）
cd /path/to/memori
python -m memori --port 8765
```

或通过 systemd 托管：

```ini
[Unit]
Description=memori 长期记忆服务
After=network.target

[Service]
Type=simple
User=hako
WorkingDirectory=/path/to/memori
ExecStart=/usr/bin/python3 -m memori --port 8765
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 2. 确认服务正常

```bash
curl http://localhost:8765/health
# → {"status": "ok", "version": "0.1.0"}

curl http://localhost:8765/api/v1/stats
# → {"ok": true, "diaries": 0, "atoms": 0, ...}
```

### 3. 安装依赖

```bash
pip install httpx  # MemoriClient 需要
```

### 4. 配置环境变量

```bash
# .env 或系统环境
MEMORI_BASE_URL=http://localhost:8765
MEMORI_API_KEY=       # 如果启用了鉴权
MEMORI_TIMEOUT=10
```

---

## UnifiedMessage → memori 字段映射

| UnifiedMessage 字段 | memori 参数 | 说明 |
|---|---|---|
| `platform + user_id` | `user_id` | 如 `qq:123456`，跨平台唯一 |
| `user_name` | `sender_name` | LLM 看到的昵称 |
| `content` | `text` | 消息正文 |
| `room_id / group_id` | `session_id` | 会话上下文 |
| — | `system_prompt` | 由 Dispatcher 传入 |

---

## 错误处理策略

| 场景 | 行为 |
|------|------|
| memori 未启动 | `process_message` 返回 `None`，Sylphid-Echo 正常处理消息但无记忆 |
| memori 超时 | 同上，不影响主流程 |
| 网络闪断 | httpx 自动重试（需配置），最终超时返回 None |
| memori 返回错误 | 日志记录，不中断 Sylphid-Echo |
