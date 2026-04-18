from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.shared.data.inventory_store import InventoryStore
from app.shared.data.product_catalog import ProductCatalog
from app.shared.models.domain import InventoryStatus, MarketingCopy, Product, RFMScore, UserProfile, UserSegment
from app.v1.agents.inventory import InventoryAgent
from app.v1.agents.marketing_copy import MarketingCopyAgent
from app.v1.agents.product_rec import ProductRecAgent
from app.v1.agents.user_profile import UserProfileAgent
from app.v1.models.agent_result import AgentResult
from app.v1.orchestrator.supervisor import Supervisor
from app.v1.services.feature_store import FeatureStore
from app.v1.services.llm_service import LLMService


def build_products(*product_ids: str) -> list[Product]:
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
        preferred_categories=["手机", "穿戴", "平板"],
        price_range=(3000, 9000),
        rfm_score=RFMScore(recency=0.9, frequency=0.8, monetary=0.95),
        tags=["高客单价", "手机", "穿戴"],
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


class DelayedAgent:
    def __init__(
        self,
        *,
        name: str,
        data: dict,
        delay: float,
        success: bool = True,
        degraded: bool = False,
    ) -> None:
        self.name = name
        self.data = data
        self.delay = delay
        self.success = success
        self.degraded = degraded

    async def run(self, input_data: dict) -> AgentResult:
        await asyncio.sleep(self.delay)
        return AgentResult(
            success=self.success,
            degraded=self.degraded,
            data=self.data,
            agent_name=self.name,
            attempts=1,
            latency_ms=round(self.delay * 1000, 2),
        )


@pytest.mark.asyncio
async def test_supervisor_returns_complete_recommendation_response():
    supervisor = Supervisor()

    response = await supervisor.recommend({"user_id": "u_high_value", "num_items": 5})

    assert response.user_id == "u_high_value"
    assert response.profile is not None
    assert response.profile.cold_start is False
    assert len(response.recommendations) == 5
    assert len(response.copies) == 5
    assert response.inventory_status
    assert {copy.product_id for copy in response.copies} == {
        product.product_id for product in response.recommendations
    }
    assert set(response.agent_details) == {
        "user_profile",
        "product_rec_coarse",
        "product_rec_ranked",
        "inventory",
        "marketing_copy",
    }


@pytest.mark.asyncio
async def test_supervisor_degrades_marketing_copy_without_affecting_other_results():
    slow_llm = RecordingLLMService(delay=0.05)
    supervisor = Supervisor(
        user_profile_agent=UserProfileAgent(
            feature_store=FeatureStore(),
            llm_service=RecordingLLMService(),
        ),
        product_rec_agent=ProductRecAgent(product_catalog=ProductCatalog()),
        inventory_agent=InventoryAgent(inventory_store=InventoryStore()),
        marketing_copy_agent=MarketingCopyAgent(
            llm_service=slow_llm,
            timeout=0.01,
            max_retries=0,
        ),
    )

    response = await supervisor.recommend({"user_id": "u_high_value", "num_items": 4})

    assert len(response.recommendations) == 4
    assert response.agent_details["marketing_copy"].degraded is True
    assert response.agent_details["inventory"].degraded is False
    assert response.agent_details["product_rec_ranked"].degraded is False
    assert response.copies == [
        MarketingCopy(
            product_id=product.product_id,
            copy_text=product.description,
        )
        for product in response.recommendations
    ]
    assert slow_llm.calls == 1


@pytest.mark.asyncio
async def test_supervisor_uses_inventory_cache_fallback_without_returning_unvalidated_products():
    catalog = ProductCatalog()
    store = InventoryStore(
        seed_inventory={
            "sku-iphone-16-pro": 0,
            "sku-huawei-mate-70-pro": 3,
            "sku-airpods-pro-3": 8,
            "sku-ipad-air-m3": 5,
            "sku-watch-ultra-3": 4,
        },
    )
    supervisor = Supervisor(
        product_rec_agent=ProductRecAgent(product_catalog=catalog),
        inventory_agent=InventoryAgent(
            inventory_store=store,
            max_retries=0,
            retry_base_delay=0.01,
        ),
    )

    warm_response = await supervisor.recommend({"user_id": "u_high_value", "num_items": 4})
    assert "sku-iphone-16-pro" not in [product.product_id for product in warm_response.recommendations]

    store.fail_live_queries = True
    response = await supervisor.recommend({"user_id": "u_high_value", "num_items": 4})

    available_ids = {
        status.product_id
        for status in response.inventory_status
        if status.available
    }
    returned_ids = [product.product_id for product in response.recommendations]

    assert response.agent_details["inventory"].degraded is True
    assert returned_ids
    assert all(product_id in available_ids for product_id in returned_ids)
    assert "sku-iphone-16-pro" not in returned_ids


@pytest.mark.asyncio
async def test_supervisor_parallel_phases_are_bound_by_slowest_agent():
    coarse_products = build_products("sku-iphone-16-pro", "sku-huawei-mate-70-pro", "sku-airpods-pro-3")
    final_products = build_products("sku-huawei-mate-70-pro", "sku-airpods-pro-3")
    profile = build_high_value_profile()
    copies = [
        MarketingCopy(product_id=product.product_id, copy_text=product.description)
        for product in final_products
    ]
    inventory_status = [
        InventoryStatus(
            product_id=product.product_id,
            available=True,
            stock=max(product.stock, 1),
        )
        for product in coarse_products
    ]

    supervisor = Supervisor(
        user_profile_agent=DelayedAgent(
            name="user_profile",
            delay=0.06,
            data={
                "profile": profile.model_dump(mode="json"),
                "segment": "high_value",
                "price_range": [3000, 9000],
                "tags": ["高客单价", "手机"],
                "cold_start": False,
            },
        ),
        product_rec_agent=DelayedAgent(
            name="product_rec",
            delay=0.05,
            data={
                "products": [
                    product.model_dump(mode="json", by_alias=True)
                    for product in final_products
                ],
            },
        ),
        inventory_agent=DelayedAgent(
            name="inventory",
            delay=0.02,
            data={
                "products": [
                    product.model_dump(mode="json", by_alias=True)
                    for product in final_products
                ],
                "inventory_status": [
                    status.model_dump(mode="json")
                    for status in inventory_status
                ],
                "used_cache": False,
            },
        ),
        marketing_copy_agent=DelayedAgent(
            name="marketing_copy",
            delay=0.01,
            data={
                "copies": [copy.model_dump(mode="json") for copy in copies],
                "template_used": "high_value",
            },
        ),
    )

    start = time.perf_counter()
    response = await supervisor.recommend({"user_id": "u_high_value", "num_items": 2})
    elapsed = time.perf_counter() - start

    assert elapsed < 0.15
    assert response.latency_ms < 150
    assert [product.product_id for product in response.recommendations] == [
        "sku-huawei-mate-70-pro",
        "sku-airpods-pro-3",
    ]


@pytest.mark.asyncio
async def test_supervisor_passes_treatment_experiment_parameters_to_product_rerank():
    rerank_llm = RecordingLLMService(
        response=json.dumps(
            {
                "ranked_product_ids": [
                    "sku-watch-ultra-3",
                    "sku-iphone-16-pro",
                    "sku-huawei-mate-70-pro",
                ],
            },
            ensure_ascii=False,
        ),
    )
    supervisor = Supervisor(
        product_rec_agent=ProductRecAgent(
            product_catalog=ProductCatalog(),
            llm_service=rerank_llm,
        ),
    )

    response = await supervisor.recommend(
        {
            "user_id": "u_high_value",
            "num_items": 3,
            "experiment_name": "rec_strategy",
            "experiment_parameters": {
                "strategy": "llm_rerank",
                "rerank_enabled": True,
            },
        },
    )

    assert rerank_llm.calls == 1
    assert len(response.recommendations) == 3
