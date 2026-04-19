from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from app.v3.models import CapabilityDescriptor, CapabilityKind, Observation
from app.v3.registry import ToolProvider

_MARKETING_COPY_GENERATE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "product": {"type": "object"},
        "products": {"type": "array", "items": {"type": "object"}},
        "preferences": {"type": "object"},
        "placement": {"type": "string"},
    },
    "additionalProperties": False,
}

_MARKETING_COPY_GENERATE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "body": {"type": "string"},
        "cta": {"type": "string"},
        "placement": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "body", "cta", "placement", "evidence"],
    "additionalProperties": False,
}


def marketing_copy_generate(
    *,
    product: dict[str, Any] | None = None,
    products: list[dict[str, Any]] | None = None,
    preferences: dict[str, Any] | None = None,
    placement: str = "home_recommendation_card",
) -> dict[str, Any]:
    preference_snapshot = preferences or {}
    candidates = [item for item in (products or []) if isinstance(item, dict)]
    primary = product if isinstance(product, dict) else (candidates[0] if candidates else {})

    name = str(primary.get("name") or "精选推荐")
    brand = str(primary.get("brand") or "优选品牌")
    price = primary.get("price")

    scene = preference_snapshot.get("scene")
    scene_prefix = f"面向{scene}场景，" if isinstance(scene, str) and scene else ""
    budget = preference_snapshot.get("budget")
    budget_text = ""
    if isinstance(budget, dict) and isinstance(budget.get("max"), int):
        budget_text = f"控制在 {budget['max']} 预算内，"

    headline = f"{scene_prefix}{name}"
    body = (
        f"{brand} {name} 结合当前偏好状态生成推荐文案，"
        f"{budget_text}突出舒适度、核心卖点和首页点击吸引力。"
    )
    if price is not None:
        body += f" 当前参考价 ¥{price}。"

    return {
        "headline": headline,
        "body": body,
        "cta": "查看推荐",
        "placement": placement,
        "evidence": [
            f"product:{name}",
            f"brand:{brand}",
            f"placement:{placement}",
            f"preference_keys:{','.join(sorted(str(key) for key in preference_snapshot))}",
        ],
    }


class MarketingCopyGenerateProvider(ToolProvider):
    def __init__(self) -> None:
        super().__init__(
            CapabilityDescriptor(
                name="marketing_copy_generate",
                kind=CapabilityKind.tool,
                input_schema=_MARKETING_COPY_GENERATE_INPUT_SCHEMA,
                output_schema=_MARKETING_COPY_GENERATE_OUTPUT_SCHEMA,
                timeout=2.0,
                permission_tag="marketing.copy.generate",
                description="Generate homepage recommendation or ad copy from products and preferences.",
            )
        )
        self._logger = logging.getLogger(__name__)

    async def invoke(self, args: dict[str, Any]) -> Observation:
        self._logger.info("marketing_copy_generate start args=%s", args)
        product = args.get("product")
        products = args.get("products")
        preferences = args.get("preferences")
        placement = args.get("placement", "home_recommendation_card")

        if product is not None and not isinstance(product, dict):
            raise ValueError("product must be an object")
        if products is not None and not isinstance(products, list):
            raise ValueError("products must be an array")
        if preferences is not None and not isinstance(preferences, dict):
            raise ValueError("preferences must be an object")
        if not isinstance(placement, str):
            raise ValueError("placement must be a string")
        if product is None and not products:
            raise ValueError("either product or products must be provided")

        payload = marketing_copy_generate(
            product=product if isinstance(product, dict) else None,
            products=products if isinstance(products, list) else None,
            preferences=preferences if isinstance(preferences, dict) else None,
            placement=placement,
        )
        observation = Observation(
            observation_id=f"obs-{uuid4().hex[:12]}",
            source=self.name,
            status="ok",
            summary="Generated homepage recommendation copy from product and preference inputs.",
            payload=payload,
            evidence_source=f"tool:{self.name}",
        )
        self._logger.info(
            "marketing_copy_generate success placement=%s observation_id=%s",
            placement,
            observation.observation_id,
        )
        return observation


__all__ = ["MarketingCopyGenerateProvider", "marketing_copy_generate"]
