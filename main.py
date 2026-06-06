"""AstrBot Memory Plugin — 日记式长期记忆插件"""

from __future__ import annotations

import asyncio

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger

from .core.memory_core import MemoryCore


@register(
    name="Memory",
    author="your_name",
    desc="日记式长期记忆插件 — 让 Bot 记住与用户的每一刻",
    version="0.1.0",
    repo="https://github.com/your_name/astrbot_plugin_memory",
)
class MemoryPlugin(Star):
    """记忆插件主入口"""

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.memory_core: MemoryCore | None = None

    async def initialize(self):
        data_dir = str(StarTools.get_data_dir())
        logger.info(f"[Memory] init: {data_dir}")
        self.memory_core = MemoryCore(
            plugin_context=self.context,
            data_dir=data_dir,
            config=self.config,
        )
        await self.memory_core.initialize()
        logger.info("[Memory] init done")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        try:
            with open("/tmp/md.log", "a") as f:
                f.write("on_llm_request|called\n")
        except:
            pass
        if not self.memory_core:
            return
        try:
            result = await self.memory_core.on_message(event)
            if result is not None:
                event.message_obj.message_str = result
        except Exception as e:
            logger.error(f"[Memory] on_llm_request err: {e}")

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        try:
            with open("/tmp/md.log", "a") as f:
                f.write("on_message|called\n")
        except:
            pass
        if not self.memory_core:
            return
        try:
            uid = self.memory_core.context_provider.get_user_id(event)
            txt = self.memory_core.context_provider.get_conversation_text(event)
            with open("/tmp/md.log", "a") as f:
                f.write(f"on_msg|uid={uid}|txt={txt[:40]}\n")
            if uid and txt:
                logger.info(f"[Memory] on_msg: {uid}")
                r = await self.memory_core.consolidation_manager.on_message(uid, txt)
                with open("/tmp/md.log", "a") as f:
                    f.write(f"consolidation|result={r}\n")
        except Exception as e:
            import traceback
            with open("/tmp/md.log", "a") as f:
                f.write(f"on_msg|EX={e}|{traceback.format_exc()[:200]}\n")
            logger.error(f"[Memory] on_msg err: {e}")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response: LLMResponse = None):
        try:
            with open("/tmp/md.log", "a") as f:
                f.write("on_llm_response|called\n")
        except:
            pass
        if not self.memory_core:
            return
        try:
            user_id = self.memory_core.context_provider.get_user_id(event)
            text = self.memory_core.context_provider.get_conversation_text(event)
            if user_id and text:
                logger.info(f"[Memory] on_resp: {user_id}")
                asyncio.ensure_future(
                    self.memory_core.consolidation_manager.on_message(user_id, text)
                )
        except Exception as e:
            logger.error(f"[Memory] on_resp err: {e}")

    async def on_unload(self):
        if self.memory_core:
            await self.memory_core.destroy()
            logger.info("[Memory] unloaded")
