"""AstrBot 框架适配器

适配器类:
    AstrBotLLM      — 将 AstrBot LLM Provider 包装为 memori.LLMProvider
    AstrBotCtx      — 从 AstrBot 事件中提取用户信息

Agent Tools:
    RecallTool      — 搜索长期记忆的 FunctionTool
    MemorizeTool    — 主动写入记忆的 FunctionTool

Star 插件入口在项目根目录的 main.py 中（AstrBot 固定导入 main.py）。
"""

from .adapter import AstrBotLLM, AstrBotCtx
from .tools import RecallTool, MemorizeTool

__all__ = [
    "AstrBotLLM",
    "AstrBotCtx",
    "RecallTool",
    "MemorizeTool",
]
