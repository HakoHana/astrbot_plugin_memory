"""指令处理器 — 处理用户命令"""

from __future__ import annotations

import time
from typing import Any

from ..storage.diary_store import DiaryStore
from ..storage.atom_store import AtomStore
from ..storage.persona_store import PersonaStore
from ..core.retriever import Retriever


class CommandHandler:
    """处理 /日记、/记忆、/记忆 搜索 等指令"""

    def __init__(
        self,
        diary_store: DiaryStore,
        atom_store: AtomStore,
        persona_store: PersonaStore,
        retriever: Retriever,
    ):
        self.diary_store = diary_store
        self.atom_store = atom_store
        self.persona_store = persona_store
        self.retriever = retriever

    async def handle_diary(self, user_id: str, args: list[str]) -> str:
        """处理 /日记 [日期]"""
        date_str = args[0] if args else time.strftime("%Y-%m-%d")

        content = await self.diary_store.read(user_id, date_str)
        if not content:
            return f"📔 {date_str} 还没有日记哦~"
        return f"📔 {date_str} 的日记：\n\n{content}"

    async def handle_diary_list(self, user_id: str, args: list[str]) -> str:
        """处理 /日记 列表 [年月]"""
        if len(args) >= 2:
            year, month = args[0], args[1].zfill(2)
            dates = await self.diary_store.list_dates(user_id, year, month)
            if not dates:
                return f"📔 {year}年{month}月还没有日记~"
            lines = [d["date"] for d in dates]
            return f"📔 {year}年{month}月的日记：\n" + "\n".join(lines)
        else:
            months = await self.diary_store.list_months(user_id)
            if not months:
                return "📔 还没有写过日记~"
            lines = [f"{m['year']}年{m['month']}月" for m in months]
            return "📔 有日记的月份：\n" + "\n".join(lines)

    async def handle_memory(self, user_id: str) -> str:
        """处理 /记忆 — 查看画像和统计"""
        persona = await self.persona_store.read(user_id)
        stats = await self.atom_store.get_stats(user_id)

        parts = []
        if persona:
            parts.append(f"🧠 关于你：\n{persona[:500]}")
        parts.append(f"📊 统计：共 {stats['total']} 条记忆")
        if stats.get("by_type"):
            type_labels = {
                "episodic": "事件", "factual": "事实", "preference": "偏好",
                "planned": "计划", "relational": "关系",
            }
            by_type = "\n".join(
                f"  - {type_labels.get(t, t)}: {c}条"
                for t, c in stats["by_type"].items()
            )
            parts.append(by_type)
        return "\n\n".join(parts) if parts else "🧠 还没有关于你的记忆~"

    async def handle_search(self, user_id: str, query: str) -> str:
        """处理 /记忆 搜索 <关键词>"""
        if not query:
            return "💡 请输入关键词，例如：/记忆 搜索 告白"

        result = await self.retriever.get_context_memories(user_id, query, k=5)

        if not result.atoms:
            return f"🔍 没有找到和「{query}」相关的记忆~"

        lines = [f"🔍 「{query}」相关的记忆："]
        for a in result.atoms:
            date = f" ({a.diary_date})" if a.diary_date else ""
            lines.append(f"- [{a.atom_type.value}]{date} {a.content[:200]}")
            lines.append(f"  重要度: {a.importance} | ID: {a.atom_id}")

        return "\n".join(lines)

    async def handle_delete(self, user_id: str, atom_id_str: str) -> str:
        """处理 /记忆 删除 <id>"""
        try:
            atom_id = int(atom_id_str)
        except ValueError:
            return "❌ 请输入有效的记忆 ID"

        success = await self.atom_store.delete(atom_id, user_id)
        if success:
            return f"✅ 已删除记忆 #{atom_id}"
        return "❌ 找不到这条记忆，或你没有权限删除"

    async def handle_stats(self, user_id: str) -> str:
        """处理 /记忆 统计"""
        stats = await self.atom_store.get_stats(user_id)
        if stats["total"] == 0:
            return "📊 还没有任何记忆~"

        type_labels = {
            "episodic": "事件", "factual": "事实", "preference": "偏好",
            "planned": "计划", "relational": "关系", "unknown": "未分类",
        }
        by_type = "\n".join(
            f"  - {type_labels.get(t, t)}: {c}条"
            for t, c in stats["by_type"].items()
        )
        return (
            f"📊 记忆统计\n"
            f"总计: {stats['total']} 条\n\n"
            f"按类型分布：\n{by_type}"
        )
