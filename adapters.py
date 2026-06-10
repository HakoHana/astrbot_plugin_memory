"""AstrBot 适配层 — 实现 memoria 定义的接口"""

from __future__ import annotations

from memoria.core.adapters import LLMProvider, ContextProvider


class AstrBotLLMProvider(LLMProvider):
    """封装 AstrBot 的 LLM Provider 调用"""

    def __init__(self, context):
        self.context = context
        self._provider = None
        self._provider_id = None
        self._judge_provider = None
        self._judge_provider_id = None

    def set_provider(self, provider_id: str | None):
        self._provider_id = provider_id
        self._provider = None

    def set_judge_provider(self, provider_id: str | None):
        self._judge_provider_id = provider_id
        self._judge_provider = None

    def _get_provider(self, use_judge: bool = False):
        key = "_judge_provider" if use_judge else "_provider"
        pid = "_judge_provider_id" if use_judge else "_provider_id"
        pid_val = getattr(self, pid, None)
        cached = getattr(self, key, None)
        if cached:
            return cached
        if pid_val:
            try:
                p = self.context.get_provider_by_id(pid_val)
                if p:
                    setattr(self, key, p)
                    return p
            except Exception:
                pass
        p = self.context.get_using_provider()
        setattr(self, key, p)
        return p

    async def chat(self, system_prompt: str, user_prompt: str, use_judge: bool = False) -> str:
        provider = self._get_provider(use_judge=use_judge)
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
        if hasattr(event, "get_message_str"):
            return event.get_message_str() or ""
        if hasattr(event, "message_str"):
            return event.message_str or ""
        return getattr(event, "message", "") or ""
