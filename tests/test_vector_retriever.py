"""AtomStore 向量搜索 + VectorRetriever 单元测试

使用真实的 SQLite :memory: 数据库验证：
1. update_embedding 写入正确
2. search_vector 余弦相似度排序
3. _row_to_atom 反序列化 embedding
4. MemoryUnitOfWork 门面代理
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from memori.models.memory_atom import MemoryAtom, AtomType
from memori.storage.atom_store import AtomStore
from memori.pipeline.memory_uow import MemoryUnitOfWork


class TestAtomStoreEmbedding:
    """AtomStore update_embedding + search_vector 测试"""

    DB_PATH = ":memory:"

    @pytest.fixture
    async def store(self):
        s = AtomStore(self.DB_PATH)
        await s.initialize()
        # 插入测试原子
        atom = MemoryAtom(
            user_id="u1",
            diary_date="2026-06-11",
            content="今天天气很好，适合出去散步",
            atom_type=AtomType.EPISODIC,
            importance=0.8,
        )
        atom.prepare_insert()
        aid = await s.insert(atom)
        atom.atom_id = aid

        atom2 = MemoryAtom(
            user_id="u1",
            diary_date="2026-06-10",
            content="用户喜欢吃苹果和香蕉",
            atom_type=AtomType.PREFERENCE,
            importance=0.6,
        )
        atom2.prepare_insert()
        aid2 = await s.insert(atom2)
        atom2.atom_id = aid2

        atom3 = MemoryAtom(
            user_id="u1",
            diary_date="2026-06-09",
            content="张三约了明天下午开会",
            atom_type=AtomType.PLANNED,
            importance=0.9,
        )
        atom3.prepare_insert()
        aid3 = await s.insert(atom3)
        atom3.atom_id = aid3

        yield s
        await s.close()

    @pytest.fixture
    async def store_with_embeddings(self, store):
        """插入 embedding 后的 store"""
        # 手动写入 embedding
        await store.update_embedding(1, [0.1, 0.2, 0.3], "test_model")
        await store.update_embedding(2, [0.4, 0.5, 0.6], "test_model")
        await store.update_embedding(3, [0.7, 0.8, 0.9], "test_model")
        return store

    async def test_update_embedding(self, store):
        """验证 embedding 写入后再读取"""
        await store.update_embedding(1, [0.1, 0.2, 0.3], "test_model")
        row = await store.fetchone(
            "SELECT embedding, embedding_model FROM memory_atoms WHERE id=1"
        )
        assert row is not None
        stored_bytes = row[0]
        assert stored_bytes is not None
        decoded = json.loads(stored_bytes.decode("utf-8"))
        assert decoded == [0.1, 0.2, 0.3]
        assert row[1] == "test_model"

    async def test_row_to_atom_deserializes_embedding(self, store):
        """_row_to_atom 应正确反序列化 embedding"""
        await store.update_embedding(1, [0.5, 0.6, 0.7], "m")
        rows = await store.fetch("SELECT * FROM memory_atoms WHERE id=1")
        assert len(rows) == 1
        atom = store._row_to_atom(rows[0])
        assert atom.embedding is not None
        assert len(atom.embedding) == 3
        assert atom.embedding[0] == 0.5

    async def test_search_vector_returns_sorted(self, store_with_embeddings):
        """查询向量与 [0.7, 0.8, 0.9] 最相似 → atom_id=3 排第一"""
        query = [0.71, 0.81, 0.89]
        results = await store_with_embeddings.search_vector(query, "u1", k=3)
        assert len(results) >= 1
        assert results[0].atom_id == 3  # 最相似

    async def test_search_vector_empty_query(self, store_with_embeddings):
        results = await store_with_embeddings.search_vector([], "u1", k=5)
        assert results == []

    async def test_search_vector_model_filter(self, store_with_embeddings):
        """指定 model_name 过滤"""
        # 无匹配模型 → 空结果
        results = await store_with_embeddings.search_vector(
            [0.1, 0.2, 0.3], "u1", k=5, model_name="nonexistent"
        )
        assert results == []

    async def test_search_vector_wrong_dimension(self, store):
        """维度不匹配的向量应被跳过"""
        await store.update_embedding(1, [0.1, 0.2, 0.3], "m")
        await store.update_embedding(2, [0.4, 0.5, 0.6], "m")
        # 查询维度 = 4，存储维度 = 3 → 全部跳过
        results = await store.search_vector([0.1, 0.2, 0.3, 0.4], "u1", k=5)
        assert results == []


class TestMemoryUnitOfWorkEmbedding:
    """MemoryUnitOfWork 代理 update_embedding"""

    @pytest.fixture
    def mock_atom(self):
        atom = MagicMock()
        atom.update_embedding = AsyncMock()
        return atom

    @pytest.fixture
    def uow(self, mock_atom):
        return MemoryUnitOfWork(
            diary_store=MagicMock(),
            atom_store=mock_atom,
            write_op_log=None,
        )

    async def test_update_embedding_delegates(self, uow, mock_atom):
        await uow.update_embedding(42, [0.1, 0.2], "test_model")
        mock_atom.update_embedding.assert_awaited_once_with(42, [0.1, 0.2], "test_model")


class TestVectorRetrieverMock:
    """基于 mock 的 VectorRetriever 验证（需要 retrieval.vector_retriever，Phase 2）"""

    @pytest.fixture
    def mock_provider(self):
        from memori.core.adapters import EmbeddingProvider

        class MockEmbedder(EmbeddingProvider):
            async def embed(self, text: str) -> list[float]:
                return [0.1, 0.2, 0.3]

            @property
            def dimension(self) -> int:
                return 3

        return MockEmbedder()

    @pytest.fixture
    def mock_store(self):
        store = MagicMock()
        store.search_vector = AsyncMock(return_value=[
            MemoryAtom(user_id="u1", diary_date="d", content="结果A", atom_id=1),
            MemoryAtom(user_id="u1", diary_date="d", content="结果B", atom_id=2),
        ])
        return store

    async def test_retrieve_calls_search_vector(
        self, mock_provider, mock_store
    ):
        from memori.retrieval.vector_retriever import VectorRetriever

        vr = VectorRetriever(atom_store=mock_store, embed_provider=mock_provider)
        results = await vr.retrieve(["天气", "好"], ["u1"], k=3)
        assert len(results) == 2
        mock_store.search_vector.assert_awaited_once()

    async def test_no_provider_returns_empty(self, mock_store):
        from memori.retrieval.vector_retriever import VectorRetriever

        vr = VectorRetriever(atom_store=mock_store, embed_provider=None)
        results = await vr.retrieve(["天气"], ["u1"], k=3)
        assert results == []
        mock_store.search_vector.assert_not_called()

    async def test_empty_keywords_returns_empty(
        self, mock_provider, mock_store
    ):
        from memori.retrieval.vector_retriever import VectorRetriever

        vr = VectorRetriever(atom_store=mock_store, embed_provider=mock_provider)
        results = await vr.retrieve([], ["u1"], k=3)
        assert results == []
