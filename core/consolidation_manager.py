"""调度器 + 会话状态管理器 — 参考 TencentDB PipelineManager"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from ..models.memory_atom import PersistedSessionState, CaptureResult
from ..storage.state_store import StateStore
from .capturer import Capturer
from .persona_engine import PersonaEngine


class ConsolidationManager:
    """
    调度器 + 会话状态管理器

    借鉴 TencentDB PipelineManager 设计：
    - L1 = Capturer（判断+写日记+提取原子）
    - L3 = PersonaEngine（画像更新）
    - 暖启动：新用户阈值从 1→2→4→8 指数增长
    - 空闲超时兜底：沉默一段时间后自动整理
    - 会话状态持久化：每次操作后写入数据库
    - 重试机制：LLM 失败后自动重试
    """

    def __init__(
        self,
        capturer: Capturer,
        persona_engine: PersonaEngine,
        state_store: StateStore,
        on_memory_created: Callable | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.capturer = capturer
        self.persona_engine = persona_engine
        self.state_store = state_store
        self.on_memory_created = on_memory_created  # 回调：当新记忆产生时通知
        self.config = config or {}

        # 配置
        self.trigger_msg_count = self.config.get("trigger_msg_count", 10)
        self.trigger_time_minutes = self.config.get("trigger_time_minutes", 360)
        self.immediate_capture = self.config.get("immediate_capture", True)
        self.warmup_enabled = self.config.get("warmup_enabled", True)
        self.idle_timeout_minutes = self.config.get("idle_timeout_minutes", 30)
        self.persona_update_interval = self.config.get("persona_update_interval", 10)
        self.max_l1_retries = self.config.get("max_l1_retries", 3)

        # 内存中的会话状态（启动时从数据库恢复）
        self._states: dict[str, PersistedSessionState] = {}

        # 空闲超时定时器
        self._idle_timers: dict[str, asyncio.Task] = {}

        # 是否已销毁
        self._destroyed = False

    async def initialize(self):
        """从数据库恢复所有会话状态"""
        states = await self.state_store.load_all()
        self._states = states
        for uid in states:
            if states[uid].msg_count > 0:
                self._start_idle_timer(uid)

    async def destroy(self):
        """销毁调度器"""
        self._destroyed = True
        for task in self._idle_timers.values():
            task.cancel()
        self._idle_timers.clear()
        # 持久化所有状态
        for uid, state in self._states.items():
            await self.state_store.save(state)

    async def on_message(self, user_id: str, conversation_text: str) -> CaptureResult | None:
        """
        每次消息调用：计数 + 判断是否触发

        返回 CaptureResult 表示是否写了日记，None 表示未触发
        """
        if self._destroyed:
            return None

        state = self._get_or_create_state(user_id)
        state.msg_count += 1

        # 检查触发条件
        should_trigger = False
        trigger_reason = ""

        # 条件 A：消息数达到阈值
        threshold = state.warmup_threshold if self.warmup_enabled and state.warmup_threshold > 0 else self.trigger_msg_count
        if state.msg_count >= threshold:
            should_trigger = True
            trigger_reason = f"消息数达到 {threshold}"

        # 条件 B：即时捕捉（重要事件）
        if self.immediate_capture and not should_trigger:
            try:
                judge = await self.capturer.should_capture(conversation_text)
                if judge.should_remember and judge.importance >= 0.7:
                    should_trigger = True
                    trigger_reason = f"即时捕捉: {judge.reason}"
                    # 即时捕捉直接调用 capture（跳过判断步骤）
                    result = await self.capturer.capture(user_id, conversation_text, judge)
                    await self._after_consolidation(user_id, result)
                    return result
            except Exception:
                pass

        # 条件 C：时间间隔
        if not should_trigger and self.trigger_time_minutes > 0:
            elapsed = time.time() - state.last_consolidated_at
            if elapsed >= self.trigger_time_minutes * 60:
                should_trigger = True
                trigger_reason = f"时间间隔达到 {self.trigger_time_minutes} 分钟"

        if not should_trigger:
            # 持久化状态（仅在计数变化时）
            await self.state_store.save(state)
            return None

        # 执行 L1 整理
        # 先判断（除非已经是即时捕捉判断过的）
        judge = await self.capturer.should_capture(conversation_text)
        if not judge.should_remember:
            # 不值得记，但更新状态防止一直触发
            state.last_consolidated_at = time.time()
            state.l1_retry_count = 0
            await self.state_store.save(state)
            return None

        # 执行完整 capture
        result = await self._run_l1_with_retry(user_id, conversation_text, judge)
        await self._after_consolidation(user_id, result)
        return result

    async def _run_l1_with_retry(
        self, user_id: str, conversation: str, judge
    ) -> CaptureResult:
        """带重试的 L1 执行"""
        state = self._get_or_create_state(user_id)
        last_error = None

        for attempt in range(self.max_l1_retries + 1):
            try:
                result = await self.capturer.capture(user_id, conversation, judge)
                state.l1_retry_count = 0
                return result
            except Exception as e:
                last_error = e
                state.l1_retry_count += 1
                await self.state_store.save(state)
                if attempt < self.max_l1_retries:
                    await asyncio.sleep(2 ** attempt * 5)  # 指数退避

        # 重试全部失败
        return CaptureResult(wrote_diary=False)

    async def _after_consolidation(self, user_id: str, result: CaptureResult):
        """整理后的收尾工作"""
        state = self._get_or_create_state(user_id)

        # 更新状态
        state.last_consolidated_at = time.time()
        if result.wrote_diary:
            state.last_diary_date = time.strftime("%Y-%m-%d")
            state.diary_count += 1
            state.diary_count_since_persona += 1

        # 重置计数
        state.msg_count = 0

        # 暖启动：阈值指数增长
        if self.warmup_enabled and state.warmup_threshold > 0:
            new_threshold = min(state.warmup_threshold * 2, self.trigger_msg_count)
            if new_threshold >= self.trigger_msg_count:
                state.warmup_threshold = 0  # 暖启动完成
            else:
                state.warmup_threshold = new_threshold

        # 检查是否需要触发 L3（画像更新）
        if state.diary_count_since_persona >= self.persona_update_interval:
            try:
                await self.persona_engine.update_persona(user_id)
                state.diary_count_since_persona = 0
            except Exception:
                pass

        # 持久化状态
        await self.state_store.save(state)

        # 重启空闲定时器
        self._start_idle_timer(user_id)

        # 通知外部（有新记忆了）
        if self.on_memory_created and result.wrote_diary:
            try:
                if asyncio.iscoroutinefunction(self.on_memory_created):
                    await self.on_memory_created(user_id, result)
                else:
                    self.on_memory_created(user_id, result)
            except Exception:
                pass

    def _start_idle_timer(self, user_id: str):
        """启动空闲超时定时器"""
        # 取消旧的定时器
        old = self._idle_timers.get(user_id)
        if old and not old.done():
            old.cancel()

        # 创建新的定时器
        timeout = self.idle_timeout_minutes * 60
        self._idle_timers[user_id] = asyncio.create_task(
            self._idle_timeout_task(user_id, timeout)
        )

    async def _idle_timeout_task(self, user_id: str, timeout: float):
        """空闲超时任务"""
        try:
            await asyncio.sleep(timeout)
            if self._destroyed:
                return

            state = self._get_or_create_state(user_id)
            if state.msg_count > 0:
                # 空闲超时触发
                judge = await self.capturer.should_capture("")
                if judge.should_remember:
                    result = await self._run_l1_with_retry(user_id, "", judge)
                    await self._after_consolidation(user_id, result)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _get_or_create_state(self, user_id: str) -> PersistedSessionState:
        """获取或创建会话状态"""
        if user_id not in self._states:
            self._states[user_id] = PersistedSessionState(user_id=user_id)
        return self._states[user_id]

    def get_state(self, user_id: str) -> PersistedSessionState | None:
        """获取用户状态（供外部查询）"""
        return self._states.get(user_id)
