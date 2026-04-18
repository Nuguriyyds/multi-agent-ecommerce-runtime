from __future__ import annotations

import asyncio
import json

import pytest

from app.shared.data.product_catalog import ProductCatalog
from app.shared.models.domain import RFMScore, UserProfile, UserSegment
from app.v1.agents.marketing_copy import MarketingCopyAgent
from app.v1.models.agent_io import MarketingCopyOutput
from app.v1.services.llm_service import LLMService


def build_products(*product_ids: str):
    catalog_products = ProductCatalog().get_fallback_products(limit=100)
    catalog_by_id = {
        product.product_id: product
        for product in catalog_products
    }
    return [
        catalog_by_id[product_id].model_copy(deep=True)
        for product_id in product_ids
    ]


def build_high_value_profile() -> UserProfile:
    return UserProfile(
        user_id="u_high_value",
        segments=[UserSegment.HIGH_VALUE, UserSegment.ACTIVE],
        preferred_categories=["手机", "穿戴"],
        price_range=(3000, 9000),
        rfm_score=RFMScore(recency=0.9, frequency=0.8, monetary=0.95),
        tags=["高客单价", "旗舰"],
        cold_start=False,
    )


def build_price_sensitive_profile() -> UserProfile:
    return UserProfile(
        user_id="u_price_sensitive",
        segments=[UserSegment.PRICE_SENSITIVE],
        preferred_categories=["配件", "耳机"],
        price_range=(0, 500),
        rfm_score=RFMScore(recency=0.7, frequency=0.4, monetary=0.1),
        tags=["价格敏感", "通勤"],
        cold_start=False,
    )


class RecordingLLMService(LLMService):
    def __init__(self, response: str | None = None, delay: float = 0.0) -> None:
        self.calls = 0
        self.response = response
        self.delay = delay

    async def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.response is not None:
            return self.response
        return self._mock_response(user_prompt)


@pytest.mark.asyncio
async def test_marketing_copy_agent_generates_different_styles_for_segments():
    llm_service = RecordingLLMService()
    agent = MarketingCopyAgent(llm_service=llm_service)
    products = build_products("sku-iphone-16-pro", "sku-gan-65w")

    high_value_result = await agent.run(
        {
            "profile": build_high_value_profile().model_dump(mode="json"),
            "products": [product.model_dump(mode="json", by_alias=True) for product in products],
        },
    )
    price_sensitive_result = await agent.run(
        {
            "profile": build_price_sensitive_profile().model_dump(mode="json"),
            "products": [product.model_dump(mode="json", by_alias=True) for product in products],
        },
    )

    high_value_output = MarketingCopyOutput(**high_value_result.data)
    price_sensitive_output = MarketingCopyOutput(**price_sensitive_result.data)

    assert high_value_result.success is True
    assert price_sensitive_result.success is True
    assert high_value_output.template_used == "high_value"
    assert price_sensitive_output.template_used == "price_sensitive"
    assert high_value_output.copies[0].copy_text != price_sensitive_output.copies[0].copy_text
    assert "高端质感" in high_value_output.copies[0].copy_text
    assert "划算" in price_sensitive_output.copies[0].copy_text
    assert llm_service.calls == 2


@pytest.mark.asyncio
async def test_marketing_copy_agent_filters_sensitive_words():
    products = build_products("sku-redmi-k80")
    llm_service = RecordingLLMService(
        response=json.dumps(
            {
                "copies": [
                    {
                        "product_id": "sku-redmi-k80",
                        "copy_text": "这是最好、100%值得入手的机型。",
                    },
                ],
            },
            ensure_ascii=False,
        ),
    )
    agent = MarketingCopyAgent(llm_service=llm_service)

    result = await agent.run(
        {
            "profile": build_price_sensitive_profile().model_dump(mode="json"),
            "products": [product.model_dump(mode="json", by_alias=True) for product in products],
        },
    )

    assert result.success is True
    assert result.degraded is False
    assert result.data["copies"][0]["copy_text"] == "这是更合适、多重值得入手的机型。"


@pytest.mark.asyncio
async def test_marketing_copy_agent_returns_default_descriptions_on_timeout():
    products = build_products("sku-airpods-pro-3", "sku-watch-fit-4")
    llm_service = RecordingLLMService(delay=0.05)
    agent = MarketingCopyAgent(
        llm_service=llm_service,
        timeout=0.01,
        max_retries=2,
    )

    result = await agent.run(
        {
            "profile": build_high_value_profile().model_dump(mode="json"),
            "products": [product.model_dump(mode="json", by_alias=True) for product in products],
        },
    )

    assert result.success is False
    assert result.degraded is True
    assert result.attempts == 1
    assert result.data["template_used"] == "high_value"
    assert result.data["copies"] == [
        {
            "product_id": product.product_id,
            "copy_text": product.description,
        }
        for product in products
    ]
    assert llm_service.calls == 1
