"""AstrBot Memory Plugin — 轻量适配层，核心逻辑委托 memoria"""

from __future__ import annotations

import asyncio

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger

from memoria import MemoryCore
from .adapters import AstrBotLLMProvider, AstrBotContextProvider
from .memory_tools import RecallMemoryTool, MemorizeMemoryTool


@register(
    name="Memory",
    author="HakoHana",
    desc="日记式长期记忆插件 — 让 Bot 记住与用户的每一刻",
    version="0.4.0",
    repo="https://github.com/HakoHana/astrbot_plugin_memory",
    tags=["memory", "diary", "long-term"],
)
class MemoryPlugin(Star):
    """记忆插件 — 适配层：将 AstrBot 事件翻译为 memoria 调用"""

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self.core: MemoryCore | None = None

    async def initialize(self):
        data_dir = str(StarTools.get_data_dir())
        logger.info(f"[Memory] 初始化 memoria 内核: {data_dir}")

        # 创建 AstrBot 适配器
        llm_provider = AstrBotLLMProvider(self.context)
        context_provider = AstrBotContextProvider()

        # 创建 memoria 内核
        self.core = MemoryCore(
            config=self.config,
            llm_provider=llm_provider,
            context_provider=context_provider,
            data_dir=data_dir,
            reply_handler=self._reply_to_user,
        )
        await self.core.initialize()

        # 注册 Agent Tools
        try:
            recall_tool = RecallMemoryTool()
            recall_tool.set_memory_core(self.core)
            memorize_tool = MemorizeMemoryTool()
            memorize_tool.set_memory_core(self.core)
            self.context.add_llm_tools(recall_tool, memorize_tool)
            self.context.activate_llm_tool("recall_long_term_memory")
            self.context.activate_llm_tool("memorize_long_term_memory")
            logger.info("[Memory] Agent Tools 已注册")
        except Exception as e:
            logger.warning(f"[Memory] 注册 Agent Tools 失败: {e}")

        # 注册 WebUI API
        try:
            from .page_api import PageApi
            from .webui_routes import register_webui_routes
            page_api = PageApi(self.core)
            register_webui_routes(self.context, page_api)
            self.core.page_api = page_api
        except Exception as e:
            logger.warning(f"[Memory] 注册 WebUI API 失败: {e}")

        logger.info("[Memory] memoria 内核初始化完成")

    def _reply_to_user(self, user_id: str, message: str) -> None:
        """回复用户（由 memoria 回调）"""
        try:
            if hasattr(self.context, "reply"):
                # AstrBot 上下文回复
                asyncio.ensure_future(self.context.reply(message))
        except Exception as e:
            logger.warning(f"[Memory] 回复用户失败: {e}")

    def _get_sender_name(self, event) -> str:
        """从事件提取发送者显示名"""
        try:
            if hasattr(event, "get_sender_name"):
                name = event.get_sender_name()
                if name: return str(name)
            if hasattr(event, "sender_name"):
                name = event.sender_name
                if name: return str(name)
            if hasattr(event, "message_obj") and event.message_obj:
                sender = getattr(event.message_obj, "sender", None)
                if sender:
                    for attr in ("card", "nickname", "name", "user_displayname"):
                        val = getattr(sender, attr, None)
                        if val: return str(val)
        except Exception:
            pass
        return ""

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.core:
            return

        raw_text = event.get_message_str() if hasattr(event, 'get_message_str') else str(event.message_str)
        if raw_text.startswith("/"):
            if hasattr(req, 'prompt'):
                req.prompt = None
            if req.contexts:
                req.contexts.clear()
            event.message_str = ""
            if hasattr(event, 'message_obj') and event.message_obj:
                event.message_obj.message_str = ""
            logger.debug(f"[Memory] on_llm_request: cmd={raw_text[:30]}, 跳过 LLM")
            return

        # 注册/更新用户名
        uid = self.core.context_provider.get_user_id(event)
        sender_name = self._get_sender_name(event)
        if self.core.atom_store and uid:
            try:
                await self.core.atom_store.ensure_user(uid, sender_name)
            except Exception:
                pass
            try:
                await self.core.atom_store.ensure_canonical_user(
                    f"qq:{uid}", sender_name, "qq"
                )
            except Exception:
                pass

        # 存储用户消息到会话
        cs = self.core.conversation_store
        if cs and raw_text:
            sid = await cs.get_session_id(event)
            await cs.add_message(sid, uid, "user", raw_text, sender_name)

        # 记忆注入
        result = await self.core.on_message(event, sender_name)
        if result is not None:
            event.message_obj.message_str = result

        # 同步 event.system_prompt → req.system_prompt
        if hasattr(event, 'system_prompt') and event.system_prompt and req and event.system_prompt != req.system_prompt:
            req.system_prompt = event.system_prompt

        # 激活记忆工具
        try:
            tmgr = self.context.get_llm_tool_manager()
            for tool_name in ["recall_long_term_memory", "memorize_long_term_memory"]:
                tool = tmgr.get_func(tool_name)
                if tool:
                    tool.active = True
                    if req and req.func_tool and tool_name not in req.func_tool.names():
                        req.func_tool.add_tool(tool)
        except Exception as e:
            logger.warning(f"[Memory] 激活工具诊断异常: {e}")

        # 更新用户等级
        try:
            await self.core._maybe_update_tier(uid)
        except Exception:
            pass

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        if not self.core:
            return
        try:
            uid = self.core.context_provider.get_user_id(event)
            txt = self.core.context_provider.get_conversation_text(event)
            sender_name = self._get_sender_name(event)

            # 预过滤
            if txt and not txt.startswith("/") and uid and self.config.get("pre_filter_enabled", False):
                try:
                    if await self.core.should_ignore(uid, txt):
                        event.message_str = ""
                        if hasattr(event, 'message_obj') and event.message_obj:
                            event.message_obj.message_str = ""
                        return
                except Exception:
                    pass

            # 指令处理
            if txt and txt.startswith("/"):
                event.message_str = ""
                if hasattr(event, 'message_obj') and event.message_obj:
                    event.message_obj.message_str = ""
                if txt.strip().startswith("/记忆重构"):
                    from astrbot.core.message.message_event_result import MessageChain
                    parts = txt.strip().split(maxsplit=1)
                    args = parts[1:] if len(parts) > 1 else []
                    chain = MessageChain().message("🔄 正在逐条重构旧记忆，请稍候...")
                    await event.send(chain)
                    result = await self.core.command_handler.handle_rebuild(uid, args)
                    chain2 = MessageChain().message(result)
                    await event.send(chain2)
                else:
                    await self.core._handle_command(uid, txt)
                return

            if uid and txt:
                logger.debug(f"[Memory] on_message: {uid}")
                # 完整对话上下文
                full_ctx = txt
                cs = self.core.conversation_store
                if cs:
                    try:
                        sid = await cs.get_session_id(event)
                        bot_name = self.config.get("bot_name", "Hana")
                        full_ctx = await cs.get_recent_context(sid, limit=10, bot_name=bot_name)
                    except Exception:
                        pass
                task = asyncio.ensure_future(
                    self.core.consolidation_manager.on_message(uid, full_ctx, sender_name)
                )
                self.core._background_tasks.add(task)
                task.add_done_callback(self.core._background_tasks.discard)
        except Exception as e:
            logger.error(f"[Memory] on_message 出错: {e}")

    @filter.on_llm_response()
    async def on_llm_response(self, event: AstrMessageEvent, response: LLMResponse = None):
        if not self.core:
            return
        try:
            cs = self.core.conversation_store
            if cs and response:
                sid = await cs.get_session_id(event)
                uid = self.core.context_provider.get_user_id(event)
                resp_text = ""
                if hasattr(response, "result_chain") and response.result_chain:
                    resp_text = response.result_chain.get_plain_text() or ""
                if resp_text:
                    bot_name = getattr(event, "bot_name", "") or self.config.get("bot_name", "Hana")
                    await cs.add_message(sid, uid, "assistant", resp_text, bot_name)
        except Exception as e:
            logger.error(f"[Memory] on_response 出错: {e}")

    async def on_unload(self):
        if self.core:
            await self.core.destroy()
            try:
                from .storage.base_store import BaseDbStore
                BaseDbStore.close_all_sync()
            except Exception:
                pass
            logger.info("[Memory] memoria 内核已关闭")
