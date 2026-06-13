"""图谱存储 — SQLite 实现

nodes(id TEXT PK, name, type, metadata, embedding, ...)
edges(id TEXT PK, from_node, to_node, relation_type, diary_id, weight, ...)
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from ..models.graph_models import GraphNode, SocialEdge
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
            now = self._now_ts()
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
            (blob, model_name, self._now_ts(), node_id),
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
        """添加边"""
        now = self._now_ts()
        try:
            await self.execute("""
                INSERT INTO edges
                (id, from_node, to_node, relation_type, diary_id,
                 weight, confidence, status, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', '{}', ?, ?)
                ON CONFLICT(from_node, to_node, relation_type)
                DO UPDATE SET weight = weight + ?, updated_at = excluded.updated_at
            """, (
                edge_key, source_node_id, target_node_id, relation_type,
                source_memory_id, weight, confidence, now, now, weight,
            ))
            return edge_key
        except Exception:
            return None

    async def increment_edge_weight(
        self,
        from_node: str,
        to_node: str,
        relation_type: str,
        amount: float = 1.0,
        diary_id: int = 0,
    ) -> float:
        """增量更新边权重，不存在则创建

        使用 ON CONFLICT 确保并发安全，带重试兜底。

        Args:
            from_node: 源节点 ID
            to_node: 目标节点 ID
            relation_type: 边类型
            amount: 增量值（首次插入时作为初始 weight）
            diary_id: 关联日记 ID（首次插入时使用）

        Returns:
            更新后的 weight
        """
        now = self._now_ts()
        src, tgt = (from_node, to_node) if from_node < to_node else (to_node, from_node)
        edge_key = f"{relation_type}:{src}:{tgt}"

        # 最多重试 3 次，处理极端并发下的 UNIQUE 冲突
        for attempt in range(3):
            try:
                await self.execute("""
                    INSERT INTO edges (id, from_node, to_node, relation_type, diary_id,
                                       weight, confidence, status, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0.6, 'active', '{}', ?, ?)
                    ON CONFLICT(from_node, to_node, relation_type)
                    DO UPDATE SET weight = weight + ?, updated_at = excluded.updated_at
                """, (edge_key, src, tgt, relation_type, diary_id, amount, now, now, amount))
                break  # 成功则跳出重试
            except Exception:
                if attempt == 2:
                    raise  # 最后一次重试也失败，向上抛
                await asyncio.sleep(0.05 * (attempt + 1))  # 退避等待

        row = await self.fetchone(
            "SELECT weight FROM edges WHERE from_node=? AND to_node=? AND relation_type=?",
            (src, tgt, relation_type),
        )
        return row[0] if row else amount

    async def get_overview_stats(self) -> dict:
        """图谱概览统计"""
        try:
            node_rows = await self.fetch("SELECT type, COUNT(*) FROM nodes GROUP BY type")
            edge_rows = await self.fetch(
                "SELECT relation_type, COUNT(*) FROM edges WHERE status='active' GROUP BY relation_type"
            )
            return {
                "nodes": {r[0]: r[1] for r in node_rows},
                "edges": {r[0]: r[1] for r in edge_rows},
            }
        except Exception:
            return {"nodes": {}, "edges": {}}

    # ── 辅助 ──────────────────────────────────────────

    # ── 社交边 CRUD ────────────────────────────────────

    async def upsert_social_edge(self, edge: SocialEdge) -> SocialEdge | None:
        """创建或更新社交关系边"""
        now = self._now_ts()
        eid = edge.edge_id
        meta = json.dumps({"cap": edge.cap, "source": edge.source,
                           "from_user": edge.from_user, "to_user": edge.to_user})
        from_node = f"user:{edge.from_user}"
        to_node = f"user:{edge.to_user}"

        try:
            await self.execute("""
                INSERT INTO edges (id, from_node, to_node, relation_type, weight,
                                   status, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(from_node, to_node, relation_type)
                DO UPDATE SET weight = excluded.weight,
                              status = excluded.status,
                              metadata = excluded.metadata,
                              updated_at = excluded.updated_at
            """, (eid, from_node, to_node, edge.relation_type, edge.weight,
                  edge.status, meta, now, now))
            edge.id = eid
            edge.created_at = edge.created_at or now
            edge.updated_at = now
            return edge
        except Exception:
            return None

    async def get_social_edge(self, from_user: str, to_user: str,
                              relation_type: str = "friend_of") -> SocialEdge | None:
        """查询单条社交关系"""
        a, b = (from_user, to_user) if from_user < to_user else (to_user, from_user)
        eid = f"social:{a}:{relation_type}:{b}"
        try:
            row = await self.fetchone(
                "SELECT id, from_node, to_node, relation_type, weight, status, metadata, "
                "       created_at, updated_at "
                "FROM edges WHERE id = ?", (eid,)
            )
            if row:
                return self._row_to_social_edge(row)
        except Exception:
            pass
        return None

    async def query_social_neighbors(
        self,
        user_id: str,
        min_weight: float = 0.0,
        status: str | None = None,
        relation_types: list[str] | None = None,
    ) -> list[SocialEdge]:
        """查询用户的社交邻居（从 user 节点找 friend_of 等双向边）"""
        uid_node = f"user:{user_id}"
        clauses = ["(from_node = ? OR to_node = ?)"]
        params: list = [uid_node, uid_node]

        if relation_types:
            placeholders = ",".join("?" for _ in relation_types)
            clauses.append(f"relation_type IN ({placeholders})")
            params.extend(relation_types)

        if status:
            clauses.append("status = ?")
            params.append(status)

        try:
            rows = await self.fetch(
                f"SELECT id, from_node, to_node, relation_type, weight, status, metadata, "
                f"       created_at, updated_at "
                f"FROM edges WHERE {' AND '.join(clauses)} ORDER BY weight DESC",
                params,
            )
            result = []
            for row in rows:
                edge = self._row_to_social_edge(row)
                if edge and edge.effective_weight >= min_weight:
                    result.append(edge)
            return result
        except Exception:
            return []

    async def query_pending_confirmations(self, user_id: str) -> list[SocialEdge]:
        """查询待确认的社交关系（别人声称与你有关）"""
        uid_node = f"user:{user_id}"
        try:
            rows = await self.fetch(
                "SELECT id, from_node, to_node, relation_type, weight, status, metadata, "
                "       created_at, updated_at "
                "FROM edges WHERE to_node = ? AND status = 'pending' "
                "  AND relation_type IN ('friend_of', 'family_of', 'trusted_by') "
                "ORDER BY created_at DESC",
                (uid_node,),
            )
            return [self._row_to_social_edge(r) for r in rows if r]
        except Exception:
            return []

    async def update_social_edge_weight(self, edge_id: str, weight: float):
        """更新社交边权重"""
        await self.execute(
            "UPDATE edges SET weight = ?, updated_at = ? WHERE id = ?",
            (weight, self._now_ts(), edge_id),
        )

    async def set_social_edge_status(self, edge_id: str, status: str):
        """设置社交边状态（confirm / reject / block）"""
        await self.execute(
            "UPDATE edges SET status = ?, updated_at = ? WHERE id = ?",
            (status, self._now_ts(), edge_id),
        )

    async def count_social_edges(self, user_id: str | None = None) -> int:
        """统计社交边数量"""
        if user_id:
            uid_node = f"user:{user_id}"
            row = await self.fetchone(
                "SELECT COUNT(*) FROM edges "
                "WHERE (from_node = ? OR to_node = ?) "
                "  AND relation_type IN ('friend_of','family_of','trusted_by')",
                (uid_node, uid_node),
            )
        else:
            row = await self.fetchone(
                "SELECT COUNT(*) FROM edges "
                "WHERE relation_type IN ('friend_of','family_of','trusted_by')",
            )
        return row[0] if row else 0

    # ── 辅助 ──────────────────────────────────────────

    @staticmethod
    def _now_ts() -> float:
        """当前时间戳（Unix epoch float，与全系统一致）"""
        return time.time()

    @staticmethod
    def _fmt_ts(ts: float | str) -> str:
        """时间戳 → 可读字符串，兼容新旧两种格式"""
        if isinstance(ts, (int, float)):
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
        if isinstance(ts, str):
            # 旧 ISO 格式：解析后格式化
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(ts)
                return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return ts[:19]
        return str(ts)[:19]

    @staticmethod
    def _row_to_social_edge(row) -> SocialEdge | None:
        """将 SQL 行转为 SocialEdge"""
        try:
            from ..models.graph_models import SocialEdge
            eid, from_node, to_node, rel_type, weight, status, meta_str, created, updated = row
            meta = json.loads(meta_str) if isinstance(meta_str, str) and meta_str not in ("{}", "") else {}

            # from_node = "user:xxx" → 提取 uid
            from_user = from_node.split(":", 1)[1] if ":" in from_node else from_node
            to_user = to_node.split(":", 1)[1] if ":" in to_node else to_node

            return SocialEdge(
                id=eid,
                from_user=from_user,
                to_user=to_user,
                relation_type=rel_type,
                status=status,
                weight=weight,
                cap=meta.get("cap", 0.4),
                source=meta.get("source", "unknown"),
                created_at=created,
                updated_at=updated,
            )
        except Exception:
            return None
