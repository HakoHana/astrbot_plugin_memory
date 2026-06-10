"""memori — AstrBot 插件适配层

此文件的关键作用：
Python 需要插件的根目录能作为包导入（__import__("memori.main")），
因此必须有 __init__.py。
后续则将核心功能委托给 memori/ 子目录的真正包。
"""

from .memori import MemoryCore
from .memori.core.adapters import LLMProvider, ContextProvider

__all__ = ["MemoryCore", "LLMProvider", "ContextProvider"]
