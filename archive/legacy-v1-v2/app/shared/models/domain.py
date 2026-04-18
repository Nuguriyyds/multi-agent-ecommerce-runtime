from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UserSegment(str, Enum):
    NEW_USER = "new_user"
    ACTIVE = "active"
    HIGH_VALUE = "high_value"
    PRICE_SENSITIVE = "price_sensitive"
    CHURN_RISK = "churn_risk"


class RFMScore(BaseModel):
    recency: float = Field(default=0.0, ge=0.0, le=1.0)
    frequency: float = Field(default=0.0, ge=0.0, le=1.0)
    monetary: float = Field(default=0.0, ge=0.0, le=1.0)


class UserProfile(BaseModel):
    user_id: str
    segments: list[UserSegment] = Field(default_factory=list)
    preferred_categories: list[str] = Field(default_factory=list)
    price_range: tuple[float, float] = (0.0, 10000.0)
    rfm_score: RFMScore = Field(default_factory=RFMScore)
    tags: list[str] = Field(default_factory=list)
    cold_start: bool = False


class BehaviorEvent(BaseModel):
    action: str
    item_id: str
    category: str
    price: float = 0.0
    occurred_at: datetime


class UserBehaviorSummary(BaseModel):
    user_id: str
    views: list[BehaviorEvent] = Field(default_factory=list)
    clicks: list[BehaviorEvent] = Field(default_factory=list)
    purchases: list[BehaviorEvent] = Field(default_factory=list)
    top_categories: list[str] = Field(default_factory=list)
    average_view_price: float = 0.0
    average_purchase_price: float = 0.0
    rfm_score: RFMScore = Field(default_factory=RFMScore)

    @property
    def has_history(self) -> bool:
        return bool(self.views or self.clicks or self.purchases)


class Product(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    product_id: str = Field(alias="id")
    name: str
    category: str
    price: float = Field(default=0.0, ge=0.0)
    description: str = ""
    brand: str = ""
    stock: int = Field(default=0, ge=0)
    tags: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0)
    image_url: str = ""


class InventoryStatus(BaseModel):
    product_id: str
    available: bool = True
    stock: int = Field(default=0, ge=0)
    low_stock: bool = False
    purchase_limit: int | None = Field(default=None, ge=1)


class MarketingCopy(BaseModel):
    product_id: str
    copy_text: str = Field(min_length=1)


class AgentExecutionDetail(BaseModel):
    success: bool = True
    degraded: bool = False
    attempts: int = Field(default=1, ge=1)
    error: str = ""
    latency_ms: float = Field(default=0.0, ge=0.0)


class AgentMetricSnapshot(BaseModel):
    calls: int = Field(default=0, ge=0)
    avg_latency_ms: float = Field(default=0.0, ge=0.0)
    error_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class MetricsResponse(BaseModel):
    agents: dict[str, AgentMetricSnapshot] = Field(default_factory=dict)


class RecommendationRequest(BaseModel):
    user_id: str
    scene: str = "default"
    num_items: int = Field(default=10, ge=1, le=20)
    experiment_name: str = ""
    experiment_parameters: dict[str, Any] = Field(default_factory=dict)


class RecommendationResponse(BaseModel):
    request_id: str
    user_id: str
    profile: UserProfile | None = None
    recommendations: list[Product] = Field(default_factory=list)
    copies: list[MarketingCopy] = Field(default_factory=list)
    inventory_status: list[InventoryStatus] = Field(default_factory=list)
    experiment_group: str = ""
    agent_details: dict[str, AgentExecutionDetail] = Field(default_factory=dict)
    latency_ms: float = Field(default=0.0, ge=0.0)
