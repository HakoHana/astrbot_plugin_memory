"""热消息缓存 — 内存 deque，每用户最近 N 条消息

架构变更（2026-06）：
- 热缓存是记忆提取的 source of truth（主数据源）
- conversations.db 只做冷备份 + 跨会话检索
- 每条消息先写入此缓存，达到阈值或定时才批量刷入 conversations.db
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from ..storage.conversation_store import ConversationStore
from ..utils.context_formatter import format_msg
from .interfaces import IHotMessageCache


class HotMessageCache(IHotMessageCache):
    """每个用户的热消息缓存

    在 main.py 入口处写入（user + assistant 双向），
    Retriever.get_recent_context 直接从此读取（零 SQL 开销）。

    格式与 ConversationStore 兼容，但完全是内存操作。
    """

    MAX_PER_USER = 50  # 单个用户最多缓存条数，超限触发 flush

    def __init__(self):
        self._caches: dict[str, deque[dict[str, Any]]] = {}

    # ── 写入 ──

    def push(
        self,
        user_id: str,
        role: str,
        content: str,
        sender_name: str = "",
        sender_id: str = "",
        session_id: str = "",
    ):
        """追加一条消息到用户的热缓存

        Args:
            user_id: 用户标识
            role: user / assistant
            content: 消息内容
            sender_name: 发送者显示名
            sender_id: 发送者 ID
            session_id: 会话 ID（刷写到 DB 时使用）
        """
        if not user_id:
            return
        if user_id not in self._caches:
            self._caches[user_id] = deque(maxlen=self.MAX_PER_USER)
        self._caches[user_id].append({
            "role": role,
            "content": content,
            "sender_name": sender_name,
            "sender_id": sender_id or user_id,
            "session_id": session_id,
            "timestamp": time.time(),
            "flushed": False,
        })

    # ── 读取 ──

    def get_recent(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """取最近 N 条原始消息（最新的在末尾）"""
        q = self._caches.get(user_id)
        if not q:
            return []
        messages = list(q)
        return messages[-limit:]

    def format_recent_context(
        self, user_id: str, limit: int = 20, bot_name: str = "我"
    ) -> str:
        """格式化为带时间戳的对话文本"""
        messages = self.get_recent(user_id, limit)
        if not messages:
            return ""
        now = time.time()
        lines = []
        for m in messages:
            ts = m.get("timestamp", now)
            content = m["content"]
            role = m["role"]
            name = m.get("sender_name", "")
            sid = m.get("sender_id", "")
            if role == "user":
                display = name if name else (sid or "用户")
            else:
                display = f"Bot: {bot_name}"
            lines.append(format_msg(ts, display, content, now))
        return "\n".join(lines)

    # ── 与持久层同步 ──

    async def flush_to_db(self, conversation_store) -> int:
        """将未刷写（flushed=False）的消息批量写入 conversations.db

        Returns:
            本次刷写的消息条数
        """
        if not isinstance(conversation_store, ConversationStore):
            return 0

        to_flush: list[dict] = []
        for user_id, msgs in self._caches.items():
            for m in msgs:
                if not m.get("flushed"):
                    to_flush.append({
                        "session_id": m.get("session_id", f"cache_{user_id}"),
                        "user_id": user_id,
                        "role": m["role"],
                        "content": m["content"],
                        "sender_name": m.get("sender_name", ""),
                        "timestamp": m.get("timestamp", time.time()),
                        "_msg_ref": m,  # 引用原对象，写成功后标记
                    })

        if not to_flush:
            return 0

        try:
            count = await conversation_store.batch_add_messages(to_flush)
            # 标记已刷写
            for item in to_flush:
                item["_msg_ref"]["flushed"] = True
            return count
        except Exception:
            return 0

    # ── 管理 ──

    def clear(self, user_id: str | None = None):
        """清空指定用户或全部缓存"""
        if user_id:
            self._caches.pop(user_id, None)
        else:
            self._caches.clear()

    def stats(self) -> dict[str, int]:
        """返回每用户的消息数（调试用）"""
        return {uid: len(q) for uid, q in self._caches.items()}
