"""抽象层 — 隔离核心逻辑和 AstrBot 运行时"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class LLMProvider(ABC):
    """LLM 调用抽象"""

    @abstractmethod
    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM，返回回复文本"""
        ...


class MemoryStore(ABC):
    """存储抽象 — 目前由 AtomStore 实现"""

    @abstractmethod
    async def search_fts(self, query: str, user_id: str, k: int) -> list:
        """FTS 全文搜索"""
        ...

    # 预留向量搜索接口
    async def search_vector(self, query: str, user_id: str, k: int) -> list:
        """向量语义搜索（预留）"""
        raise NotImplementedError


class ContextProvider(ABC):
    """AstrBot 事件上下文抽象"""

    @abstractmethod
    def get_user_id(self, event) -> str:
        """从事件中提取用户 ID"""
        ...

    @abstractmethod
    def get_conversation_text(self, event) -> str:
        """获取对话文本"""
        ...


# ── AstrBot 实现 ──

class AstrBotLLMProvider(LLMProvider):
    """封装 AstrBot 的 LLM Provider 调用"""

    def __init__(self, context):
        self.context = context
        self._provider = None
        self._provider_id = None

    def set_provider(self, provider_id: str | None):
        """指定使用哪个 Provider。None = 使用 AstrBot 默认"""
        self._provider_id = provider_id
        self._provider = None  # 触发重新获取

    def _get_provider(self):
        """获取当前有效的 Provider 实例"""
        if self._provider:
            return self._provider

        if self._provider_id:
            try:
                self._provider = self.context.get_provider_by_id(self._provider_id)
                if self._provider:
                    return self._provider
            except Exception:
                pass

        self._provider = self.context.get_using_provider()
        return self._provider

    async def chat(self, system_prompt: str, user_prompt: str) -> str:
        """调用 LLM 并返回文本"""
        provider = self._get_provider()
        if not provider:
            raise RuntimeError("没有可用的 LLM Provider")

        result = await provider.text_chat(
            prompt=user_prompt,
            system_prompt=system_prompt,
        )
        if hasattr(result, "result_chain") and result.result_chain:
            return result.result_chain.get_plain_text() or ""
        if hasattr(result, "completion"):
            return result.completion
        if hasattr(result, "text"):
            return result.text
        return str(result)


class AstrBotContextProvider(ContextProvider):
    """从 AstrBot 事件中提取上下文"""

    def get_user_id(self, event) -> str:
        """提取用户唯一标识"""
        try:
            if hasattr(event, "get_sender_id"):
                sid = event.get_sender_id()
                if sid:
                    return str(sid)
        except Exception:
            pass
        try:
            if hasattr(event, "get_session_id"):
                return str(event.get_session_id())
        except Exception:
            pass
        sender = getattr(event, "sender", None)
        if sender:
            user_id = getattr(sender, "user_id", None)
            if user_id:
                return str(user_id)
        group_id = getattr(event, "group_id", None) or getattr(event, "group_id", None)
        if group_id:
            return f"group_{group_id}"
        return "default"

    def get_conversation_text(self, event) -> str:
        """提取用户消息文本"""
        if hasattr(event, "get_message_str"):
            return event.get_message_str() or ""
        if hasattr(event, "message_str"):
            return event.message_str or ""
        return getattr(event, "message", "") or ""
