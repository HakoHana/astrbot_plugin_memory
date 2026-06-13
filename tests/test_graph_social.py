"""社交关系边测试 — SocialEdge CRUD + 引擎逻辑"""

from __future__ import annotations

import pytest

from memori.models.graph_models import SocialEdge


class TestSocialEdgeModel:
    """SocialEdge 数据模型行为"""

    def test_edge_id_ordering(self):
        """edge_id 对 from_user / to_user 排序，确保双向一致性"""
        a = SocialEdge(from_user="alice", to_user="bob", relation_type="friend_of")
        b = SocialEdge(from_user="bob", to_user="alice", relation_type="friend_of")
        assert a.edge_id == b.edge_id
        assert "alice" in a.edge_id
        assert "bob" in a.edge_id

    def test_edge_id_custom(self):
        """自定义 id 优先于自动生成"""
        e = SocialEdge(from_user="a", to_user="b", relation_type="friend_of", id="custom_id")
        assert e.edge_id == "custom_id"

    def test_effective_weight_respects_cap(self):
        """effective_weight = min(weight, cap)"""
        e = SocialEdge(from_user="a", to_user="b", relation_type="friend_of",
                       weight=0.8, cap=0.4)
        assert e.effective_weight == 0.4

    def test_effective_weight_without_cap(self):
        """cap=1.0 时 effective_weight = weight"""
        e = SocialEdge(from_user="a", to_user="b", relation_type="friend_of",
                       weight=0.7, cap=1.0)
        assert e.effective_weight == 0.7

    def test_default_values(self):
        """默认 status=pending, weight=0.1, cap=0.4"""
        e = SocialEdge(from_user="a", to_user="b", relation_type="friend_of")
        assert e.status == "pending"
        assert e.weight == 0.1
        assert e.cap == 0.4
        assert e.source == "explicit_claim"


@pytest.mark.asyncio
class TestSocialGraphStore:
    """GraphStore 社交边 CRUD"""

    @pytest.fixture
    async def store(self):
        from memori.storage.graph_store import GraphStore
        s = GraphStore(db_path=":memory:")
        await s.initialize()
        return s

    async def test_upsert_and_query(self, store):
        e = SocialEdge(from_user="alice", to_user="bob", relation_type="friend_of")
        result = await store.upsert_social_edge(e)
        assert result is not None

        got = await store.get_social_edge("alice", "bob")
        assert got is not None
        assert got.from_user == "alice"
        assert got.status == "pending"

    async def test_query_neighbors(self, store):
        await store.upsert_social_edge(
            SocialEdge(from_user="alice", to_user="bob", relation_type="friend_of",
                       status="active", weight=0.8, cap=1.0)
        )
        await store.upsert_social_edge(
            SocialEdge(from_user="alice", to_user="carol", relation_type="friend_of",
                       status="pending", weight=0.1, cap=0.4)
        )
        neighbors = await store.query_social_neighbors("alice", min_weight=0.0)
        assert len(neighbors) == 2

        active = await store.query_social_neighbors("alice", status="active")
        assert len(active) == 1

    async def test_query_pending(self, store):
        await store.upsert_social_edge(
            SocialEdge(from_user="alice", to_user="bob", relation_type="friend_of")
        )
        pending = await store.query_pending_confirmations("bob")
        assert len(pending) == 1
        assert pending[0].from_user == "alice"

    async def test_update_weight_and_status(self, store):
        e = SocialEdge(from_user="a", to_user="b", relation_type="friend_of")
        await store.upsert_social_edge(e)
        await store.update_social_edge_weight(e.edge_id, 0.9)
        await store.set_social_edge_status(e.edge_id, "active")
        got = await store.get_social_edge("a", "b")
        assert got.weight == 0.9
        assert got.status == "active"


@pytest.mark.asyncio
class TestSocialGraphEngine:
    """GraphEngine 社交关系逻辑"""

    @pytest.fixture
    async def engine(self):
        from memori.storage.graph_store import GraphStore
        from memori.features.graph_engine import GraphEngine

        store = GraphStore(db_path=":memory:")
        await store.initialize()
        return GraphEngine(graph_store=store, diary_store=None)

    async def test_claim_relationship(self, engine):
        result = await engine.claim_relationship("alice", "bob", "friend_of")
        assert result is not None
        assert result.status == "pending"
        assert result.weight == 0.1

    async def test_confirm_relationship(self, engine):
        await engine.claim_relationship("alice", "bob")
        result = await engine.confirm_relationship("bob", "alice")
        assert result is not None
        assert result.status == "active"
        assert result.effective_weight == 0.7

    async def test_block_user(self, engine):
        await engine.claim_relationship("alice", "bob")
        await engine.confirm_relationship("bob", "alice")
        await engine.block_user("alice", "bob")
        neighbors = await engine.get_neighbor_ids("alice")
        assert "bob" not in neighbors

    async def test_get_neighbor_ids_includes_self(self, engine):
        neighbors = await engine.get_neighbor_ids("alice")
        assert neighbors["alice"] == 1.0

    async def test_co_occur_relation(self, engine):
        result = await engine.form_relation_from_co_occur("alice", "bob")
        assert result is not None
        assert result.status == "passive"
        assert result.weight == 0.05

    async def test_co_occur_does_not_override_active(self, engine):
        await engine.claim_relationship("alice", "bob")
        await engine.confirm_relationship("bob", "alice")
        result = await engine.form_relation_from_co_occur("alice", "bob")
        assert result is None

    async def test_decay_social_edges(self, engine):
        await engine.claim_relationship("grace", "heidi", "friend_of")
        await engine.confirm_relationship("heidi", "grace")
        count = await engine.decay_social_edges(days_since_last_interaction=14)
        assert count > 0
        edge = await engine.graph_store.get_social_edge("grace", "heidi")
        assert edge.weight < 0.7
        assert edge.weight >= 0.02
