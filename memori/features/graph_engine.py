"""图谱引擎 — 实体关系记忆（非检索引擎）

价值定位：
  图谱不做关键词/向量检索（那是 AtomStore 和 BM25 的事）。
  图谱只做：实体→关系→关联发现。

写入入口（单一）：
  index_diary(diary_id, content, entities) — 被 Capturer 回调

检索入口：
  query_neighbors(entity) → 关联的节点和边
  find_linked_diaries(node_ids) → 关联的日记 ID

新增表结构（graph.db）：
  nodes(id TEXT PK, name, type, metadata, embedding, ...)
    — id = "entity:hako" / "topic:coffee" / "date:20260612"
  edges(id TEXT PK, from_node, to_node, relation_type, diary_id, weight, ...)
    — relation_type = mention | co_occur | belongs_to | same_as
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from ..models.graph_models import GraphNode
from ..storage.graph_store import GraphStore
from ..storage.atom_store import AtomStore
from ..storage.diary_store import DiaryStore
from ..core.interfaces import IGraphEngine


class GraphEngine(IGraphEngine):
    """
    图谱引擎（简化版）

    节点类型：
    - entity/user: 人/事物（从日记 [[链接]] 或传参提取）
    - topic/emotion/date: 元数据（从 frontmatter 提取）
    - diary: 日记节点（如 "diary:diary_123"）

    边类型：
    - mention:     entity → diary（实体在日记中被提及）
    - co_occur:    entity ↔ entity（共现，weight=次数）
    - belongs_to:  diary → date|topic|emotion（归属）
    - same_as:     entity ↔ entity（同一现实对象的不同名）
    """

    NODE_TYPES_FOR_SEARCH = ("entity", "user", "topic", "emotion")

    def __init__(
        self,
        graph_store: GraphStore,
        atom_store: AtomStore,
        diary_store: DiaryStore,
        config: dict[str, Any] | None = None,
        embed_provider=None,
    ):
        self.graph_store = graph_store
        self.atom_store = atom_store
        self.diary_store = diary_store
        self.config = config or {}
        self.embed_provider = embed_provider

    # ── 单一写入入口 ───────────────────────────────────

    async def index_diary(
        self,
        diary_id: int,
        content: str,
        entities: list[str] | None = None,
    ):
        """从日记内容构建图谱索引

        步骤：
        1. 创建 diary 节点
        2. 解析 [[wikilinks]] + entities 参数 → 创建 entity/user 节点
        3. 解析 frontmatter → topic/emotion/date 节点
        4. 创建 mention 边（entity → diary）
        5. 创建 belongs_to 边（diary → topic/emotion/date）
        6. 增量更新 co_occur 边（entity ↔ entity 共现计数）
        """
        from ..utils.diary_helper import extract_wikilinks

        # 1. 提取实体名称
        wikilinks = extract_wikilinks(content)
        all_names: list[str] = []
        seen: set[str] = set()
        for name in wikilinks + (entities or []):
            name = name.strip()
            if name and name not in seen:
                seen.add(name)
                all_names.append(name)

        # 2. 解析 frontmatter
        from ..utils.diary_helper import parse_diary_content
        fm, _ = parse_diary_content(content)

        # 3. 创建 diary 节点
        diary_cv = f"diary_{diary_id}"
        diary_nodes = [
            GraphNode(node_type="diary", value=f"#{diary_id}", canonical_value=diary_cv, metadata={"diary_id": diary_id})
        ]
        node_key_map = await self.graph_store.upsert_nodes(diary_nodes)
        diary_key = f"diary:{diary_cv}"
        diary_node_id: str | None = node_key_map.get(diary_key)

        # 4. 创建 entity/user 节点
        entity_nodes = []
        for name in all_names:
            cv = self._canonicalize(name)
            ntype = "entity"
            try:
                row = await self.atom_store.fetchone(
                    "SELECT uid FROM user_identities WHERE display_name=? LIMIT 1", (name,)
                )
                if row:
                    ntype = "user"
            except Exception:
                pass
            entity_nodes.append(GraphNode(node_type=ntype, value=name, canonical_value=cv, metadata={"diary_refs": 1}))
        if entity_nodes:
            node_key_map.update(await self.graph_store.upsert_nodes(entity_nodes))

        # 5. 创建 topic/emotion/date 元节点
        meta_nodes: list[GraphNode] = []
        date_str = fm.get("date", "")
        if date_str:
            meta_nodes.append(GraphNode(node_type="date", value=date_str, canonical_value=date_str.replace("-", ""), metadata={"count": 1}))
        mood = fm.get("mood", "")
        if mood:
            meta_nodes.append(GraphNode(node_type="emotion", value=mood, canonical_value=mood.lower().strip(), metadata={"count": 1}))
        for topic in (fm.get("topics") or []):
            t = str(topic).strip()
            if t:
                meta_nodes.append(GraphNode(node_type="topic", value=t, canonical_value=self._canonicalize(t), metadata={"count": 1}))
        if meta_nodes:
            node_key_map.update(await self.graph_store.upsert_nodes(meta_nodes))

        # 6. 创建 mention 边 (entity → diary)
        entity_ids: list[str] = []
        for name in all_names:
            cv = self._canonicalize(name)
            for prefix in ("entity:", "user:"):
                src_id = node_key_map.get(f"{prefix}{cv}")
                if src_id and diary_node_id:
                    await self.graph_store.add_edge_by_ids(
                        edge_key=f"diary:{diary_id}:mentions:{cv}",
                        source_node_id=src_id,
                        target_node_id=diary_node_id,
                        relation_type="mentions",
                        source_memory_id=diary_id,
                        weight=1.0,
                    )
                    entity_ids.append(src_id)
                    break

        # 7. 创建 belongs_to 边 (diary → date / topic / emotion)
        if diary_node_id:
            if date_str:
                dk = f"date:{date_str.replace('-', '')}"
                dn_id = node_key_map.get(dk)
                if dn_id:
                    await self.graph_store.add_edge_by_ids(
                        edge_key=f"diary:{diary_id}:on_date",
                        source_node_id=diary_node_id,
                        target_node_id=dn_id,
                        relation_type="belongs_to",
                        source_memory_id=diary_id,
                        weight=0.5,
                    )
            if mood:
                mk = f"emotion:{mood.lower().strip()}"
                mn_id = node_key_map.get(mk)
                if mn_id:
                    await self.graph_store.add_edge_by_ids(
                        edge_key=f"diary:{diary_id}:mood",
                        source_node_id=diary_node_id,
                        target_node_id=mn_id,
                        relation_type="belongs_to",
                        source_memory_id=diary_id,
                        weight=0.5,
                    )
            for topic in (fm.get("topics") or []):
                t = str(topic).strip()
                if t:
                    tk = f"topic:{self._canonicalize(t)}"
                    tn_id = node_key_map.get(tk)
                    if tn_id:
                        await self.graph_store.add_edge_by_ids(
                            edge_key=f"diary:{diary_id}:topic:{self._canonicalize(t)}",
                            source_node_id=diary_node_id,
                            target_node_id=tn_id,
                            relation_type="belongs_to",
                            source_memory_id=diary_id,
                            weight=0.5,
                        )

        # 8. co_occur 边增量更新
        if len(entity_ids) >= 2:
            await self._update_cooccur(entity_ids, diary_id)

        # 9. 实体 embedding（可选）
        if self.embed_provider and entity_ids:
            await self._compute_embeddings(entity_ids, all_names, node_key_map, meta_nodes)

    # ── co_occur 增量更新 ────────────────────────────

    async def _update_cooccur(self, entity_ids: list[str], diary_id: int):
        """增量更新 entity 间的 co_occur 边（新表 edges）

        对当前日记中出现的所有实体对：
        - 已有 co_occur 边 → weight += 1
        - 无 → 创建
        """
        for i in range(len(entity_ids)):
            for j in range(i + 1, len(entity_ids)):
                a, b = entity_ids[i], entity_ids[j]
                if a == b:
                    continue
                src, tgt = (a, b) if a < b else (b, a)
                edge_key = f"cooccur:{src}:{tgt}"

                existing = await self.graph_store.fetchone(
                    "SELECT weight FROM edges WHERE from_node=? AND to_node=? AND relation_type='co_occur'",
                    (src, tgt),
                )
                if existing:
                    new_weight = (existing[0] or 1.0) + 1.0
                    await self.graph_store.execute(
                        "UPDATE edges SET weight=?, updated_at=? WHERE from_node=? AND to_node=? AND relation_type='co_occur'",
                        (new_weight, self.graph_store._now_iso(), src, tgt),
                    )
                else:
                    await self.graph_store.add_edge_by_ids(
                        edge_key=edge_key,
                        source_node_id=src,
                        target_node_id=tgt,
                        relation_type="co_occur",
                        source_memory_id=diary_id,
                        weight=1.0,
                        confidence=0.6,
                    )

    # ── embedding 计算 ────────────────────────────────

    async def _compute_embeddings(
        self,
        entity_ids: list[str],
        names: list[str],
        node_key_map: dict[str, str],
        meta_nodes: list[GraphNode],
    ):
        if not self.embed_provider:
            return

        embed_texts: list[str] = []
        embed_targets: list[str] = []
        for name, nid in zip(names, entity_ids):
            embed_texts.append(name)
            embed_targets.append(nid)
        for mn in meta_nodes:
            if mn.node_type in ("topic", "emotion"):
                nid = node_key_map.get(mn.node_key)
                if nid:
                    embed_texts.append(mn.value)
                    embed_targets.append(nid)

        if not embed_texts:
            return
        try:
            embeddings = await self.embed_provider.embed_batch(embed_texts)
            model_name = type(self.embed_provider).__name__
            for nid, emb in zip(embed_targets, embeddings):
                await self.graph_store.update_node_embedding(nid, emb, model_name)
        except Exception:
            pass

    # ── 关系查询（API 用） ──────────────────────────────

    async def query_neighbors(self, entity_name: str) -> dict:
        """查询实体的邻居，返回子图（使用新表 nodes/edges）

        流程：模糊匹配节点名 → 查 edges 出邻居 → 收集子图
        """
        if not entity_name or not entity_name.strip():
            return {"nodes": [], "edges": []}

        cv = self._canonicalize(entity_name)

        # 匹配节点（新表 nodes）
        try:
            rows = await self.graph_store.fetch(
                "SELECT id, type, name FROM nodes "
                "WHERE (id LIKE ? OR name LIKE ?) AND type IN (?, ?, ?, ?) "
                "ORDER BY name ASC LIMIT 5",
                (f"%{cv}%", f"%{cv}%", *self.NODE_TYPES_FOR_SEARCH),
            )
        except Exception:
            return {"nodes": [], "edges": []}

        if not rows:
            return {"nodes": [], "edges": []}

        nodes: list[dict] = []
        node_ids: list[str] = []
        for r in rows:
            nid, ntype, name = r
            nodes.append({"id": nid, "type": ntype, "label": name})
            node_ids.append(nid)

        if not node_ids:
            return {"nodes": nodes, "edges": []}

        # 查邻居边（新表 edges）
        placeholders = ",".join("?" for _ in node_ids)
        try:
            edge_rows = await self.graph_store.fetch(
                f"""SELECT id, from_node, to_node, relation_type, weight
                    FROM edges
                    WHERE (from_node IN ({placeholders}) OR to_node IN ({placeholders}))
                      AND status = 'active'
                    ORDER BY weight DESC LIMIT 50""",
                node_ids + node_ids,
            )
        except Exception:
            edge_rows = []

        # 收集邻居节点去重
        neighbor_ids: set[str] = set(node_ids)
        edges: list[dict] = []
        for r in edge_rows:
            eid, fn, tn, rel, w = r
            edges.append({"id": eid, "from": fn, "to": tn, "relation_type": rel, "weight": w})
            neighbor_ids.add(fn)
            neighbor_ids.add(tn)

        # 补全邻居节点信息
        extra_ids = [nid for nid in neighbor_ids if nid not in node_ids]
        if extra_ids:
            ep = ",".join("?" for _ in extra_ids)
            try:
                extra_rows = await self.graph_store.fetch(
                    f"SELECT id, type, name FROM nodes WHERE id IN ({ep})",
                    extra_ids,
                )
                for r in extra_rows:
                    nodes.append({"id": r[0], "type": r[1], "label": r[2]})
            except Exception:
                pass

        return {"nodes": nodes, "edges": edges}

    async def find_linked_diaries(self, node_ids: list[str]) -> list[int]:
        """从实体节点沿 mention 边找到关联的 diary_id（新表 edges）

        Args:
            node_ids: 实体节点 ID 列表（如 ["entity:hako", "entity:coffee"]）

        Returns:
            关联的 diary_id 列表（int）
        """
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        try:
            rows = await self.graph_store.fetch(
                f"""SELECT DISTINCT diary_id
                    FROM edges
                    WHERE from_node IN ({placeholders})
                      AND relation_type = 'mentions'
                      AND diary_id > 0
                    ORDER BY diary_id DESC LIMIT 30""",
                node_ids,
            )
            return [r[0] for r in rows]
        except Exception:
            return []

    # ── 后台定时任务 ──────────────────────────────────────

    async def batch_cooccur(self) -> int:
        """增量统计 co_occur 边权重（新表 edges）

        扫描 mentions 边 → 按 diary_id 分组 → 统计共现 → 更新 co_occur weight
        """
        try:
            rows = await self.graph_store.fetch("""
                SELECT diary_id, from_node
                FROM edges
                WHERE relation_type = 'mentions' AND diary_id > 0
                ORDER BY diary_id, from_node
            """)
        except Exception:
            return 0

        diary_entities: dict[int, list[str]] = defaultdict(list)
        for r in rows:
            did, nid = r[0], r[1]
            if nid not in diary_entities[did]:
                diary_entities[did].append(nid)

        updated = 0
        for entities in diary_entities.values():
            if len(entities) < 2:
                continue
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    a, b = entities[i], entities[j]
                    if a == b:
                        continue
                    src, tgt = (a, b) if a < b else (b, a)
                    try:
                        existing = await self.graph_store.fetchone(
                            "SELECT weight FROM edges "
                            "WHERE from_node=? AND to_node=? AND relation_type='co_occur'",
                            (src, tgt),
                        )
                        if existing:
                            new_w = max(existing[0] or 1.0, float(sum(
                                1 for ents in diary_entities.values()
                                if a in ents and b in ents
                            )))
                            if new_w != existing[0]:
                                await self.graph_store.execute(
                                    "UPDATE edges SET weight=? WHERE from_node=? AND to_node=? AND relation_type='co_occur'",
                                    (new_w, src, tgt),
                                )
                                updated += 1
                    except Exception:
                        pass

        return updated

    # ── 辅助 ──────────────────────────────────────────

    @staticmethod
    def _canonicalize(value: str) -> str:
        """归一化：小写、去空格、统一字符"""
        v = value.lower().strip()
        v = re.sub(r'\s+', '_', v)
        v = v.replace('（', '(').replace('）', ')').replace('：', ':').replace('；', ';')
        return v[:80]
