"""请求上下文 — contextvars 实现

在 on_llm_request 等入口处设置当前用户 ID，
tools 和下游模块通过 contextvar 读取，无需传参。

asyncio 自动随 Task 传播子任务。
"""

import contextvars

current_user_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    'current_user_id', default=''
)
