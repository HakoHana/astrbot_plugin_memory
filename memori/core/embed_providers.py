"""内置 EmbeddingProvider 实现

使用 sentence-transformers 本地运行嵌入模型，零 API 费用，离线可用。
需安装 `pip install memori[embedding]` 或 `sentence-transformers>=3.0.0`。
"""

from __future__ import annotations

from .adapters import EmbeddingProvider


class LocalEmbeddingProvider(EmbeddingProvider):
    """基于 sentence-transformers 的本地嵌入模型

    用法:
        provider = LocalEmbeddingProvider("all-MiniLM-L6-v2")
        vec = await provider.embed("你好")
        print(provider.dimension)  # 384
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "LocalEmbeddingProvider 需要 sentence-transformers。\n"
                "请运行: pip install 'memori[embedding]'"
            )
        self._model = SentenceTransformer(model_name)
        self._model_name = model_name
        self._dim = self._model.get_sentence_embedding_dimension()

    async def embed(self, text: str) -> list[float]:
        """单条文本嵌入"""
        return self._model.encode(text).tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量文本嵌入（sentence-transformers 原生支持 batch）"""
        if not texts:
            return []
        return self._model.encode(texts).tolist()

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name
