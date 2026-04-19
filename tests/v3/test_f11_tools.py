from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.v3.models import CapabilityKind
from app.v3.registry import CapabilityRegistry
from app.v3.tools import (
    CatalogSearchProvider,
    InventoryCheckProvider,
    MarketingCopyGenerateProvider,
    PreferenceProfileUpdateProvider,
    ProductCompareProvider,
    catalog_search,
    get_seed_catalog,
    marketing_copy_generate,
    preference_profile_update,
    register_mock_tool_providers,
    seed_counts,
)


@pytest.mark.asyncio
async def test_catalog_search_provider_returns_filtered_candidates_and_rejects_invalid_args() -> None:
    provider = CatalogSearchProvider()

    observation = await provider.invoke(
        {
            "query": "3000 左右的降噪耳机",
            "filters": {
                "category": "earphones",
                "subcategory": "noise_cancelling_headphones",
                "price_min": 2500,
                "price_max": 3500,
                "scene": "commute",
                "exclude_brands": ["Beats"],
                "limit": 4,
            },
        }
    )

    results = observation.payload["results"]
    assert observation.source == "catalog_search"
    assert observation.evidence_source == "tool:catalog_search"
    assert observation.payload["total"] == 4
    assert {item["name"] for item in results} == {
        "Sony WH-1000XM5",
        "Bose QuietComfort Ultra Headphones",
        "Sennheiser Momentum 4 Wireless",
        "Apple AirPods Max",
    }

    with pytest.raises(ValidationError):
        await provider.invoke({"query": "   "})


@pytest.mark.asyncio
async def test_inventory_check_provider_reports_low_stock_and_rejects_unknown_sku() -> None:
    provider = InventoryCheckProvider()

    observation = await provider.invoke({"sku": "EAR-NTG-EAR2024"})

    assert observation.payload["sku"] == "EAR-NTG-EAR2024"
    assert observation.payload["status"] == "low_stock"
    assert observation.payload["quantity"] == 3
    assert observation.payload["is_available"] is True

    with pytest.raises(LookupError, match="Unknown SKU"):
        await provider.invoke({"sku": "missing-sku"})


@pytest.mark.asyncio
async def test_product_compare_provider_returns_summary_and_rejects_invalid_dimensions() -> None:
    provider = ProductCompareProvider()

    observation = await provider.invoke(
        {
            "sku_a": "EAR-SON-WH1000XM5",
            "sku_b": "EAR-BOS-QCUH",
            "dimensions": ["price", "battery", "noise_cancel", "weight"],
        }
    )

    dimensions = {item["dimension"]: item for item in observation.payload["dimensions"]}
    assert observation.source == "product_compare"
    assert observation.evidence_source == "tool:product_compare"
    assert observation.payload["summary"] == "Sony WH-1000XM5 leads this comparison."
    assert dimensions["price"]["winner"] == "sku_a"
    assert dimensions["battery"]["winner"] == "sku_a"
    assert dimensions["noise_cancel"]["winner"] == "sku_b"

    with pytest.raises(ValidationError):
        await provider.invoke(
            {
                "sku_a": "EAR-SON-WH1000XM5",
                "sku_b": "EAR-BOS-QCUH",
                "dimensions": ["latency"],
            }
        )


def test_seed_catalog_covers_reference_dialogues_and_tools_register_with_registry() -> None:
    catalog = get_seed_catalog()
    counts = seed_counts()

    assert len(catalog) >= 30
    assert counts["phone"] >= 15
    assert counts["earphones"] >= 15
    assert {product.name for product in catalog} >= {
        "Sony WH-1000XM5",
        "Bose QuietComfort Ultra Headphones",
        "Sennheiser Momentum 4 Wireless",
        "Apple AirPods Max",
        "Apple AirPods Pro 2",
    }

    apple_results = catalog_search("Apple", {"limit": 6}, catalog=catalog)
    assert {product.category.value for product in apple_results} == {"phone", "earphones"}

    registry = CapabilityRegistry()
    register_mock_tool_providers(registry, catalog=catalog)

    assert [descriptor.name for descriptor in registry.list(CapabilityKind.tool)] == [
        "catalog_search",
        "inventory_check",
        "product_compare",
        "preference_profile_update",
        "marketing_copy_generate",
    ]


@pytest.mark.asyncio
async def test_preference_profile_update_provider_returns_session_scoped_auditable_payload() -> None:
    provider = PreferenceProfileUpdateProvider()

    observation = await provider.invoke(
        {
            "preferences": {
                "scene": "commute",
                "budget": {"max": 3000, "currency": "CNY"},
            },
            "feedback_signal": "interested",
            "context": {"placement": "home_recommendation_card"},
        }
    )

    assert observation.source == "preference_profile_update"
    assert observation.evidence_source == "tool:preference_profile_update"
    assert observation.payload["write_policy"] == "requires_user_confirmation"
    assert len(observation.payload["profile_updates"]) == 2
    assert "durable memory" in " ".join(observation.payload["audit_notes"]).lower()

    direct = preference_profile_update(
        preferences={"scene": "commute"},
        feedback_signal="explicit_confirmed",
    )
    assert direct["write_policy"] == "session_only"


@pytest.mark.asyncio
async def test_marketing_copy_generate_provider_returns_structured_copy() -> None:
    provider = MarketingCopyGenerateProvider()
    product = get_seed_catalog()[0].model_dump(mode="json")

    observation = await provider.invoke(
        {
            "product": product,
            "preferences": {"scene": "commute", "budget": {"max": 3000}},
            "placement": "home_recommendation_card",
        }
    )

    assert observation.source == "marketing_copy_generate"
    assert observation.evidence_source == "tool:marketing_copy_generate"
    assert observation.payload["placement"] == "home_recommendation_card"
    assert observation.payload["headline"]
    assert observation.payload["body"]
    assert observation.payload["cta"] == "查看推荐"

    direct = marketing_copy_generate(
        product=product,
        preferences={"scene": "commute"},
    )
    assert "placement:home_recommendation_card" in direct["evidence"]
