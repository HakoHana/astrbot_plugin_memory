"""EmbeddingProvider 接口 + LocalEmbeddingProvider 单元测试

注意：LocalEmbeddingProvider 需要 pip install 'memori[embedding]'，
若不满足则跳过该组测试。
"""

from __future__ import annotations

import pytest

from memori.core.adapters import EmbeddingProvider


class TestEmbeddingProviderInterface:
    """EmbeddingProvider ABC 契约验证"""

    def test_is_abstract(self):
        """EmbeddingProvider 是抽象类，不能直接实例化"""
        with pytest.raises(TypeError):
            EmbeddingProvider()  # type: ignore

    def test_has_abstract_embed(self):
        """embed 是抽象方法"""
        assert getattr(EmbeddingProvider.embed, "__isabstractmethod__", False)

    def test_has_abstract_dimension(self):
        """dimension 是抽象属性"""
        assert getattr(EmbeddingProvider.dimension, "__isabstractmethod__", False)

    def test_embed_batch_default_delegates(self):
        """默认 embed_batch 逐条调用 embed"""
        calls = []

        class TestProvider(EmbeddingProvider):
            async def embed(self, text: str) -> list[float]:
                calls.append(text)
                return [1.0, 2.0]

            @property
            def dimension(self) -> int:
                return 2

        import asyncio
        provider = TestProvider()
        results = asyncio.run(provider.embed_batch(["a", "b"]))
        assert len(results) == 2
        assert calls == ["a", "b"]

    def test_set_provider_noop_by_default(self):
        """set_provider 默认 no-op"""

        class TestProvider(EmbeddingProvider):
            async def embed(self, text: str) -> list[float]:
                return []

            @property
            def dimension(self) -> int:
                return 0

        p = TestProvider()
        p.set_provider("foo")  # 不应抛异常
        p.set_provider(None)  # 不应抛异常


@pytest.mark.skipif(
    True,  # 由 conftest 中的 import 判断
    reason="需要安装 sentence-transformers（pip install 'memori[embedding]'）",
)
class TestLocalEmbeddingProvider:
    """LocalEmbeddingProvider 集成测试（需 sentence-transformers 依赖）"""

    @pytest.fixture
    def provider(self):
        from memori.core.embed_providers import LocalEmbeddingProvider

        return LocalEmbeddingProvider("all-MiniLM-L6-v2")

    async def test_embed_returns_384_dims(self, provider):
        vec = await provider.embed("测试文本")
        assert len(vec) == 384
        assert all(isinstance(v, float) for v in vec)

    async def test_embed_batch(self, provider):
        vecs = await provider.embed_batch(["你好", "世界"])
        assert len(vecs) == 2
        assert len(vecs[0]) == 384

    async def test_embed_similarity(self, provider):
        """语义相似的句子应产生更接近的向量"""
        vec_a = await provider.embed("今天天气很好")
        vec_b = await provider.embed("今天天气不错")
        vec_c = await provider.embed("苹果是一种水果")

        import math

        def cos_sim(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            return dot / (na * nb) if na * nb > 0 else 0

        sim_ab = cos_sim(vec_a, vec_b)
        sim_ac = cos_sim(vec_a, vec_c)
        assert sim_ab > sim_ac, "相似句子的相似度应高于不相似句子"

    def test_dimension_property(self, provider):
        assert provider.dimension == 384

    def test_model_name(self, provider):
        assert "MiniLM" in provider.model_name

    async def test_empty_batch(self, provider):
        results = await provider.embed_batch([])
        assert results == []


class TestMockEmbeddingProvider:
    """用 Mock 验证 EmbeddingProvider 在检索链中的行为"""

    @pytest.fixture
    def mock_provider(self):
        """返回固定维度的 mock provider"""

        class FixedEmbedder(EmbeddingProvider):
            async def embed(self, text: str) -> list[float]:
                # 返回固定向量，模拟 "dim=4"
                return [0.1, 0.2, 0.3, 0.4]

            async def embed_batch(self, texts: list[str]) -> list[list[float]]:
                return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

            @property
            def dimension(self) -> int:
                return 4

        return FixedEmbedder()

    async def test_fixed_embedder_returns_consistent(self, mock_provider):
        v1 = await mock_provider.embed("任何文本")
        v2 = await mock_provider.embed("不同文本")
        assert v1 == v2  # 固定返回
        assert len(v1) == 4

    async def test_batch_order_preserved(self, mock_provider):
        results = await mock_provider.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        for r in results:
            assert r == [0.1, 0.2, 0.3, 0.4]
