from __future__ import annotations

import json

import pytest

from app.shared.data.product_catalog import ProductCatalog
from app.shared.models.domain import RFMScore, UserProfile, UserSegment
from app.v1.agents.product_rec import ProductRecAgent
from app.v1.models.agent_io import ProductRecOutput
from app.v1.services.llm_service import LLMService


def build_high_value_profile() -> UserProfile:
    return UserProfile(
        user_id="u_high_value",
        segments=[UserSegment.HIGH_VALUE, UserSegment.ACTIVE],
        preferred_categories=["手机", "穿戴", "平板"],
        price_range=(3000, 9000),
        rfm_score=RFMScore(recency=0.9, frequency=0.8, monetary=0.95),
        tags=["高客单价", "手机", "穿戴"],
        cold_start=False,
    )


def build_price_sensitive_profile() -> UserProfile:
    return UserProfile(
        user_id="u_price_sensitive",
        segments=[UserSegment.PRICE_SENSITIVE],
        preferred_categories=["配件", "耳机", "平板"],
        price_range=(0, 500),
        rfm_score=RFMScore(recency=0.7, frequency=0.4, monetary=0.1),
        tags=["价格敏感", "配件", "耳机"],
        cold_start=False,
    )


def build_cold_start_profile() -> UserProfile:
    return UserProfile(
        user_id="u_new",
        segments=[UserSegment.NEW_USER],
        preferred_categories=["手机", "耳机", "配件"],
        price_range=(0, 999),
        rfm_score=RFMScore(),
        tags=["手机", "耳机", "配件"],
        cold_start=True,
    )


class BrokenProductCatalog(ProductCatalog):
    async def get_candidate_products(self, limit: int = 20):  # type: ignore[override]
        raise RuntimeError("catalog unavailable")


class RecordingLLMService(LLMService):
    def __init__(self, response: str | None = None) -> None:
        self.calls = 0
        self.response = response

    async def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self.response is not None:
            return self.response
        return self._mock_response(user_prompt)


@pytest.mark.asyncio
async def test_product_rec_agent_returns_top_10_products_for_profile():
    agent = ProductRecAgent(product_catalog=ProductCatalog())

    result = await agent.run(
        {
            "profile": build_high_value_profile().model_dump(mode="json"),
            "num_items": 10,
        },
    )

    assert result.success is True
    assert result.degraded is False
    assert len(result.data["products"]) == 10

    scores = [item["score"] for item in result.data["products"]]
    assert scores == sorted(scores, reverse=True)
    assert result.data["products"][0]["category"] in {"手机", "穿戴", "平板"}
    assert sum(1 for item in result.data["products"][:5] if item["category"] in {"手机", "穿戴", "平板"}) >= 4


@pytest.mark.asyncio
async def test_product_rec_agent_returns_schema_validated_product_items():
    agent = ProductRecAgent(product_catalog=ProductCatalog())

    result = await agent.run(
        {
            "profile": build_price_sensitive_profile().model_dump(mode="json"),
            "num_items": 5,
        },
    )

    validated = ProductRecOutput(**result.data)
    assert len(validated.products) == 5

    required_fields = {"id", "name", "score", "category"}
    assert all(required_fields.issubset(item.keys()) for item in result.data["products"])


@pytest.mark.asyncio
async def test_product_rec_agent_control_variant_uses_rule_based_ranking_without_llm_call():
    catalog = ProductCatalog()
    llm_service = RecordingLLMService(
        response=json.dumps(
            {
                "ranked_product_ids": [
                    "sku-watch-fit-4",
                    "sku-qcy-a10",
                ],
            },
            ensure_ascii=False,
        ),
    )
    agent = ProductRecAgent(product_catalog=catalog, llm_service=llm_service)
    profile = build_high_value_profile()

    candidates = await catalog.get_candidate_products(limit=20)
    expected_products = agent._personalized_rank(candidates, profile, limit=5)

    result = await agent.run(
        {
            "profile": profile.model_dump(mode="json"),
            "num_items": 5,
            "experiment_name": "rec_strategy",
            "experiment_parameters": {
                "strategy": "rule_based",
                "rerank_enabled": False,
            },
        },
    )

    assert result.success is True
    assert llm_service.calls == 0
    assert [item["id"] for item in result.data["products"]] == [
        product.product_id for product in expected_products
    ]


@pytest.mark.asyncio
async def test_product_rec_agent_treatment_variant_uses_llm_rerank_with_same_schema():
    catalog = ProductCatalog()
    profile = build_high_value_profile()
    baseline_agent = ProductRecAgent(product_catalog=catalog, llm_service=RecordingLLMService())
    candidates = await catalog.get_candidate_products(limit=20)
    baseline_products = baseline_agent._personalized_rank(candidates, profile, limit=20)
    preferred_ids = [
        baseline_products[3].product_id,
        baseline_products[0].product_id,
        baseline_products[1].product_id,
    ]

    llm_service = RecordingLLMService(
        response=json.dumps({"ranked_product_ids": preferred_ids}, ensure_ascii=False),
    )
    agent = ProductRecAgent(product_catalog=catalog, llm_service=llm_service)

    result = await agent.run(
        {
            "profile": profile.model_dump(mode="json"),
            "num_items": 5,
            "experiment_name": "rec_strategy",
            "experiment_parameters": {
                "strategy": "llm_rerank",
                "rerank_enabled": True,
            },
        },
    )

    assert result.success is True
    assert result.degraded is False
    assert llm_service.calls == 1

    validated = ProductRecOutput(**result.data)
    assert len(validated.products) == 5
    assert [item["id"] for item in result.data["products"][:3]] == preferred_ids
    scores = [item["score"] for item in result.data["products"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_product_rec_agent_uses_coarse_ranking_for_cold_start_profile():
    catalog = ProductCatalog()
    agent = ProductRecAgent(product_catalog=catalog)

    result = await agent.run(
        {
            "profile": build_cold_start_profile().model_dump(mode="json"),
            "num_items": 3,
        },
    )

    expected_ids = [product.product_id for product in catalog.get_fallback_products(limit=3)]
    assert [item["id"] for item in result.data["products"]] == expected_ids


@pytest.mark.asyncio
async def test_product_rec_agent_degrades_to_fallback_when_catalog_fails():
    catalog = BrokenProductCatalog()
    agent = ProductRecAgent(product_catalog=catalog)

    result = await agent.run({"num_items": 3})

    assert result.success is False
    assert result.degraded is True
    assert [item["id"] for item in result.data["products"]] == [
        product.product_id for product in catalog.get_fallback_products(limit=3)
    ]
