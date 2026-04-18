from __future__ import annotations

from time import perf_counter
from typing import Any, Iterable

from pydantic import ValidationError

from app.shared.data.inventory_store import InventoryStore
from app.shared.models.domain import Product
from app.v2.core.models import WorkerResult, WorkerTask
from app.v2.core.runtime import Worker, WorkerExecutionContext


class InventoryWorker(Worker):
    def __init__(self) -> None:
        super().__init__(
            "inventory_worker",
            allowed_tools={"inventory.check"},
        )

    async def execute(
        self,
        task: WorkerTask,
        context: WorkerExecutionContext,
    ) -> WorkerResult:
        started = perf_counter()
        payload = await context.call_tool(
            "inventory.check",
            {
                "products": list(task.input.get("products") or []),
            },
        )
        return WorkerResult(
            worker_name=self.name,
            payload=dict(payload or {}),
            latency_ms=(perf_counter() - started) * 1000,
        )


def build_inventory_check_handler(inventory_store: InventoryStore):
    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        products = _coerce_products(payload.get("products"))
        try:
            statuses = await inventory_store.get_inventory_statuses(products)
            used_cache = False
        except Exception:  # noqa: BLE001
            statuses = inventory_store.get_cached_inventory_statuses(products)
            used_cache = True

        filtered_products = _filter_available_products(products, statuses)
        return {
            "products": [_dump_product(product) for product in filtered_products],
            "inventory_status": [status.model_dump(mode="json") for status in statuses],
            "used_cache": used_cache,
        }

    return handler


def _filter_available_products(
    products: list[Product],
    statuses: list[Any],
) -> list[Product]:
    status_by_product_id = {
        str(status.product_id): status
        for status in statuses
    }
    filtered: list[Product] = []
    for product in products:
        status = status_by_product_id.get(product.product_id)
        if status is None or not status.available:
            continue
        filtered.append(
            product.model_copy(
                update={"stock": status.stock},
                deep=True,
            ),
        )
    return filtered


def _coerce_products(raw_products: Any) -> list[Product]:
    if not isinstance(raw_products, Iterable) or isinstance(raw_products, (str, bytes, dict)):
        return []

    products: list[Product] = []
    for item in raw_products:
        try:
            products.append(Product.model_validate(item))
        except ValidationError:
            continue
    return products


def _dump_product(product: Product) -> dict[str, Any]:
    return product.model_dump(mode="json")


__all__ = [
    "InventoryWorker",
    "build_inventory_check_handler",
]
