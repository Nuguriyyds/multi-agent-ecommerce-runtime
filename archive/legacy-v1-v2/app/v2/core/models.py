from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.shared.models.domain import BehaviorEvent, InventoryStatus, MarketingCopy, Product


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionState(BaseModel):
    session_id: str
    user_id: str
    memory: dict[str, Any] = Field(default_factory=dict)
    status: Literal["active", "closed"] = "active"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    turn_number: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=utc_now)


class SessionTurnRecord(BaseModel):
    turn_id: str
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    turn_number: int = Field(ge=1)
    timestamp: datetime = Field(default_factory=utc_now)


class PreferenceSignal(BaseModel):
    category: Literal["budget", "product_category", "brand", "use_case", "exclusion"]
    value: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_turn: int = Field(ge=1)


class UserProfile(BaseModel):
    user_id: str
    preferred_categories: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    use_cases: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    price_range: tuple[float, float] | None = None
    segments: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    cold_start: bool = False


class WorkerTask(BaseModel):
    task_id: str
    worker_name: str
    step: int = Field(ge=1)
    intent: str
    input: dict[str, Any] = Field(default_factory=dict)


class WorkerResult(BaseModel):
    worker_name: str
    success: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    signals: list[PreferenceSignal] = Field(default_factory=list)
    clarification: str | None = None
    error: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)


class TaskRecord(BaseModel):
    task_id: str
    task_scope: Literal["conversation", "background"]
    session_id: str | None = None
    turn_id: str | None = None
    event_id: str | None = None
    manager_name: str | None = None
    worker_name: str | None = None
    tool_name: str | None = None
    step: int | None = Field(default=None, ge=1)
    status: Literal["pending", "running", "completed", "failed"]
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)


class StoredTaskRecord(TaskRecord):
    created_at: datetime
    updated_at: datetime


class RecommendationSnapshot(BaseModel):
    snapshot_id: str
    user_id: str
    scene: str = "default"
    scene_context: dict[str, Any] = Field(default_factory=dict)
    products: list[Product] = Field(default_factory=list)
    copies: list[MarketingCopy] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None


class Event(BaseModel):
    event_id: str
    event_type: str
    user_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "processing", "completed", "failed"] = "pending"
    retry_count: int = Field(default=0, ge=0)
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    processed_at: datetime | None = None


class FeedbackEvent(BaseModel):
    event_id: str
    user_id: str
    event_type: Literal["click", "skip", "purchase"]
    scene: str
    product_id: str | None = None
    product_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class FeedbackSummary(BaseModel):
    boosted_categories: list[str] = Field(default_factory=list)
    boosted_brands: list[str] = Field(default_factory=list)
    suppressed_product_ids: list[str] = Field(default_factory=list)


TurnPlanStepName = Literal[
    "clarify",
    "preference_worker",
    "catalog_worker",
    "inventory_worker",
    "comparison_worker",
    "copy_worker",
    "profile.request_projection",
]


class TurnPlanStep(BaseModel):
    name: TurnPlanStepName
    step: int = Field(ge=1)
    conditional: bool = False
    skip_reason: str | None = None


class TurnPlan(BaseModel):
    intent: Literal["clarify", "fallback", "advisory", "recommendation", "comparison"] | None = None
    terminal_state: Literal["reply_ready", "needs_clarification", "fallback_used"]
    steps: list[TurnPlanStep] = Field(default_factory=list)
    fallback_reason: str | None = None


class ManagerTurnContext(BaseModel):
    session_id: str
    turn_id: str = ""
    user_id: str
    scene: str = "default"
    scene_context: dict[str, Any] = Field(default_factory=dict)
    session_state: SessionState
    user_profile: UserProfile | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    side_effect_level: Literal["none", "session", "persistent"]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        parts = value.split(".")
        if len(parts) != 2 or not all(parts):
            raise ValueError("tool name must use domain.action format")
        return value


__all__ = [
    "BehaviorEvent",
    "ChatTurn",
    "Event",
    "FeedbackEvent",
    "FeedbackSummary",
    "InventoryStatus",
    "ManagerTurnContext",
    "MarketingCopy",
    "PreferenceSignal",
    "Product",
    "RecommendationSnapshot",
    "SessionState",
    "SessionTurnRecord",
    "StoredTaskRecord",
    "TaskRecord",
    "ToolSpec",
    "TurnPlan",
    "TurnPlanStep",
    "TurnPlanStepName",
    "UserProfile",
    "WorkerResult",
    "WorkerTask",
]
