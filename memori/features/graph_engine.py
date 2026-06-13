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

import json
import re
from collections import defaultdict
from typing import Any

import logging
logger = logging.getLogger("memori")

from ..models.graph_models import GraphNode, SocialEdge
from ..storage.graph_store import GraphStore
from ..core.adapters import CoreUserIdentityResolver
from ..storage.diary_store import DiaryStore
from ..core.interfaces import IGraphEngine, IUserIdentityResolver


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
        diary_store: DiaryStore,
        config: dict[str, Any] | None = None,
        embed_provider=None,
        user_identity_resolver: IUserIdentityResolver | None = None,
    ):
        self.graph_store = graph_store
        self.diary_store = diary_store
        self.config = config or {}
        self.embed_provider = embed_provider
        self._identity = user_identity_resolver

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
            if self._identity:
                try:
                    uid = await self._identity.resolve_display_name(name)
                    if uid:
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
        """增量更新 entity 间的 co_occur 边"""
        for i in range(len(entity_ids)):
            for j in range(i + 1, len(entity_ids)):
                a, b = entity_ids[i], entity_ids[j]
                if a == b:
                    continue
                src, tgt = (a, b) if a < b else (b, a)
                await self.graph_store.increment_edge_weight(
                    from_node=src, to_node=tgt,
                    relation_type="co_occur",
                    amount=1.0,
                    diary_id=diary_id,
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
        seen_pairs: set[tuple[str, str]] = set()
        for entities in diary_entities.values():
            if len(entities) < 2:
                continue
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    a, b = entities[i], entities[j]
                    if a == b:
                        continue
                    src, tgt = (a, b) if a < b else (b, a)
                    pair = (src, tgt)
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    try:
                        new_w = await self.graph_store.increment_edge_weight(
                            from_node=src, to_node=tgt,
                            relation_type="co_occur",
                            amount=1.0,
                        )
                        updated += 1
                    except Exception:
                        pass

        return updated

    # ── 社交关系管理 ────────────────────────────────────

    async def claim_relationship(
        self, uid: str, target_uid: str, relation_type: str = "friend_of"
    ) -> SocialEdge | None:
        """A 声称与 B 的关系，创建 pending 边

        流程：
        1. 检查是否已存在 blocked_by 边（被对方屏蔽则拒绝）
        2. 检查是否已存在反向关系
        3. 创建/更新 pending 边
        4. 发送通知
        """
        # 被对方屏蔽了？
        blocked = await self.graph_store.get_social_edge(
            target_uid, uid, relation_type="blocked_by"
        )
        if blocked and blocked.status == "active":
            logger.info(f"[memori] {uid} 已被 {target_uid} 屏蔽，拒绝声称关系")
            return None

        # 确保双方 user 节点存在（edges 不指向空节点）
        for uid_to_ensure in (uid, target_uid):
            user_key = f"user:{uid_to_ensure}"
            existing = await self.graph_store.fetchone(
                "SELECT id FROM nodes WHERE id=?", (user_key,)
            )
            if not existing:
                await self.graph_store.upsert_nodes([
                    GraphNode(
                        node_type="user",
                        value=uid_to_ensure[:20],
                        canonical_value=uid_to_ensure[:80],
                        metadata={"source": "social_claim"},
                    )
                ])

        edge = SocialEdge(
            from_user=uid,
            to_user=target_uid,
            relation_type=relation_type,
            status="pending",
            weight=0.1,
            cap=0.4,
            source="explicit_claim",
        )
        result = await self.graph_store.upsert_social_edge(edge)
        if result:
            logger.info(f"[memori] {uid} 声称与 {target_uid} 为 {relation_type}")
            await self._notify(target_uid, f"{uid} 声称与你为 {relation_type}，请确认")
        return result

    async def confirm_relationship(self, uid: str, claimer_uid: str) -> SocialEdge | None:
        """B 确认 A 的关系声称

        pending → active, weight 升到 0.7, 移除 cap
        """
        edge = await self.graph_store.get_social_edge(claimer_uid, uid)
        if not edge or edge.status != "pending":
            return None

        edge.status = "active"
        edge.weight = 0.7
        edge.cap = 1.0
        edge.source = "mutual_confirmation"

        await self.graph_store.set_social_edge_status(edge.edge_id, "active")
        await self.graph_store.update_social_edge_weight(edge.edge_id, 0.7)

        # 写入 metadata 更新 cap 和 source
        meta = json.dumps({"cap": 1.0, "source": "mutual_confirmation",
                           "from_user": edge.from_user, "to_user": edge.to_user})
        await self.graph_store.execute(
            "UPDATE edges SET metadata = ?, updated_at = ? WHERE id = ?",
            (meta, self.graph_store._now_ts(), edge.edge_id),
        )

        logger.info(f"[memori] {uid} 确认了 {claimer_uid} 的 {edge.relation_type} 关系")
        await self._notify(claimer_uid, f"{uid} 确认了你们之间的 {edge.relation_type} 关系")
        return edge

    async def reject_relationship(self, uid: str, claimer_uid: str) -> bool:
        """B 拒绝 A 的关系声称 → 删除边"""
        edge = await self.graph_store.get_social_edge(claimer_uid, uid)
        if not edge or edge.status != "pending":
            return False
        await self.graph_store.set_social_edge_status(edge.edge_id, "rejected")
        logger.info(f"[memori] {uid} 拒绝了 {claimer_uid} 的 {edge.relation_type} 声称")
        return True

    async def block_user(self, uid: str, target_uid: str) -> bool:
        """屏蔽用户 → 插入 blocked_by 边，清除已有关系"""
        # 先清除双方之间的所有社交边
        for rel_type in ("friend_of", "family_of", "trusted_by"):
            edge = await self.graph_store.get_social_edge(uid, target_uid, rel_type)
            if edge:
                await self.graph_store.set_social_edge_status(edge.edge_id, "blocked")

        # 创建屏蔽边
        block_edge = SocialEdge(
            from_user=uid,
            to_user=target_uid,
            relation_type="blocked_by",
            status="active",
            weight=1.0,
            cap=1.0,
            source="explicit_claim",
        )
        result = await self.graph_store.upsert_social_edge(block_edge)
        return result is not None

    async def form_relation_from_co_occur(
        self, uid_a: str, uid_b: str
    ) -> SocialEdge | None:
        """从两次共现推断被动关系（weight 0.05, status=passive）

        当 A 和 B 在群聊中同时出现时调用，不通知。
        """
        # 已存在主动关系则不覆盖
        existing = await self.graph_store.get_social_edge(uid_a, uid_b)
        if existing and existing.status in ("active", "pending"):
            return None
        existing_rev = await self.graph_store.get_social_edge(uid_b, uid_a)
        if existing_rev and existing_rev.status in ("active", "pending"):
            return None

        edge = SocialEdge(
            from_user=uid_a,
            to_user=uid_b,
            relation_type="friend_of",
            status="passive",
            weight=0.05,
            cap=0.4,
            source="co_occur",
        )
        result = await self.graph_store.upsert_social_edge(edge)
        return result

    async def get_neighbor_ids(self, user_id: str) -> dict[str, float]:
        """获取用户的所有可访问邻居 {neighbor_id: effective_weight}

        用于认证中间件，自动包含自己（weight=1.0）。
        排除 blocked_by 的发起方。
        """
        neighbors: dict[str, float] = {user_id: 1.0}

        # 查主动社交关系
        active_edges = await self.graph_store.query_social_neighbors(
            user_id,
            min_weight=0.0,
            status="active",
            relation_types=["friend_of", "family_of", "trusted_by"],
        )
        for edge in active_edges:
            neighbor = edge.to_user if edge.from_user == user_id else edge.from_user
            neighbors[neighbor] = max(neighbors.get(neighbor, 0), edge.effective_weight)

        # 查 pending 和 passive 的（低权限）
        pending_edges = await self.graph_store.query_social_neighbors(
            user_id,
            min_weight=0.0,
            relation_types=["friend_of", "family_of", "trusted_by"],
        )
        for edge in pending_edges:
            if edge.status in ("pending", "passive"):
                neighbor = edge.to_user if edge.from_user == user_id else edge.from_user
                current = neighbors.get(neighbor, 0)
                ew = edge.effective_weight
                if ew > current:
                    neighbors[neighbor] = ew

        # 排除已屏蔽我的
        blocked = await self.graph_store.query_social_neighbors(
            user_id,
            relation_types=["blocked_by"],
        )
        for edge in blocked:
            blocker = edge.to_user if edge.from_user == user_id else edge.from_user
            neighbors.pop(blocker, None)

        return neighbors

    async def get_persona_from_graph_node(self, display_name: str) -> dict | None:
        """从图谱 user 节点（display_name）查到用户画像

        桥接两个系统（通过 IUserIdentityResolver 解耦）：
          graph node "user:{display_name}"  →  resolver
                                         →  user_persona 画像

        Args:
            display_name: 图谱中的用户显示名（如 "hako"）

        Returns:
            画像摘要数据，或 None
        """
        if not display_name or not self._identity:
            return None
        try:
            return await self._identity.get_persona_full(display_name)
        except Exception:
            return None

    async def decay_social_edges(self, days_since_last_interaction: int = 7) -> int:
        """社交边权重衰减

        衰减因子：
        - active 边：每 7 天无互动 weight × 0.95
        - pending 边：每 7 天 × 0.85（更快衰减）
        - weight < 0.02 的 pending 边自动清理

        Args:
            days_since_last_interaction: 距上次互动的天数

        Returns:
            受影响/清理的边数
        """
        if days_since_last_interaction < 1:
            return 0

        updated = 0
        rate_active = 0.95 ** days_since_last_interaction
        rate_pending = 0.85 ** days_since_last_interaction

        # active 边衰减
        try:
            active_rows = await self.graph_store.fetch(
                "SELECT id, weight FROM edges "
                "WHERE relation_type IN ('friend_of','family_of','trusted_by') "
                "  AND status = 'active'",
            )
            for row in active_rows:
                eid, w = row
                new_w = w * rate_active
                if new_w < 0.02:
                    new_w = 0.02  # 留底线不完全消失
                await self.graph_store.update_social_edge_weight(eid, new_w)
                updated += 1
        except Exception:
            pass

        # pending 边衰减 + 低值清理
        try:
            pending_rows = await self.graph_store.fetch(
                "SELECT id, weight FROM edges "
                "WHERE relation_type IN ('friend_of','family_of','trusted_by') "
                "  AND status = 'pending'",
            )
            for row in pending_rows:
                eid, w = row
                new_w = w * rate_pending
                if new_w < 0.02:
                    await self.graph_store.set_social_edge_status(eid, "rejected")
                else:
                    await self.graph_store.update_social_edge_weight(eid, new_w)
                updated += 1
        except Exception:
            pass

        return updated

    # ── 通知钩子 ────────────────────────────────────────

    async def _notify(self, target_uid: str, message: str):
        """通知用户（Phase 1: 写日志，后续接入 AstrBot 消息推送）"""
        logger.info(f"[memori] 通知 {target_uid}: {message}")
        # TODO: 接入 AstrBot 消息接口
        # if hasattr(self, '_astrbot_context'):
        #     await self._astrbot_context.send_message(target_uid, message)

    # ── 辅助 ──────────────────────────────────────────

    @staticmethod
    def _canonicalize(value: str) -> str:
        """归一化：小写、去空格、统一字符"""
        v = value.lower().strip()
        v = re.sub(r'\s+', '_', v)
        v = v.replace('（', '(').replace('）', ')').replace('：', ':').replace('；', ';')
        return v[:80]
