from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from .base import V3Model


class HookPoint(str, Enum):
    turn_start = "turn_start"
    decision = "decision"
    task = "task"
    invocation = "invocation"
    memory_write = "memory_write"
    fallback = "fallback"
    turn_end = "turn_end"


class HookEvent(V3Model):
    hook_point: HookPoint
    session_id: str | None = None
    trace_id: str | None = None
    turn_number: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class HookResult(V3Model):
    handler_name: str
    accepted: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None
