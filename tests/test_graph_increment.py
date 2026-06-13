"""GraphStore increment_edge_weight 测试 — 统一写入语义"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestGraphIncrementEdgeWeight:
    """increment_edge_weight 统一写入入口"""

    @pytest.fixture
    async def store(self):
        from memori.storage.graph_store import GraphStore
        s = GraphStore(db_path=":memory:")
        await s.initialize()
        return s

    async def test_insert_creates_edge_with_weight(self, store):
        """首次调用插入边，weight = amount"""
        w = await store.increment_edge_weight("entity:a", "entity:b", "co_occur", amount=1.0)
        assert w == 1.0

        row = await store.fetchone(
            "SELECT weight FROM edges WHERE from_node=? AND to_node=? AND relation_type=?",
            ("entity:a", "entity:b", "co_occur"),
        )
        assert row is not None
        assert row[0] == 1.0

    async def test_increment_adds_to_existing(self, store):
        """第二次调用累加权重"""
        w1 = await store.increment_edge_weight("entity:x", "entity:y", "co_occur", amount=1.0)
        assert w1 == 1.0

        w2 = await store.increment_edge_weight("entity:x", "entity:y", "co_occur", amount=1.0)
        assert w2 == 2.0

        w3 = await store.increment_edge_weight("entity:x", "entity:y", "co_occur", amount=3.0)
        assert w3 == 5.0

    async def test_order_independent(self, store):
        """(a,b) 和 (b,a) 走同一行（内部排序）"""
        w1 = await store.increment_edge_weight("entity:a", "entity:b", "co_occur", amount=1.0)
        w2 = await store.increment_edge_weight("entity:b", "entity:a", "co_occur", amount=2.0)
        assert w2 == 3.0

        row = await store.fetchone(
            "SELECT from_node, to_node, weight FROM edges WHERE relation_type='co_occur'"
        )
        assert row is not None
        # from_node < to_node (sorted)
        assert row[0] == "entity:a"
        assert row[1] == "entity:b"
        assert row[2] == 3.0

    async def test_different_types_independent(self, store):
        """不同类型的边互不影响"""
        w1 = await store.increment_edge_weight("entity:a", "entity:b", "co_occur", amount=1.0)
        w2 = await store.increment_edge_weight("entity:a", "entity:b", "mentions", amount=5.0)
        assert w1 == 1.0
        assert w2 == 5.0

        # co_occur 不受 mentions 影响
        w3 = await store.increment_edge_weight("entity:a", "entity:b", "co_occur", amount=1.0)
        assert w3 == 2.0

    async def test_sequential_increments(self, store):
        """顺序调用累积正确（避免 SQLite 连接池并发限制）"""
        for _ in range(10):
            await store.increment_edge_weight("entity:a", "entity:b", "co_occur", amount=1.0)

        row = await store.fetchone(
            "SELECT weight FROM edges WHERE from_node=? AND to_node=? AND relation_type='co_occur'",
            ("entity:a", "entity:b"),
        )
        assert row is not None
        assert row[0] == 10.0, f"顺序累加后应为 10.0，实际 {row[0]}"

    async def test_zero_amount(self, store):
        """amount=0 插入一条 weight=0 的边"""
        w = await store.increment_edge_weight("entity:a", "entity:b", "co_occur", amount=0.0)
        assert w == 0.0

        row = await store.fetchone(
            "SELECT weight FROM edges WHERE from_node=? AND to_node=?",
            ("entity:a", "entity:b"),
        )
        assert row is not None
        assert row[0] == 0.0

    async def test_negative_amount(self, store):
        """amount 为负值（用于衰减/纠偏）"""
        w1 = await store.increment_edge_weight("entity:a", "entity:b", "co_occur", amount=5.0)
        assert w1 == 5.0

        w2 = await store.increment_edge_weight("entity:a", "entity:b", "co_occur", amount=-2.0)
        assert w2 == 3.0

    async def test_large_amount(self, store):
        """大 amount 正常工作"""
        w = await store.increment_edge_weight("entity:a", "entity:b", "co_occur", amount=999.0)
        assert w == 999.0
