"""WebUI Dashboard API — 向后兼容适配层

新代码应直接导入 PageService 和 PageRoute：
  from .page_service import PageService
  from .page_route import PageRoute

此类仅为旧导入路径 from .page_api import PageApi 提供兼容。
"""

from __future__ import annotations

from ..core.logger import logger
from .page_service import PageService
from .page_route import PageRoute


class PageApi:
    """向后兼容包装 — 继承旧接口行为"""

    def __init__(self, memory_core):
        self._service = PageService(memory_core)
        self._route = PageRoute(self._service)

    def register_routes(self, register):
        self._route.register_routes(register)

    @property
    def core(self):
        return self._service.core

    # ── 委托 PageService 的公开方法（若外部有直接调用） ──

    async def get_stats(self):
        return await self._service.get_stats()

    async def get_graph_overview(self):
        return await self._service.get_graph_overview()

    async def query_graph(self):
        return await self._route.query_graph()

    async def list_memories(self):
        return await self._route.list_memories()

    async def get_memory_detail(self):
        return await self._route.get_memory_detail()

    async def update_memory(self):
        return await self._route.update_memory()

    async def delete_memory(self):
        return await self._route.delete_memory()

    async def batch_delete_memories(self):
        return await self._route.batch_delete_memories()

    async def update_diary_status(self):
        return await self._route.update_diary_status()

    async def get_timeline(self):
        return await self._route.get_timeline()

    async def get_day_detail(self):
        return await self._route.get_day_detail()

    async def get_diary(self):
        return await self._route.get_diary()

    async def update_diary(self):
        return await self._route.update_diary()

    async def get_persona(self):
        return await self._route.get_persona()

    async def update_persona(self):
        return await self._route.update_persona()

    async def list_users(self):
        return await self._route.list_users()

    async def get_user_detail(self):
        return await self._route.get_user_detail()

    async def list_archived(self):
        return await self._route.list_archived()

    async def restore_archived(self):
        return await self._route.restore_archived()
