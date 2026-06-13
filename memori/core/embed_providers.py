"""内置 EmbeddingProvider 实现

使用 sentence-transformers 本地运行嵌入模型，零 API 费用，离线可用。
需安装 `pip install memori[embedding]` 或 `sentence-transformers>=3.0.0`。
"""

from __future__ import annotations

from .adapters import EmbeddingProvider


class LocalEmbeddingProvider(EmbeddingProvider):
    """基于 sentence-transformers 的本地嵌入模型（懒加载 + 异步初始化）

    模型在首次调用 embed() 时自动异步加载，
    不阻塞构造函数与事件循环，适合在 FastAPI 等异步场景中使用。

    用法:
        provider = LocalEmbeddingProvider("BAAI/bge-m3")  # 立即返回
        await provider.ensure_loaded()                     # 显式预加载（可选）
        vec = await provider.embed("你好")                  # 首次自动加载
    """

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self._model_name = model_name
        self._model = None
        self._dim = None
        self._loaded = False

    async def ensure_loaded(self):
        """异步初始化模型（首次调用 embed 时自动调用，也可手动预加载）"""
        if self._loaded:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "LocalEmbeddingProvider 需要 sentence-transformers。\n"
                "请运行: pip install 'memori[embedding]'"
            )
        # SentenceTransformer.__init__ 内部可能涉及网络 I/O（下载/校验），
        # 在线程池中运行以避免阻塞事件循环
        import asyncio
        loop = asyncio.get_event_loop()
        self._model = await loop.run_in_executor(
            None, lambda: SentenceTransformer(self._model_name)
        )
        self._dim = self._model.get_embedding_dimension()
        self._loaded = True

    async def embed(self, text: str) -> list[float]:
        await self.ensure_loaded()
        return self._model.encode(text).tolist()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        await self.ensure_loaded()
        return self._model.encode(texts).tolist()

    @property
    def dimension(self) -> int:
        if self._dim is None:
            return 0
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model_name
