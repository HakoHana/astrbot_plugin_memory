"""多路检索编排 — BM25 文档路 + Graph 图路 + 可选 Vector 向量路

保留 DualRouteRetriever 作为向后兼容别名。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..models.memory_atom import MemoryAtom
from ..core.logger import logger

from .bm25_retriever import BM25Retriever
from .graph_entity_retriever import GraphEntityRetriever
from .vector_retriever import VectorRetriever
from .rrf_fusion import rrf_merge


class MultiRouteRetriever:
    """多路检索引擎

    协调 BM25 文档路 + Graph 图路 + 可选 Vector 向量路，RRF 融合排序。
    向量路默认不启用，需传入 VectorRetriever 实例。
    """

    def __init__(
        self,
        bm25_retriever: BM25Retriever,
        graph_retriever: GraphEntityRetriever,
        vector_retriever: VectorRetriever | None = None,
    ):
        self.bm25 = bm25_retriever
        self.graph = graph_retriever
        self.vector = vector_retriever

    async def retrieve(
        self,
        keywords: list[str],
        user_ids: list[str],
        k: int = 5,
    ) -> list[MemoryAtom]:
        """多路检索入口

        三路并行 → RRF 融合：
        1. BM25 文档路 → from memory_atoms_fts
        2. Graph 图路   → entity→graph→diary→fact
        3. Vector 向量路 → embedding 余弦相似度（可选）
        """
        if not keywords or not user_ids:
            return []

        logger.debug(
            f"[MultiRoute] keywords={keywords} users={user_ids} top_k={k}"
        )

        # 三路并行（向量路可选）
        tasks = [
            asyncio.create_task(
                self.bm25.retrieve(keywords, user_ids, k=k * 3)
            ),
            asyncio.create_task(
                self.graph.retrieve(keywords, user_ids, k=k * 2)
            ),
        ]
        if self.vector:
            tasks.append(
                asyncio.create_task(
                    self.vector.retrieve(keywords, user_ids, k=k * 2)
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_lists: list[list[MemoryAtom]] = []
        for r in results:
            if isinstance(r, list):
                all_lists.append(r)
            elif isinstance(r, Exception):
                logger.warning(f"[MultiRoute] 一路检索异常: {r}")

        logger.debug(
            f"[MultiRoute] bm25={len(all_lists[0]) if all_lists else 0} "
            f"graph={len(all_lists[1]) if len(all_lists) > 1 else 0} "
            f"vector={len(all_lists[2]) if len(all_lists) > 2 else 0}"
        )

        if not all_lists:
            return []

        fused = rrf_merge(all_lists, top_k=k)
        return fused


# ── 向后兼容别名 ──

class DualRouteRetriever(MultiRouteRetriever):
    """旧名称兼容 — 等价于不带向量路的 MultiRouteRetriever"""

    def __init__(
        self,
        bm25_retriever: BM25Retriever,
        graph_retriever: GraphEntityRetriever,
    ):
        super().__init__(
            bm25_retriever=bm25_retriever,
            graph_retriever=graph_retriever,
            vector_retriever=None,
        )
