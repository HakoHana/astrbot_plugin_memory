"""画像引擎 — L3Runner"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

    async def get_persona(self, user_id: str) -> str | None:
        """获取用户画像"""
        return await self.persona_store.read(user_id)

    async def update_persona(self, user_id: str) -> str | None:
        """增量更新用户画像"""
        # 收集素材
        old_persona = await self.persona_store.read(user_id) or "（还没有画像）"

        # 取最近几天的日记
        recent_diaries = await self._get_recent_diaries(user_id)

        # 取最近的重要原子
        recent_atoms = await self.atom_store.get_by_user(user_id)
        recent_atoms = [a for a in recent_atoms if a.importance > 0.3][:20]
        atoms_text = "\n".join(
            f"- [{a.atom_type.value}] {a.content} (重要度:{a.importance})"
            for a in recent_atoms
        )

        # 构建 prompt
        user_prompt = (
            f"旧画像：\n{old_persona}\n\n"
            f"最近的日记：\n{recent_diaries}\n\n"
            f"最近的记忆原子：\n{atoms_text}\n"
        )

        try:
            new_persona = await self.llm.chat(self.prompt_template, user_prompt)
            if new_persona and new_persona.strip():
                await self.persona_store.write(user_id, new_persona.strip())
                return new_persona.strip()
        except Exception:
            pass
        return None

    async def _get_recent_diaries(self, user_id: str, count: int = 5) -> str:
        """获取最近几篇日记"""
        months = await self.diary_store.list_months(user_id)
        diaries = []
        for m in months[:3]:  # 最近 3 个月
            dates = await self.diary_store.list_dates(
                user_id, m["year"], m["month"]
            )
            for d in dates[:5]:
                content = await self.diary_store.read(user_id, d["date"])
                if content:
                    diaries.append(f"--- {d['date']} ---\n{content[:500]}")
                if len(diaries) >= count:
                    break
            if len(diaries) >= count:
                break
        return "\n\n".join(diaries[:count])
