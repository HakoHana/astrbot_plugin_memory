"""热消息缓存 — 内存 deque + WAL（Write-Ahead Log），每用户最近 N 条消息

定位：
- 纯环形消息缓冲区，所有消息都进，满则丢弃旧消息
- 不触发任何整理操作，触点完全由外部控制
- conversations.db 做冷备份 + 跨会话检索

持久化保障（类似 Redis AOF）：
- 每条消息同时写入 {wal_dir}/{user_id}.wal（JSON Lines）
- 启动时从 WAL 恢复热缓存（进程崩溃不丢数据）
- 刷入 DB 成功后清理已刷写的 WAL 条目
- 关闭前 destroy() 做最终刷写（零丢失）
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

from ..storage.conversation_store import ConversationStore
from ..utils.context_formatter import format_msg
from .interfaces import IHotMessageCache
from .logger import logger


class HotMessageCache(IHotMessageCache):
    """每个用户的热消息缓存（纯环形缓冲区，无触发逻辑）

    Retriever.get_recent_context 直接从此读取（零 SQL 开销）。
    WAL 文件做崩溃恢复，flushed 标记防止重复刷写。
    """

    def __init__(self, data_dir: str = "", config: dict | None = None):
        config = config or {}
        self._max_per_user: int = config.get("hotcache_max_per_user", 50)

        self._caches: dict[str, deque[dict[str, Any]]] = {}
        self._wal_dir = Path(data_dir) / "hotcache" if data_dir else None
        if self._wal_dir:
            self._wal_dir.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════
    #  启动恢复
    # ═══════════════════════════════════════════════════

    def restore_from_wal(self):
        """启动时从 WAL 文件恢复热缓存（类似 Redis AOF replay）"""
        if not self._wal_dir or not self._wal_dir.exists():
            return 0
        count = 0
        for wal_path in sorted(self._wal_dir.iterdir()):
            if wal_path.suffix != ".wal":
                continue
            user_id = wal_path.stem
            try:
                for line in wal_path.read_text(encoding="utf-8").strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    if user_id not in self._caches:
                        self._caches[user_id] = deque(maxlen=self._max_per_user)
                    self._caches[user_id].append(msg)
                    count += 1
            except Exception as exc:
                logger.warning(f"[HotCache] WAL 恢复失败 {wal_path.name}: {exc}")
        if count:
            logger.info(f"[HotCache] WAL 恢复: {count} 条消息")
        return count

    # ═══════════════════════════════════════════════════
    #  写入
    # ═══════════════════════════════════════════════════

    def push(
        self,
        user_id: str,
        role: str,
        content: str,
        sender_name: str = "",
        sender_id: str = "",
        session_id: str = "",
    ):
        """追加一条消息到用户的热缓存，同时写入 WAL"""
        if not user_id:
            return
        if user_id not in self._caches:
            self._caches[user_id] = deque(maxlen=self._max_per_user)

        msg = {
            "role": role,
            "content": content,
            "sender_name": sender_name,
            "sender_id": sender_id or user_id,
            "session_id": session_id,
            "timestamp": time.time(),
            "flushed": False,
        }
        self._caches[user_id].append(msg)

        # WAL：追加写入（类 Redis AOF，每条消息持久化）
        self._append_wal(user_id, msg)

    def _append_wal(self, user_id: str, msg: dict):
        """追加一条记录到 WAL 文件"""
        if not self._wal_dir:
            return
        try:
            wal_path = self._wal_dir / f"{user_id}.wal"
            with open(wal_path, "a", encoding="utf-8") as f:
                slim = {k: v for k, v in msg.items() if k != "flushed"}
                f.write(json.dumps(slim, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(f"[HotCache] WAL 写入失败 {user_id}: {exc}")

    # ═══════════════════════════════════════════════════
    #  读取
    # ═══════════════════════════════════════════════════

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

    # ═══════════════════════════════════════════════════
    #  与持久层同步
    # ═══════════════════════════════════════════════════

    async def flush_to_db(self, conversation_store) -> int:
        """将未刷写（flushed=False）的消息批量写入 conversations.db

        成功后裁剪 WAL（移除已刷写条目）。

        Returns:
            本次刷写的消息条数
        """
        if not isinstance(conversation_store, ConversationStore):
            return 0

        to_flush: list[dict] = []
        users_with_unflushed: set[str] = set()
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
                        "_msg_ref": m,
                    })
                    users_with_unflushed.add(user_id)

        if not to_flush:
            return 0

        try:
            count = await conversation_store.batch_add_messages(to_flush)
            for item in to_flush:
                item["_msg_ref"]["flushed"] = True
            self._compact_wal(users_with_unflushed)
            return count
        except Exception:
            return 0

    def _compact_wal(self, user_ids: set[str]):
        """裁剪 WAL：只保留未刷写的消息"""
        if not self._wal_dir:
            return
        for uid in user_ids:
            wal_path = self._wal_dir / f"{uid}.wal"
            if not wal_path.exists():
                continue
            msgs = self._caches.get(uid)
            if not msgs:
                try:
                    wal_path.unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            unflushed = [m for m in msgs if not m.get("flushed")]
            if not unflushed:
                try:
                    wal_path.unlink(missing_ok=True)
                except Exception:
                    pass
            else:
                try:
                    lines = "\n".join(
                        json.dumps({k: v for k, v in m.items() if k != "flushed"},
                                   ensure_ascii=False)
                        for m in unflushed
                    )
                    wal_path.write_text(lines + "\n", encoding="utf-8")
                except Exception:
                    pass

    # ═══════════════════════════════════════════════════
    #  管理
    # ═══════════════════════════════════════════════════

    def update_config(self, config: dict):
        """热更新缓存配置（仅支持 hotcache_max_per_user）"""
        max_val = config.get("hotcache_max_per_user")
        if max_val is not None and max_val > 0:
            self._max_per_user = int(max_val)

    def clear(self, user_id: str | None = None):
        """清空缓存（同时清除 WAL 文件）"""
        if user_id:
            self._caches.pop(user_id, None)
            if self._wal_dir:
                try:
                    (self._wal_dir / f"{user_id}.wal").unlink(missing_ok=True)
                except Exception:
                    pass
        else:
            self._caches.clear()
            if self._wal_dir and self._wal_dir.exists():
                import shutil
                shutil.rmtree(self._wal_dir, ignore_errors=True)
                self._wal_dir.mkdir(parents=True, exist_ok=True)

    def stats(self) -> dict[str, int]:
        """返回每用户的消息数（调试用）"""
        return {uid: len(q) for uid, q in self._caches.items()}

    def wal_size(self) -> dict[str, int]:
        """返回 WAL 文件大小（调试用）"""
        if not self._wal_dir or not self._wal_dir.exists():
            return {}
        sizes = {}
        for p in self._wal_dir.iterdir():
            if p.suffix == ".wal":
                try:
                    sizes[p.stem] = p.stat().st_size
                except Exception:
                    pass
        return sizes
