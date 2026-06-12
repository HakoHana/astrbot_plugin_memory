"""图谱数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GraphNode:
    """图谱节点 — id = node_key = \”{node_type}:{canonical_value}\”"""
    node_type: str          # entity / user / topic / emotion / date / diary
    value: str              # 展示名
    canonical_value: str    # 归一化值（用于去重）
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def node_key(self) -> str:
        return f"{self.node_type}:{self.canonical_value}"
