from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.shared.data.inventory_store import InventoryStore
from app.shared.data.product_catalog import ProductCatalog
from app.v2 import (
    ComparisonWorker,
    CopyWorker,
    PromptRegistry,
    PromptTemplate,
    SessionMessageRequest,
    ToolRegistry,
    ToolSpec,
    V2SessionService,
    WorkerTask,
    build_copy_generate_handler,
    build_product_compare_handler,
)
from app.v2.workers.inventory import build_inventory_check_handler


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_comparison_copy"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir()
    return path


def _catalog_by_id():
    return {product.product_id: product for product in ProductCatalog().get_fallback_products(limit=100)}


@pytest.mark.asyncio
async def test_v2_comparison_worker_returns_structured_comparison_results():
    catalog = _catalog_by_id()
    products = [catalog["sku-iphone-16-pro"], catalog["sku-huawei-mate-70-pro"], catalog["sku-xiaomi-15"]]

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="inventory.check", description="read inventory availability", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"),
        build_inventory_check_handler(InventoryStore()),
    )
    registry.register(
        ToolSpec(name="product.compare", description="compare products", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"),
        build_product_compare_handler(),
    )

    worker = ComparisonWorker()
    result = await worker.run(
        WorkerTask(task_id="task_compare_1", worker_name="comparison_worker", step=1, intent="compare_products", input={"focus": "budget", "products": [product.model_dump(mode="json") for product in products]}),
        registry,
        manager_name="shopping",
        session_id="sess_compare_1",
    )

    comparisons = result.payload["comparisons"]
    assert result.payload["focus"] == "budget"
    assert result.payload["prompt_render"]
    assert len(comparisons) == 1
    assert comparisons[0]["summary"]
    assert len(comparisons[0]["products"]) == 3


@pytest.mark.asyncio
async def test_v2_copy_worker_uses_prompt_registry_backed_generation():
    catalog = _catalog_by_id()
    prompt_registry = PromptRegistry()
    prompt_registry.register(
        PromptTemplate(
            name="copy.generate",
            version="v1",
            template="PROMPT::{audience}::{product_name}::{selling_points}",
            variables_schema={"type": "object", "required": ["audience", "product_name", "selling_points"]},
        ),
    )

    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="copy.generate", description="generate copy", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"),
        build_copy_generate_handler(prompt_registry=prompt_registry),
    )

    worker = CopyWorker()
    result = await worker.run(
        WorkerTask(task_id="task_copy_1", worker_name="copy_worker", step=1, intent="generate_copy", input={"message": "budget 3000 office", "scene": "default", "preferences": {"budget": "3000", "use_case": "办公"}, "products": [catalog["sku-redmi-k80"].model_dump(mode="json")]}),
        registry,
        manager_name="shopping",
        session_id="sess_copy_1",
    )

    assert result.payload["audience"] == "办公用户"
    assert result.payload["prompt_renders"]["sku-redmi-k80"].startswith("PROMPT::办公用户::Redmi K80::")
    assert result.payload["copies"] == [{"product_id": "sku-redmi-k80", "copy_text": result.payload["copies"][0]["copy_text"]}]
    assert "办公" in result.payload["copies"][0]["copy_text"]


@pytest.mark.asyncio
async def test_v2_manager_routes_comparison_without_copy_in_v22():
    service = V2SessionService(_workspace_tempdir() / "v2.sqlite3")
    session_id = service.create_session("u_f08").session_id

    response = await service.handle_message(
        session_id,
        SessionMessageRequest(message="compare this", scene="product_page", scene_context={"product_id": "sku-iphone-16-pro"}),
    )

    assert response.products
    assert response.comparisons
    assert response.copies == []
    assert response.agent_details.terminal_state == "reply_ready"
    assert response.agent_details.steps_executed == 4
    assert response.agent_details.workers_called == [
        "preference_worker",
        "catalog_worker",
        "inventory_worker",
        "comparison_worker",
    ]
    assert response.comparisons[0].summary
