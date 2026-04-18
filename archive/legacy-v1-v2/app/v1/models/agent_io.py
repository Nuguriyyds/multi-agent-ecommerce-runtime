from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.shared.models.domain import InventoryStatus, MarketingCopy, Product, UserProfile, UserSegment


class UserProfileInput(BaseModel):
    user_id: str


class UserProfileOutput(BaseModel):
    profile: UserProfile
    segment: UserSegment
    price_range: tuple[float, float]
    tags: list[str] = Field(default_factory=list)
    cold_start: bool = False

    @classmethod
    def from_profile(cls, profile: UserProfile) -> "UserProfileOutput":
        segment = profile.segments[0] if profile.segments else UserSegment.NEW_USER
        return cls(
            profile=profile,
            segment=segment,
            price_range=profile.price_range,
            tags=profile.tags,
            cold_start=profile.cold_start,
        )


class ProductRecInput(BaseModel):
    profile: UserProfile | None = None
    num_items: int = Field(default=10, ge=1, le=50)
    candidate_pool_size: int = Field(default=20, ge=1, le=50)
    experiment_name: str = ""
    experiment_parameters: dict[str, Any] = Field(default_factory=dict)


class ProductRecOutput(BaseModel):
    products: list[Product] = Field(default_factory=list)


class InventoryInput(BaseModel):
    products: list[Product] = Field(default_factory=list)


class InventoryOutput(BaseModel):
    products: list[Product] = Field(default_factory=list)
    inventory_status: list[InventoryStatus] = Field(default_factory=list)
    used_cache: bool = False


class MarketingCopyInput(BaseModel):
    profile: UserProfile | None = None
    products: list[Product] = Field(default_factory=list)


class MarketingCopyOutput(BaseModel):
    copies: list[MarketingCopy] = Field(default_factory=list)
    template_used: str = ""
