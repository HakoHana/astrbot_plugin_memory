"""Agent 记忆工具 — 让 LLM 可以主动搜索和写入记忆"""

from __future__ import annotations

import json
from typing import Any

from astrbot.api import logger
from astrbot.core.agent.tool import FunctionTool

from .memory_core import MemoryCore


def _json_result(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


class RecallMemoryTool(FunctionTool):
    """主动搜索记忆工具"""

    def __init__(self, memory_core: MemoryCore):
        super().__init__()
        self.memory_core = memory_core

    name = "recall_long_term_memory"
    description = (
        "当对话需要参考长期记忆中的信息时，调用此工具搜索相关记忆。"
        "使用简短的关键词，不要复制整个用户消息。"
        "当用户问「你还记得吗」「之前说的」「帮我回忆」等时，优先调用此工具。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词，简短的话题、实体名、偏好等",
            },
            "k": {
                "type": "integer",
                "description": "返回结果数量",
                "default": 3,
            },
        },
        "required": ["query"],
    }

    async def call(self, **kwargs) -> str:
        query = kwargs.get("query", "").strip()
        k = int(kwargs.get("k", 3))
        if not query:
            return _json_result({"count": 0, "results": [], "error": "query is empty"})

        try:
            user_id = "Hana"
            atoms = await self.memory_core.retriever.recall(user_id, query, k)
            results = [
                {"content": a.content, "type": a.atom_type.value, "importance": a.importance, "date": a.diary_date}
                for a in atoms
            ]
            return _json_result({"count": len(results), "results": results})
        except Exception as e:
            logger.error(f"RecallMemoryTool error: {e}")
            return _json_result({"count": 0, "results": [], "error": str(e)})


class MemorizeMemoryTool(FunctionTool):
    """主动写入记忆工具"""

    def __init__(self, memory_core: MemoryCore):
        super().__init__()
        self.memory_core = memory_core

    name = "memorize_long_term_memory"
    description = (
        "当用户明确要求你记住某些信息时（如「帮我记住」「别忘了」「请记住」），"
        "调用此工具将信息写入长期记忆。"
        "将信息整理为简洁的一句话或几个关键点。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "要记住的信息内容，简洁的一句话",
            },
            "importance": {
                "type": "number",
                "description": "重要度（0~1），默认为 0.7",
                "default": 0.7,
            },
        },
        "required": ["content"],
    }

    async def call(self, **kwargs) -> str:
        content = kwargs.get("content", "").strip()
        importance = float(kwargs.get("importance", 0.7))
        if not content:
            return _json_result({"success": False, "error": "content is empty"})

        try:
            from ..models.memory_atom import MemoryAtom, AtomType
            import time

            today = time.strftime("%Y-%m-%d")

            # 先确保有日记条目
            diary = await self.memory_core.diary_store.read("Hana", today)
            if not diary:
                await self.memory_core.diary_store.append(
                    "Hana", today, f"## {time.strftime('%H:%M')}\n\n{content}"
                )

            # 写入原子
            atom = MemoryAtom(
                user_id="Hana",
                diary_date=today,
                content=content[:200],
                atom_type=AtomType.FACTUAL,
                importance=importance,
            )
            atom.prepare_insert()
            aid = await self.memory_core.atom_store.insert(atom)

            # 索引到图谱
            if self.memory_core.graph_engine:
                await self.memory_core.graph_engine.index_atom(atom)

            return _json_result({"success": True, "id": aid, "content": content})
        except Exception as e:
            logger.error(f"MemorizeMemoryTool error: {e}")
            return _json_result({"success": False, "error": str(e)})
