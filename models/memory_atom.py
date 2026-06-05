"""记忆插件数据模型"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AtomType(str, Enum):
    """原子记忆类型"""
    EPISODIC = "episodic"       # 情景/事件
    FACTUAL = "factual"         # 事实型
    PREFERENCE = "preference"   # 偏好型
    PLANNED = "planned"         # 计划型
    RELATIONAL = "relational"   # 关系型
    UNKNOWN = "unknown"


class AtomStatus(str, Enum):
    """原子生命周期状态"""
    ACTIVE = "active"           # 活跃
    DORMANT = "dormant"         # 休眠
    ARCHIVED = "archived"       # 归档
    FORGOTTEN = "forgotten"     # 遗忘


@dataclass(slots=True)
class MemoryAtom:
    """记忆原子 — 结构化事实的最小单元"""
    user_id: str
    diary_date: str                    # YYYY-MM-DD
    content: str = ""
    atom_type: AtomType = AtomType.UNKNOWN
    entities: list[str] = field(default_factory=list)
    importance: float = 0.5            # 0.0 ~ 1.0
    confidence: float = 0.7            # 0.0 ~ 1.0
    access_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float | None = None
    ttl_days: float = 30.0
    status: AtomStatus = AtomStatus.ACTIVE
    session_id: str | None = None
    diary_ref: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    atom_id: int = 0                   # 数据库 ID，插入后填充

    @property
    def is_expired(self) -> bool:
        """检查是否超过 TTL"""
        if self.ttl_days <= 0:
            return False
        age = time.time() - self.created_at
        return age > self.ttl_days * 86400


@dataclass(slots=True)
class CaptureJudgeResult:
    """LLM 判断结果 — 值不值得记"""
    should_remember: bool = False
    reason: str = ""
    importance: float = 0.0
    mood: str = ""
    context_summary: str = ""


@dataclass(slots=True)
class CaptureResult:
    """一次抓取的结果"""
    wrote_diary: bool = False
    diary_content: str = ""
    atoms: list[MemoryAtom] = field(default_factory=list)

    @property
    def atom_count(self) -> int:
        return len(self.atoms)


@dataclass(slots=True)
class PersistedSessionState:
    """持久化的会话状态 — 每个用户一份"""
    user_id: str
    msg_count: int = 0
    warmup_threshold: int = 1          # 0 = 已完成暖启动
    last_consolidated_at: float = 0.0
    last_diary_date: str = ""
    diary_count: int = 0
    diary_count_since_persona: int = 0
    l1_retry_count: int = 0

    def reset_after_consolidation(self):
        """整理后重置计数"""
        self.msg_count = 0
        self.l1_retry_count = 0
        self.last_consolidated_at = time.time()
        self.diary_count += 1
        self.diary_count_since_persona += 1


@dataclass(slots=True)
class RecallResult:
    """召回结果 — 供注入器使用"""
    memory_text: str = ""
    atoms: list[MemoryAtom] = field(default_factory=list)
    persona_text: str | None = None
