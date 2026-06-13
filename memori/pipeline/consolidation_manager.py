"""调度器 + 会话状态管理器 — 水位线 + 空闲超时双触发，重量操作委托 WarmProcessor

触发器设计：
A. 热缓存水位线 — 用户热缓存消息数达到阈值 → 立刻触发
B. 空闲超时 — 用户超过 N 分钟无活动 → 扫描未整理内容兜底整理

全局限速防止多个用户同时触发挤爆 LLM 队列。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from ..core.logger import logger

from ..models.memory_atom import PersistedSessionState
from ..storage.state_store import StateStore
from ..core.interfaces import IConsolidationManager, IWarmProcessor, IHotMessageCache


class ConsolidationManager(IConsolidationManager):
    """
    调度器 + 会话状态管理器

    职责（轻量）：
    - 消息计数 & 去抖
    - 水位线触发（来自 HotCache 回调）
    - 空闲超时兜底
    - 全局限速 + 用户级限速
    - 会话状态持久化（延迟刷写）
    - 空闲超时兜底

    重量操作（LLM 调用、DB 写入）委托给 WarmProcessor 异步队列。
    """

    def __init__(
        self,
        state_store: StateStore,
        hot_cache: IHotMessageCache | None = None,
        warm_processor=None,
        config: dict[str, Any] | None = None,
    ):
        self.state_store = state_store
        self.hot_cache = hot_cache
        self.warm_processor = warm_processor
        self.config = config or {}

        # 配置
        self.idle_timeout_minutes = self.config.get("idle_timeout_minutes", 60)
        self._scan_interval_minutes = self.config.get("scan_interval_minutes", 120)
        self._min_global_interval = self.config.get("min_global_interval", 120)
        # 用户级速率（独立于全局限速，防同用户刷屏重复触发）
        self._min_user_interval = self.config.get("min_user_interval", 60)

        # 会话状态（内存中，延迟写回）
        self._states: dict[str, PersistedSessionState] = {}
        self._dirty_users: set[str] = set()
        self._flush_task: asyncio.Task | None = None
        self._flush_interval: float = 5.0

        # 空闲检测
        self._idle_check_task: asyncio.Task | None = None
        self._idle_check_interval: float = 60.0
        self._last_activity: dict[str, float] = {}

        # 定时扫描（不管用户是否活跃，周期扫积压）
        self._periodic_scan_task: asyncio.Task | None = None

        # 去抖
        self._debounce_interval: float = 10.0
        self._last_trigger_check: dict[str, float] = {}
        self._pending_counts: dict[str, int] = {}

        # 全局限速
        self._global_last_consolidation: float = 0.0

        # 用户级限速（独立于全局限速）
        self._last_user_consolidation: dict[str, float] = {}

        self._destroyed = False

        # 注册为 HotCache 水位线回调
        if self.hot_cache:
            self.hot_cache.set_water_callback(self._on_water_trigger)

    async def initialize(self):
        """从数据库恢复所有会话状态"""
        states = await self.state_store.load_all()
        self._states = states
        now = time.time()
        for uid in states:
            if states[uid].msg_count > 0:
                self._last_activity[uid] = now

        self._flush_task = asyncio.create_task(self._flush_loop())
        self._idle_check_task = asyncio.create_task(self._idle_check_loop())
        self._periodic_scan_task = asyncio.create_task(self._periodic_scan_loop())

    async def destroy(self):
        """销毁调度器"""
        self._destroyed = True
        pending_tasks = []
        for t in (self._flush_task, self._idle_check_task, self._periodic_scan_task):
            if t and not t.done():
                t.cancel()
                pending_tasks.append(t)
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        if self._dirty_users:
            await self._flush_dirty_states()
        for uid, state in self._states.items():
            await self.state_store.save(state)
        self._states.clear()
        self._dirty_users.clear()

    # ═══════════════════════════════════════════════════
    #  A. 水位线触发（由 HotCache 回调）
    # ═══════════════════════════════════════════════════

    def _on_water_trigger(self, user_id: str):
        """HotCache 水位线到顶时回调此方法"""
        if self._destroyed:
            return
        # 异步执行，不阻塞 HotCache.push()
        task = asyncio.ensure_future(self._handle_water_trigger(user_id))
        task.add_done_callback(lambda t: None)

    async def _handle_water_trigger(self, user_id: str):
        """异步处理水位触发"""
        # 用户级限速
        now = time.time()
        last_user = self._last_user_consolidation.get(user_id, 0.0)
        if now - last_user < self._min_user_interval:
            logger.debug(f"[Memory] 用户级限速: {user_id} 距上次 {now - last_user:.0f}s")
            return

        # 全局限速
        if self._global_last_consolidation > 0 and now - self._global_last_consolidation < self._min_global_interval:
            logger.debug(f"[Memory] 全局限速: 距上次 {now - self._global_last_consolidation:.0f}s")
            return

        # 取热缓存最近的对话上下文作为整理素材（用户 + Bot 双向消息）
        conversation_text = self._get_hot_context(user_id)

        state = self._get_or_create_state(user_id)
        logger.info(f"[Memory] 水位线触发整理: uid={user_id}")
        if self.warm_processor:
            await self.warm_processor.enqueue(user_id, conversation_text, state, on_done=self._after_consolidation)

    # ═══════════════════════════════════════════════════
    #  B. on_message（AstrBot 每条消息调一次）
    # ═══════════════════════════════════════════════════

    async def on_message(self, user_id: str, conversation_text: str, sender_name: str = ""):
        """
        每次有消息到来时的入口。

        职责（轻量）：
        - 计数（用于空闲超时判断是否有新内容）
        - 去抖
        - 记录 last_activity（空闲超时用）

        水位线触发由 HotCache.push() 直接回调，不经由此方法。
        """
        if self._destroyed:
            return

        # 1. 内存计数（零 SQLite 开销）
        self._pending_counts[user_id] = self._pending_counts.get(user_id, 0) + 1
        self._last_activity[user_id] = time.time()

        # 2. 去抖：10 秒内不重复刷状态
        now = time.time()
        last_check = self._last_trigger_check.get(user_id, 0.0)
        if now - last_check < self._debounce_interval:
            return
        self._last_trigger_check[user_id] = now

        # 3. 内存计数 → 刷入持久状态
        state = self._get_or_create_state(user_id)
        state.msg_count += self._pending_counts.pop(user_id, 0)
        self._mark_dirty(user_id)

    # ═══════════════════════════════════════════════════
    #  整理完成回调
    # ═══════════════════════════════════════════════════

    async def _after_consolidation(self, user_id: str, result):
        """整理后的收尾工作 — 状态重置 + 解除水位线"""
        now = time.time()
        self._global_last_consolidation = now
        self._last_user_consolidation[user_id] = now

        # 重置水位线，允许下次满水位再次触发
        if self.hot_cache:
            try:
                self.hot_cache.reset_water_level(user_id)
            except Exception:
                pass

        state = self._get_or_create_state(user_id)
        state.reset_after_consolidation()
        self._mark_dirty(user_id)

    # ═══════════════════════════════════════════════════
    #  延迟刷写
    # ═══════════════════════════════════════════════════

    def _mark_dirty(self, user_id: str):
        self._dirty_users.add(user_id)

    async def _flush_loop(self):
        while not self._destroyed:
            try:
                await asyncio.sleep(self._flush_interval)
                if self._dirty_users:
                    await self._flush_dirty_states()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    async def _flush_dirty_states(self):
        dirty = list(self._dirty_users)
        self._dirty_users.clear()
        for uid in dirty:
            state = self._states.get(uid)
            if state:
                try:
                    await self.state_store.save(state)
                except Exception as e:
                    logger.warning(f"[Memory] 状态刷写失败 {uid}: {e}")
                    self._dirty_users.add(uid)

    # ═══════════════════════════════════════════════════
    #  空闲超时（兜底）
    # ═══════════════════════════════════════════════════

    async def _idle_check_loop(self):
        timeout_sec = self.idle_timeout_minutes * 60
        while not self._destroyed:
            try:
                await asyncio.sleep(self._idle_check_interval)
                now = time.time()
                for uid, last_active in list(self._last_activity.items()):
                    if self._destroyed:
                        return
                    if now - last_active < timeout_sec:
                        continue

                    state = self._states.get(uid)
                    if not state:
                        continue

                    # 检查是否有未整理的新消息
                    pending = self._pending_counts.pop(uid, 0)
                    has_content = (state.msg_count + pending) > 0

                    if pending:
                        state.msg_count += pending
                        self._mark_dirty(uid)

                    if not has_content:
                        continue

                    # 用户级限速
                    last_user = self._last_user_consolidation.get(uid, 0.0)
                    if now - last_user < self._min_user_interval:
                        continue

                    # 全局限速
                    if self._global_last_consolidation > 0 and now - self._global_last_consolidation < self._min_global_interval:
                        continue

                    logger.info(f"[Memory] 空闲超时触发: {uid}")
                    conv_text = self._get_hot_context(uid)
                    if self.warm_processor:
                        await self.warm_processor.enqueue(uid, conv_text, state, on_done=self._after_consolidation)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    # ═══════════════════════════════════════════════════
    #  C. 定时扫描（不管活跃状态，周期扫积压）
    # ═══════════════════════════════════════════════════

    async def _periodic_scan_loop(self):
        """定时扫描：每 scan_interval_minutes 扫一遍所有用户

        不管用户是否活跃，只要距上次整理超过 scan_interval 且有积压内容
        （包括未到水位线的和因限速被跳过的），就触发整理。
        """
        interval_sec = self._scan_interval_minutes * 60
        while not self._destroyed:
            try:
                await asyncio.sleep(interval_sec)
                if self._destroyed:
                    return
                now = time.time()
                for uid, state in list(self._states.items()):
                    if self._destroyed:
                        return
                    # 距上次整理不足一个周期则跳过
                    if now - state.last_consolidated_at < interval_sec:
                        continue
                    # 合并 pending 计数到 state
                    pending = self._pending_counts.pop(uid, 0)
                    if pending:
                        state.msg_count += pending
                        self._mark_dirty(uid)
                    # 没有积压内容则跳过
                    if state.msg_count <= 0:
                        continue
                    # 用户级限速
                    last_user = self._last_user_consolidation.get(uid, 0.0)
                    if now - last_user < self._min_user_interval:
                        continue
                    # 全局限速
                    if self._global_last_consolidation > 0 and now - self._global_last_consolidation < self._min_global_interval:
                        continue
                    logger.info(f"[Memory] 定时扫描触发: uid={uid}")
                    conv_text = self._get_hot_context(uid)
                    if self.warm_processor:
                        await self.warm_processor.enqueue(uid, conv_text, state, on_done=self._after_consolidation)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    # ═══════════════════════════════════════════════════
    #  状态管理
    # ═══════════════════════════════════════════════════

    def _get_or_create_state(self, user_id: str) -> PersistedSessionState:
        if user_id not in self._states:
            self._states[user_id] = PersistedSessionState(user_id=user_id)
        return self._states[user_id]

    def get_state(self, user_id: str) -> PersistedSessionState | None:
        return self._states.get(user_id)

    def set_warm_processor(self, warm_processor: IWarmProcessor):
        """注入 WarmProcessor（初始化顺序解耦）"""
        self.warm_processor = warm_processor

    def set_hot_cache(self, hot_cache: IHotMessageCache):
        """注入 HotCache（初始化顺序解耦）"""
        self.hot_cache = hot_cache
        self.hot_cache.set_water_callback(self._on_water_trigger)

    # ── 辅助 ──

    def _get_hot_context(self, user_id: str) -> str:
        """从热缓存获取用户 + Bot 双向对话上下文"""
        if not self.hot_cache:
            return ""
        try:
            return self.hot_cache.format_recent_context(user_id, limit=10)
        except Exception:
            return ""

    def update_config(self, config: dict[str, Any]):
        """热更新配置"""
        self.idle_timeout_minutes = config.get("idle_timeout_minutes", self.idle_timeout_minutes)
        self._scan_interval_minutes = config.get("scan_interval_minutes", self._scan_interval_minutes)
        self._min_global_interval = config.get("min_global_interval", self._min_global_interval)
        self._min_user_interval = config.get("min_user_interval", self._min_user_interval)
