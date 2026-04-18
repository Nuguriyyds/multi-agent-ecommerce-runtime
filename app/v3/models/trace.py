from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from .base import V3Model
from .capability import CapabilityKind
from .decision import AgentDecision, Observation


class InvocationRecord(V3Model):
    invocation_id: str
    task_id: str
    capability_name: str
    capability_kind: CapabilityKind
    status: Literal["started", "succeeded", "failed"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    observation_id: str | None = None
    error: str | None = None


class TaskRecord(V3Model):
    task_id: str
    task_name: str
    status: str
    invocation_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TraceRecord(V3Model):
    trace_id: str
    session_id: str
    turn_number: int
    decisions: list[AgentDecision] = Field(default_factory=list)
    task_records: list[TaskRecord] = Field(default_factory=list)
    invocations: list[InvocationRecord] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    guardrail_hits: list[str] = Field(default_factory=list)
    memory_reads: list[str] = Field(default_factory=list)
    memory_denials: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    terminal_state: Literal["reply", "clarification", "fallback"] | None = None
