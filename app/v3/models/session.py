from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import V3Model
from .decision import Action, Observation
from .task import TurnTaskBoard


class CompressionPolicy(V3Model):
    max_messages: int = 8
    keep_sections: list[str] = Field(default_factory=list)
    pinned_keys: list[str] = Field(default_factory=list)


class SessionState(V3Model):
    session_id: str
    user_id: str | None = None
    turn_count: int = 0
    session_working_memory: dict[str, Any] = Field(default_factory=dict)
    durable_user_memory: dict[str, Any] = Field(default_factory=dict)
    last_turn_status: Literal["reply", "clarification", "fallback"] | None = None


class LoopState(V3Model):
    step_number: int = 0
    current_node: str
    current_task_id: str | None = None
    ready_task_ids: list[str] = Field(default_factory=list)
    blocked_task_ids: list[str] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)


class ContextPacket(V3Model):
    session_id: str
    latest_user_message: str
    active_constraints: dict[str, Any] = Field(default_factory=dict)
    session_working_memory: dict[str, Any] = Field(default_factory=dict)
    durable_user_memory: dict[str, Any] = Field(default_factory=dict)
    confirmed_preferences: dict[str, Any] = Field(default_factory=dict)
    current_candidates: list[Any] = Field(default_factory=list)
    comparison_dimensions: list[str] = Field(default_factory=list)
    unanswered_clarifications: list[Any] = Field(default_factory=list)
    memory_conflicts: list[Any] = Field(default_factory=list)
    recent_observation_ids: list[str] = Field(default_factory=list)
    compression_policy: CompressionPolicy = Field(default_factory=CompressionPolicy)


class TurnResult(V3Model):
    session_id: str
    turn_number: int
    status: Literal["reply", "clarification", "fallback"]
    message: str
    action: Action
    trace_id: str | None = None
    completed_steps: int = 0
    error_summary: str | None = None


class TurnRuntimeContext(V3Model):
    session: SessionState
    loop_state: LoopState
    context_packet: ContextPacket
    task_board: TurnTaskBoard
    turn_result: TurnResult | None = None
    trace_id: str | None = None
