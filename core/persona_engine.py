"""画像引擎 — L3Runner"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ..storage.persona_store import PersonaStore
from ..storage.atom_store import AtomStore
from ..storage.diary_store import DiaryStore
from .adapters import LLMProvider
from .capturer import Capturer


class PersonaEngine:
    """
    用户画像引擎 — L3Runner

    定期读取旧画像 + 最新日记 + 最新原子 → LLM 生成新画像。
    增量更新，不是每次重写全部。

    优化：
    - 画像读取使用 LRU 缓存（TTL 60秒），避免频繁读文件
    - 日记加载改为批量 SQL 查询，消除 N+1
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        persona_store: PersonaStore,
        diary_store: DiaryStore,
        atom_store: AtomStore,
        capturer: Capturer,
        prompts_dir: str,
        config: dict[str, Any] | None = None,
    ):
        self.llm = llm_provider
        self.persona_store = persona_store
        self.diary_store = diary_store
        self.atom_store = atom_store
        self.capturer = capturer
        self.config = config or {}

        # 加载 prompt
        prompt_path = Path(prompts_dir) / "persona.txt"
        self.prompt_template = (
            prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
        )

        # 缓存统计
        self._persona_cache: dict[str, str | None] = {}
        self._persona_cache_time: float = 0

    async def get_persona(self, user_id: str) -> str | None:
        """获取用户画像（带 60 秒缓存）"""
        import time
        now = time.time()
        cached = self._persona_cache.get(user_id, ...)
        if cached is not ... and now - self._persona_cache_time < 60:
            return cached  # type: ignore

        persona = await self.persona_store.read(user_id)
        self._persona_cache[user_id] = persona
        self._persona_cache_time = now
        return persona

    async def update_persona(self, user_id: str) -> str | None:
        """增量更新用户画像"""
        old_persona = await self.persona_store.read(user_id) or "（还没有画像）"

        # 批量获取最近日记（一次 SQL 查询替代 N+1）
        recent_diaries = await self._get_recent_diaries_batch(user_id, count=5)

        # 取最近的重要原子
        recent_atoms = await self.atom_store.get_by_user(user_id)
        recent_atoms = [a for a in recent_atoms if a.importance > 0.3][:20]
        atoms_text = "\n".join(
            f"- [{a.atom_type.value}] {a.content} (重要度:{a.importance})"
            for a in recent_atoms
        )

        user_prompt = (
            f"旧画像：\n{old_persona}\n\n"
            f"最近的日记：\n{recent_diaries}\n\n"
            f"最近的记忆原子：\n{atoms_text}\n"
        )

        try:
            new_persona = await self.llm.chat(self.prompt_template, user_prompt)
            if new_persona and new_persona.strip():
                await self.persona_store.write(user_id, new_persona.strip())
                # 失效缓存
                self._persona_cache.pop(user_id, None)
                return new_persona.strip()
        except Exception as e:
            logger.warning(f"[Memory] 更新画像失败: {e}")
        return None

    async def _get_recent_diaries_batch(self, user_id: str, count: int = 5) -> str:
        """
        批量获取最近 N 篇日记（一次 SQL 查询）
        替代原来的 N+1 查询方式
        """
        rows = await self.diary_store.fetch("""
            SELECT date, content FROM diary_entries
            WHERE user_id = ?
            ORDER BY date DESC
            LIMIT ?
        """, (user_id, count))

        if not rows:
            return ""

        entries = []
        for r in rows:
            date_str = r[0]
            content = r[1] or ""
            entries.append(f"--- {date_str} ---\n{content[:500]}")

        return "\n\n".join(entries)

    async def invalidate_cache(self, user_id: str):
        """失效用户画像缓存（外部调画像更新后调用）"""
        self._persona_cache.pop(user_id, None)
