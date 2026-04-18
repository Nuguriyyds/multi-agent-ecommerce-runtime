from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from .base import V3Model
from .memory import MemoryWriteDecision


class Observation(V3Model):
    observation_id: str
    source: str
    status: Literal["ok", "error", "partial"] = "ok"
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_source: str | None = None


class ReplyToUserAction(V3Model):
    kind: Literal["reply_to_user"] = "reply_to_user"
    message: str
    observation_ids: list[str] = Field(default_factory=list)


class AskClarificationAction(V3Model):
    kind: Literal["ask_clarification"] = "ask_clarification"
    question: str
    missing_slots: list[str] = Field(default_factory=list)


class CallToolAction(V3Model):
    kind: Literal["call_tool"] = "call_tool"
    capability_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class CallSubAgentAction(V3Model):
    kind: Literal["call_sub_agent"] = "call_sub_agent"
    capability_name: str
    brief: dict[str, Any] = Field(default_factory=dict)


class FallbackAction(V3Model):
    kind: Literal["fallback"] = "fallback"
    reason: str
    user_message: str


Action = Annotated[
    ReplyToUserAction
    | AskClarificationAction
    | CallToolAction
    | CallSubAgentAction
    | FallbackAction,
    Field(discriminator="kind"),
]

ACTION_TYPE_ADAPTER = TypeAdapter(Action)


class AgentDecision(V3Model):
    action: Action
    rationale: str
    next_task_label: str | None = None
    continue_loop: bool = False


class HardeningGateResult(V3Model):
    decision: Literal["allow", "block", "degrade", "fallback"]
    reason: str | None = None
    guardrail: str | None = None
    memory_write_decision: MemoryWriteDecision | None = None
    fallback_action: FallbackAction | None = None
