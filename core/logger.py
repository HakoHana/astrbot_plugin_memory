"""Logger 桥接模块

统一日志入口，隔离对 astrbot.api.logger 的直接依赖。
在其他平台可替换此模块的 backend 即可。
"""

import logging

logger = logging.getLogger("memory")
