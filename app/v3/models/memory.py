from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field

from .base import V3Model


class MemoryLayer(str, Enum):
    session_working = "session_working"
    durable_user = "durable_user"


class MemorySource(str, Enum):
    user_confirmed = "user_confirmed"
    inferred = "inferred"
    tool_fact = "tool_fact"


class MemoryStatus(str, Enum):
    active = "active"
    superseded = "superseded"
    revoked = "revoked"
    conflicted = "conflicted"


class MemoryEntry(V3Model):
    key: str
    value: Any
    source: MemorySource
    layer: MemoryLayer = MemoryLayer.session_working
    status: MemoryStatus = MemoryStatus.active
    observation_id: str | None = None
    rationale: str | None = None


class MemoryWriteDecision(V3Model):
    decision: Literal["allow", "deny", "replace", "revoke"]
    target_layer: MemoryLayer
    memory_key: str | None = None
    reason: str | None = None

    @classmethod
    def evaluate(
        cls,
        entry: MemoryEntry,
        *,
        target_layer: MemoryLayer = MemoryLayer.durable_user,
    ) -> "MemoryWriteDecision":
        if target_layer == MemoryLayer.session_working:
            return cls(
                decision="allow",
                target_layer=target_layer,
                memory_key=entry.key,
                reason="session working memory accepts structured entries",
            )

        if entry.source == MemorySource.user_confirmed:
            return cls(
                decision="allow",
                target_layer=target_layer,
                memory_key=entry.key,
                reason="source user_confirmed is eligible for durable memory",
            )

        return cls(
            decision="deny",
            target_layer=target_layer,
            memory_key=entry.key,
            reason=f"durable memory requires source=user_confirmed, got {entry.source.value}",
        )
