from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.shared.models.domain import MarketingCopy
from app.v2.core.models import PreferenceSignal, TurnPlan

V2TerminalState = Literal["reply_ready", "needs_clarification", "fallback_used"]


class CreateSessionRequest(BaseModel):
    user_id: str = Field(min_length=1)


class CreateSessionResponse(BaseModel):
    session_id: str
    manager_type: str = "shopping"
    created_at: datetime


class SessionMessageRequest(BaseModel):
    message: str = Field(min_length=1)
    scene: str = "default"
    scene_context: dict[str, Any] = Field(default_factory=dict)


class RecommendationReadRequest(BaseModel):
    scene: str = "default"
    product_id: str | None = None
    product_ids: list[str] = Field(default_factory=list)


class FeedbackEventRequest(BaseModel):
    event_type: Literal["click", "skip", "purchase"]
    scene: Literal["default", "homepage", "product_page", "cart"] = "default"
    product_id: str | None = None
    product_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scene_context(self) -> "FeedbackEventRequest":
        if self.scene == "product_page" and not self.product_id:
            raise ValueError("product_page feedback requires product_id")
        if self.scene == "cart" and not self.product_ids:
            raise ValueError("cart feedback requires product_ids")
        return self


class FeedbackEventResponse(BaseModel):
    accepted: bool = True
    event_id: str


class SessionProductPreview(BaseModel):
    product_id: str
    name: str
    price: float = Field(default=0.0, ge=0.0)
    category: str = ""
    brand: str = ""


class SessionComparisonProduct(BaseModel):
    product_id: str
    name: str
    price: float = Field(default=0.0, ge=0.0)
    category: str = ""
    brand: str = ""
    stock: int = Field(default=0, ge=0)
    available: bool = True
    highlights: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)


class SessionProductComparison(BaseModel):
    focus: str = ""
    summary: str = ""
    compared_product_ids: list[str] = Field(default_factory=list)
    products: list[SessionComparisonProduct] = Field(default_factory=list)


class ShoppingAgentDetails(BaseModel):
    steps_executed: int = Field(default=0, ge=0)
    workers_called: list[str] = Field(default_factory=list)
    terminal_state: V2TerminalState
    latency_ms: float = Field(default=0.0, ge=0.0)


class RecommendationReadResponse(BaseModel):
    user_id: str
    scene: str = "default"
    products: list[SessionProductPreview] = Field(default_factory=list)
    copies: list[MarketingCopy] = Field(default_factory=list)
    generated_at: datetime | None = None
    stale: bool = False
    pending_refresh: bool = False


class SessionMessageResponse(BaseModel):
    session_id: str
    reply: str = ""
    products: list[SessionProductPreview] = Field(default_factory=list)
    comparisons: list[SessionProductComparison] = Field(default_factory=list)
    copies: list[MarketingCopy] = Field(default_factory=list)
    clarification: str | None = None
    preferences_extracted: list[PreferenceSignal] = Field(default_factory=list)
    recommendation_refresh_triggered: bool = False
    agent_details: ShoppingAgentDetails


class ShoppingManagerTurnResult(BaseModel):
    reply: str = ""
    products: list[SessionProductPreview] = Field(default_factory=list)
    comparisons: list[SessionProductComparison] = Field(default_factory=list)
    copies: list[MarketingCopy] = Field(default_factory=list)
    clarification: str | None = None
    preferences_extracted: list[PreferenceSignal] = Field(default_factory=list)
    recommendation_refresh_triggered: bool = False
    session_memory: dict[str, Any] = Field(default_factory=dict)
    plan: TurnPlan | None = None
    executed_steps: list[str] = Field(default_factory=list)
    skipped_steps: list[str] = Field(default_factory=list)
    projection_event_id: str | None = None
    projection_event_type: str | None = None
    projection_trigger: str | None = None
    agent_details: ShoppingAgentDetails


class TurnTraceTask(BaseModel):
    task_id: str
    record_type: Literal["conversation", "worker", "tool"]
    step: int | None = None
    worker_name: str | None = None
    tool_name: str | None = None
    status: Literal["pending", "running", "completed", "failed"]
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    created_at: datetime
    updated_at: datetime


class TurnTraceProjection(BaseModel):
    requested: bool = False
    event_type: str | None = None
    event_id: str | None = None
    trigger: str | None = None


class TurnTraceResponse(BaseModel):
    session_id: str
    turn_id: str
    user_turn_number: int = Field(ge=1)
    terminal_state: V2TerminalState
    plan: TurnPlan
    tasks: list[TurnTraceTask] = Field(default_factory=list)
    projection: TurnTraceProjection


__all__ = [
    "CreateSessionRequest",
    "CreateSessionResponse",
    "FeedbackEventRequest",
    "FeedbackEventResponse",
    "RecommendationReadRequest",
    "RecommendationReadResponse",
    "SessionMessageRequest",
    "SessionMessageResponse",
    "SessionComparisonProduct",
    "SessionProductComparison",
    "SessionProductPreview",
    "ShoppingAgentDetails",
    "ShoppingManagerTurnResult",
    "TurnTraceProjection",
    "TurnTraceResponse",
    "TurnTraceTask",
    "V2TerminalState",
]
