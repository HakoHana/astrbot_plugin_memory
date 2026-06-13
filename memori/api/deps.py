"""FastAPI 依赖注入 — 核心实例 + 认证 + 授权"""

from __future__ import annotations

from fastapi import HTTPException, Query, Request

from ..core.memory_core import MemoryCore


def get_core(request: Request) -> MemoryCore:
    """从 FastAPI app.state 获取 MemoryCore 实例"""
    core: MemoryCore | None = getattr(request.app.state, "memory_core", None)
    if core is None:
        raise RuntimeError("MemoryCore 未初始化，请先调用 app.state.memory_core = core")
    return core


async def get_current_user(request: Request) -> str:
    """从 auth middleware 注入的 state 获取当前用户 ID"""
    uid = getattr(request.state, "user_id", None)
    if uid is None:
        raise HTTPException(status_code=401, detail="未授权，请提供有效令牌")
    return uid


async def authorized_user(
    uid: str = Query(..., description="目标用户 ID"),
    user_id: str = __import__("fastapi").Depends(get_current_user),
) -> str:
    """检查是否有权限访问目标用户的数据

    权限规则（社交图谱 + 权重）：
    - 查自己的数据永远允许
    - 查邻居的数据要求 weight >= 0
    """
    if uid == user_id:
        return uid

    # 走邻居缓存检查
    from .auth import AuthManager
    from . import _get_auth_manager

    auth = _get_auth_manager()
    neighbors = await auth.get_accessible_users(user_id)

    if not auth.check_access(neighbors, uid, min_weight=0.0):
        raise HTTPException(
            status_code=403,
            detail=f"无权访问用户 {uid} 的数据（无社交关系）",
        )
    return uid


async def authorized_write(
    uid: str = Query(..., description="目标用户 ID"),
    user_id: str = __import__("fastapi").Depends(get_current_user),
) -> str:
    """写操作权限检查 — 只能写自己的数据"""
    if uid != user_id:
        raise HTTPException(
            status_code=403,
            detail="无权修改其他用户的数据",
        )
    return uid


async def authorized_admin(
    user_id: str = __import__("fastapi").Depends(get_current_user),
) -> str:
    """管理员权限检查（预留）"""
    # TODO: 接入管理员白名单
    return user_id
