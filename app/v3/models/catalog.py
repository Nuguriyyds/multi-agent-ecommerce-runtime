from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import V3Model

ComparableValue = str | int | float | bool | None


class ProductCategory(str, Enum):
    phone = "phone"
    earphones = "earphones"


class InventoryAvailability(str, Enum):
    in_stock = "in_stock"
    low_stock = "low_stock"
    out_of_stock = "out_of_stock"


class ComparisonDimension(str, Enum):
    price = "price"
    battery = "battery"
    noise_cancel = "noise_cancel"
    weight = "weight"
    warranty = "warranty"
    brand = "brand"
    camera = "camera"
    charging = "charging"


class Product(V3Model):
    sku: str
    name: str
    brand: str
    category: ProductCategory
    subcategory: str
    price: int = Field(ge=0)
    currency: str = Field(default="CNY")
    rating: float = Field(ge=0.0, le=5.0)
    description: str
    scene_tags: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    features: dict[str, ComparableValue] = Field(default_factory=dict)
    stock: int = Field(ge=0)
    low_stock_threshold: int = Field(default=5, ge=1)

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("sku must not be empty")
        return normalized

    @field_validator("scene_tags", "tags", "aliases")
    @classmethod
    def strip_text_lists(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]


class CatalogSearchFilters(V3Model):
    category: ProductCategory | None = None
    subcategory: str | None = None
    brand: str | None = None
    exclude_brands: list[str] = Field(default_factory=list)
    scene: str | None = None
    price_min: int | None = Field(default=None, ge=0)
    price_max: int | None = Field(default=None, ge=0)
    min_rating: float | None = Field(default=None, ge=0.0, le=5.0)
    tags: list[str] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=20)

    @field_validator("subcategory", "brand", "scene")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("exclude_brands", "tags")
    @classmethod
    def strip_filter_lists(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]

    @model_validator(mode="after")
    def validate_price_range(self) -> "CatalogSearchFilters":
        if (
            self.price_min is not None
            and self.price_max is not None
            and self.price_min > self.price_max
        ):
            raise ValueError("price_min must be less than or equal to price_max")
        return self


class CatalogSearchRequest(V3Model):
    query: str = Field(min_length=1)
    filters: CatalogSearchFilters = Field(default_factory=CatalogSearchFilters)

    @field_validator("query")
    @classmethod
    def strip_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be empty")
        return normalized


class InventoryCheckRequest(V3Model):
    sku: str = Field(min_length=1)

    @field_validator("sku")
    @classmethod
    def normalize_sku(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("sku must not be empty")
        return normalized


class InventoryStatus(V3Model):
    sku: str
    product_name: str
    status: InventoryAvailability
    quantity: int = Field(ge=0)
    is_available: bool
    low_stock_threshold: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_availability(self) -> "InventoryStatus":
        if self.status is InventoryAvailability.out_of_stock and self.is_available:
            raise ValueError("out_of_stock items cannot be marked available")
        return self


class ProductCompareRequest(V3Model):
    sku_a: str = Field(min_length=1)
    sku_b: str = Field(min_length=1)
    dimensions: list[ComparisonDimension] = Field(default_factory=list)

    @field_validator("sku_a", "sku_b")
    @classmethod
    def normalize_skus(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized:
            raise ValueError("sku must not be empty")
        return normalized

    @field_validator("dimensions")
    @classmethod
    def require_dimensions(cls, value: list[ComparisonDimension]) -> list[ComparisonDimension]:
        if not value:
            raise ValueError("dimensions must contain at least one dimension")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_distinct_products(self) -> "ProductCompareRequest":
        if self.sku_a == self.sku_b:
            raise ValueError("sku_a and sku_b must be different products")
        return self


class ComparisonDimensionResult(V3Model):
    dimension: ComparisonDimension
    value_a: ComparableValue = None
    value_b: ComparableValue = None
    winner: Literal["sku_a", "sku_b", "tie", "not_applicable"] = "tie"
    rationale: str


class ComparisonResult(V3Model):
    sku_a: str
    sku_b: str
    product_a_name: str
    product_b_name: str
    category: ProductCategory | None = None
    dimensions: list[ComparisonDimensionResult] = Field(default_factory=list)
    summary: str
