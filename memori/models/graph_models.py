"""Graph data models"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class GraphNode:
    """Graph node - id = node_key = node_type:canonical_value"""
    node_type: str          # entity / user / topic / emotion / date / diary
    value: str              # display name
    canonical_value: str    # normalized value (for dedup)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def node_key(self) -> str:
        return f"{self.node_type}:{self.canonical_value}"


@dataclass(slots=True)
class SocialEdge:
    """Social relation edge - stored in edges table, relation_type identifies type

    Relation is permission: weight determines access depth, cap limits unconfirmed max.
    """
    from_user: str              # claimer user_id
    to_user: str                # target user_id
    relation_type: str          # friend_of | family_of | trusted_by | blocked_by
    status: str = "pending"     # pending | active | rejected | blocked | passive
    weight: float = 0.1         # 0.0-1.0
    cap: float = 0.4            # max reachable weight (locked at 0.4 when pending)
    source: str = "explicit_claim"  # explicit_claim | co_occur | mutual_confirmation
    id: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def edge_id(self) -> str:
        if self.id:
            return self.id
        a, b = (self.from_user, self.to_user) if self.from_user < self.to_user else (self.to_user, self.from_user)
        return f"social:{a}:{self.relation_type}:{b}"

    @property
    def effective_weight(self) -> float:
        """Actual effective weight = min(weight, cap)"""
        return min(self.weight, self.cap)
