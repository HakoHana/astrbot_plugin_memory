"""会话存储 — 保存对话历史，为日记提供完整上下文"""

from __future__ import annotations

import json
import time

from .base_store import BaseDbStore


class ConversationStore(BaseDbStore):
    """存储用户和 Bot 的对话消息"""

    async def initialize(self):
        async with self._connect() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    platform TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    last_active_at REAL NOT NULL,
                    message_count INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sender_id TEXT DEFAULT '',
                    sender_name TEXT DEFAULT '',
                    timestamp REAL NOT NULL,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, id DESC)
            """)
            # 兼容旧表：补齐 sender_id, sender_name
            for col in ["sender_id", "sender_name"]:
                try:
                    await db.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT DEFAULT ''")
                except Exception:
                    pass
            await db.commit()

    async def add_message(self, session_id: str, user_id: str, role: str, content: str,
                           sender_name: str = ""):
        """添加一条消息"""
        now = time.time()
        async with self._connect() as db:
            await db.execute(
                "INSERT OR IGNORE INTO sessions(session_id, user_id, created_at, last_active_at) VALUES (?,?,?,?)",
                (session_id, user_id, now, now),
            )
            await db.execute(
                "UPDATE sessions SET last_active_at=?, message_count=message_count+1 WHERE session_id=?",
                (now, session_id),
            )
            await db.execute(
                "INSERT INTO messages(session_id, role, content, sender_id, sender_name, timestamp) VALUES (?,?,?,?,?,?)",
                (session_id, role, content, user_id, sender_name, now),
            )
            await db.commit()

    async def get_recent_context(self, session_id: str, limit: int = 20,
                                  bot_name: str = "我") -> str:
        """获取最近的对话上下文（用于写日记/判断）

        格式：用户消息显示 sender_name，bot 消息显示 bot_name
        """
        async with self._connect() as db:
            rows = await db.execute_fetchall(
                "SELECT role, content, sender_name, sender_id FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
        if not rows:
            return ""
        lines = []
        for r in reversed(rows):
            role = r[0]
            content = r[1]
            name = r[2] or ""  # sender_name
            sid = r[3] or ""   # sender_id
            if role == "user":
                display = name if name else (sid or "用户")
                lines.append(f"[{display}]: {content}")
            else:
                lines.append(f"[{bot_name}]: {content}")
        return "\n".join(lines)

    async def get_session_id(self, event) -> str:
        """从事件中提取会话 ID"""
        if hasattr(event, "unified_msg_origin"):
            sid = event.unified_msg_origin
            if sid:
                return str(sid)
        if hasattr(event, "get_session_id"):
            return event.get_session_id() or "default"
        return "default"

    async def get_user_id(self, event) -> str:
        """从事件中提取用户 ID（建议改用 AstrBotContextProvider.get_user_id）"""
        if hasattr(event, "get_sender_id"):
            sid = event.get_sender_id()
            if sid:
                return str(sid)
        return "default"
