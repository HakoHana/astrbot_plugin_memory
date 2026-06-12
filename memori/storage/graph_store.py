"""图谱存储 — SQLite 实现

nodes(id TEXT PK, name, type, metadata, embedding, ...)
edges(id TEXT PK, from_node, to_node, relation_type, diary_id, weight, ...)
"""

from __future__ import annotations

import json
from typing import Any

from ..models.graph_models import GraphNode
from .base_store import BaseDbStore


class GraphStore(BaseDbStore):
    """持久化图谱节点和边"""
    _pragmas = ["PRAGMA journal_mode = WAL", "PRAGMA foreign_keys = ON"]
    _busy_timeout_ms = 10000

    async def initialize(self):
        async with self._connect() as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    embedding BLOB,
                    embedding_model TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS edges (
                    id TEXT PRIMARY KEY,
                    from_node TEXT NOT NULL,
                    to_node TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    diary_id INTEGER DEFAULT 0,
                    weight REAL DEFAULT 1.0,
                    confidence REAL DEFAULT 0.8,
                    status TEXT DEFAULT 'active',
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(from_node, to_node, relation_type)
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(relation_type)
            """)
            await db.commit()

    # ── 节点操作 ──────────────────────────────────────

    async def upsert_nodes(self, nodes: list[GraphNode]) -> dict[str, str]:
        """创建或更新节点

        Args:
            nodes: GraphNode 列表（node_key = type:canonical_value 作为 ID）

        Returns:
            dict[node_key, node_id] — 两者相同（node_key = id）
        """
        if not nodes:
            return {}

        result: dict[str, str] = {}
        async with self._connect() as db:
            now = self._now_iso()
            for node in nodes:
                if not node.value or not node.value.strip():
                    continue
                nid = node.node_key
                meta_str = json.dumps(node.metadata) if isinstance(node.metadata, dict) else str(node.metadata)

                try:
                    existing = await db.execute_fetchall(
                        "SELECT metadata FROM nodes WHERE id=?", (nid,)
                    )
                    if existing:
                        old_meta = existing[0][0] or "{}"
                        try:
                            om = json.loads(old_meta) if isinstance(old_meta, str) else {}
                        except Exception:
                            om = {}
                        new_count = om.get("count", 0) + 1
                        merged = json.dumps({"count": new_count, **node.metadata})
                        await db.execute("""
                            UPDATE nodes SET name=?, metadata=?, updated_at=? WHERE id=?
                        """, (node.value, merged, now, nid))
                    else:
                        await db.execute("""
                            INSERT INTO nodes (id, name, type, metadata, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (nid, node.value, node.node_type, meta_str, now, now))
                    result[node.node_key] = nid
                except Exception:
                    pass

            await db.commit()
        return result

    async def update_node_embedding(self, node_id: str, embedding: list[float], model_name: str):
        blob = json.dumps(embedding).encode("utf-8")
        await self.execute(
            "UPDATE nodes SET embedding=?, embedding_model=?, updated_at=? WHERE id=?",
            (blob, model_name, self._now_iso(), node_id),
        )

    async def search_vector(
        self,
        query_embed: list[float],
        k: int = 10,
        model_name: str | None = None,
    ) -> list[tuple[str, float]]:
        """向量搜索节点：余弦相似度排序

        Returns:
            [(node_id, cosine_similarity), ...]  — node_id 是 TEXT（如 "entity:hako"）
        """
        if not query_embed:
            return []

        model_filter = "AND embedding_model=?" if model_name else ""
        try:
            rows = await self.fetch(
                f"SELECT id, embedding FROM nodes "
                f"WHERE embedding IS NOT NULL {model_filter} AND type IN ('entity','topic','user') "
                f"ORDER BY updated_at DESC LIMIT 500",
                (model_name,) if model_name else (),
            )
        except Exception:
            return []

        if not rows:
            return []

        q_norm = sum(x * x for x in query_embed) ** 0.5
        if q_norm < 1e-9:
            return []

        scored: list[tuple[str, float]] = []
        for nid, blob in rows:
            if not blob:
                continue
            try:
                stored = json.loads(blob.decode("utf-8"))
            except Exception:
                continue
            if not stored or len(stored) != len(query_embed):
                continue
            dot = sum(a * b for a, b in zip(query_embed, stored))
            n_norm = sum(x * x for x in stored) ** 0.5
            if n_norm < 1e-9:
                continue
            cos_sim = dot / (q_norm * n_norm)
            scored.append((nid, max(0.0, cos_sim)))

        scored.sort(key=lambda x: -x[1])
        return scored[:k]

    # ── 边操作 ────────────────────────────────────────

    async def add_edge_by_ids(
        self,
        edge_key: str,
        source_node_id: str,
        target_node_id: str,
        relation_type: str,
        source_memory_id: int,
        weight: float = 1.0,
        confidence: float = 0.8,
    ) -> str | None:
        """添加边

        ON CONFLICT 时 weight += 0.1（标记关系增强）。
        """
        now = self._now_iso()
        try:
            await self.execute("""
                INSERT INTO edges
                (id, from_node, to_node, relation_type, diary_id,
                 weight, confidence, status, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', '{}', ?, ?)
                ON CONFLICT(from_node, to_node, relation_type)
                DO UPDATE SET weight = weight + 0.1, updated_at = excluded.updated_at
            """, (
                edge_key, source_node_id, target_node_id, relation_type,
                source_memory_id, weight, confidence, now, now,
            ))
            return edge_key
        except Exception:
            return None

    # ── 辅助 ──────────────────────────────────────────

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
