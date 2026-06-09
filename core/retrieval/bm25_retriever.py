"""文档路：FTS5 全文检索（替代原 LIKE %kw% 全表扫描）"""

from __future__ import annotations

from typing import Any

from ...models.memory_atom import MemoryAtom
from ...storage.atom_store import AtomStore


class BM25Retriever:
    """文档路检索器

    利用 memory_atoms_fts (FTS5) 做关键词检索，
    按 BM25 × 重要度 加权排序。
    """

    def __init__(self, atom_store: AtomStore):
        self.atom_store = atom_store

    async def retrieve(
        self,
        keywords: list[str],
        user_ids: list[str],
        k: int = 5,
    ) -> list[MemoryAtom]:
        """FTS5 全文检索

        Args:
            keywords: 关键词列表（已分词去停用词）
            user_ids: 用户 ID 列表（含关联身份）
            k: 返回 top N

        Returns:
            按相关度降序排列的 MemoryAtom 列表
        """
        if not keywords:
            return []

        # 构建 FTS5 query：中文词用短语 `"词"`，英文/数字直接用
        fts_terms = []
        for kw in keywords:
            if not kw or len(kw) < 1:
                continue
            # 纯英文/数字：直接作为 token
            if kw.isascii() and kw.isalnum():
                fts_terms.append(kw)
            else:
                # 中文/混合：短语精确匹配
                fts_terms.append(f'"{kw}"')

        if not fts_terms:
            return []

        fts_query = " OR ".join(fts_terms)

        # 复用 atom_store 已有的 search_fts（含权限过滤 + 重要度加权）
        uid = user_ids[0] if user_ids else ""
        extra = user_ids[1:] if len(user_ids) > 1 else None
        return await self.atom_store.search_fts(
            query=fts_query,
            user_id=uid,
            k=k,
            extra_user_ids=extra,
        )
