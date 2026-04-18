from __future__ import annotations

import pytest

from app.shared.data.inventory_store import InventoryStore
from app.shared.data.product_catalog import ProductCatalog
from app.v1.agents.inventory import InventoryAgent
from app.v1.models.agent_io import InventoryOutput


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


@pytest.mark.asyncio
async def test_inventory_agent_filters_out_zero_stock_products():
    products = build_products(
        "sku-iphone-16-pro",
        "sku-airpods-pro-3",
        "sku-anker-140w",
    )
    store = InventoryStore(
        seed_inventory={
            "sku-iphone-16-pro": 0,
            "sku-airpods-pro-3": 12,
            "sku-anker-140w": 48,
        },
    )
    agent = InventoryAgent(inventory_store=store)

    result = await agent.run(
        {
            "products": [
                product.model_dump(mode="json", by_alias=True)
                for product in products
            ],
        },
    )

    assert result.success is True
    assert result.degraded is False

    validated = InventoryOutput(**result.data)
    filtered_ids = [product.product_id for product in validated.products]
    assert filtered_ids == ["sku-airpods-pro-3", "sku-anker-140w"]
    assert "sku-iphone-16-pro" not in filtered_ids
    assert any(
        status.product_id == "sku-iphone-16-pro"
        and status.available is False
        and status.stock == 0
        for status in validated.inventory_status
    )


@pytest.mark.asyncio
async def test_inventory_agent_marks_low_stock_warning():
    products = build_products("sku-airpods-pro-3", "sku-qcy-a10")
    store = InventoryStore(
        seed_inventory={
            "sku-airpods-pro-3": 2,
            "sku-qcy-a10": 25,
        },
        safety_stock_threshold=5,
    )
    agent = InventoryAgent(inventory_store=store)

    result = await agent.run(
        {
            "products": [
                product.model_dump(mode="json", by_alias=True)
                for product in products
            ],
        },
    )

    assert result.success is True
    validated = InventoryOutput(**result.data)
    low_stock_status = next(
        status
        for status in validated.inventory_status
        if status.product_id == "sku-airpods-pro-3"
    )

    assert low_stock_status.available is True
    assert low_stock_status.low_stock is True
    assert low_stock_status.stock == 2
    assert low_stock_status.purchase_limit == 1
    assert validated.products[0].product_id == "sku-airpods-pro-3"
    assert validated.products[0].stock == 2


@pytest.mark.asyncio
async def test_inventory_agent_returns_cached_data_when_live_query_fails():
    products = build_products(
        "sku-redmi-k80",
        "sku-watch-fit-4",
        "sku-gan-65w",
    )
    store = InventoryStore(
        seed_inventory={
            "sku-redmi-k80": 6,
            "sku-watch-fit-4": 3,
            "sku-gan-65w": 0,
        },
    )
    agent = InventoryAgent(
        inventory_store=store,
        max_retries=0,
        retry_base_delay=0.01,
    )
    payload = {
        "products": [
            product.model_dump(mode="json", by_alias=True)
            for product in products
        ],
    }

    warm_result = await agent.run(payload)
    warm_output = InventoryOutput(**warm_result.data)
    store.fail_live_queries = True

    result = await agent.run(payload)

    assert result.success is False
    assert result.degraded is True
    assert result.data["used_cache"] is True
    assert result.data["products"]
    assert [item["id"] for item in result.data["products"]] == [
        product.product_id
        for product in warm_output.products
    ]
    assert all(
        status["product_id"] != "sku-gan-65w" or status["available"] is False
        for status in result.data["inventory_status"]
    )
