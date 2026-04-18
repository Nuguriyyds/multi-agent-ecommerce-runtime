from __future__ import annotations

import logging

from pydantic import ValidationError

from app.shared.data.inventory_store import InventoryStore
from app.shared.models.domain import InventoryStatus, Product
from app.v1.agents.base import BaseAgent
from app.v1.models.agent_io import InventoryInput, InventoryOutput

logger = logging.getLogger(__name__)


class InventoryAgent(BaseAgent):
    def __init__(
        self,
        *,
        inventory_store: InventoryStore | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
    ) -> None:
        super().__init__(
            name="inventory",
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
        self.inventory_store = inventory_store or InventoryStore()

    async def execute(self, input_data: dict) -> dict:
        request = InventoryInput(**input_data)
        logger.info("inventory started product_count=%s", len(request.products))

        statuses = await self.inventory_store.get_inventory_statuses(request.products)
        output = self._build_output(
            products=request.products,
            statuses=statuses,
            used_cache=False,
        )
        logger.info(
            "inventory completed checked=%s available=%s low_stock=%s",
            len(output.inventory_status),
            len(output.products),
            sum(1 for status in output.inventory_status if status.low_stock),
        )
        return output.model_dump(mode="json", by_alias=True)

    def default_result(self, input_data: dict) -> dict:
        products = self._coerce_products(input_data)
        statuses = self.inventory_store.get_cached_inventory_statuses(products)
        logger.warning(
            "inventory degrading to cache fallback requested=%s cached=%s",
            len(products),
            len(statuses),
        )
        output = self._build_output(
            products=products,
            statuses=statuses,
            used_cache=True,
        )
        return output.model_dump(mode="json", by_alias=True)

    def _coerce_products(self, input_data: dict) -> list[Product]:
        products: list[Product] = []
        for raw_product in input_data.get("products") or []:
            try:
                products.append(Product.model_validate(raw_product))
            except ValidationError:
                continue
        return products

    def _build_output(
        self,
        *,
        products: list[Product],
        statuses: list[InventoryStatus],
        used_cache: bool,
    ) -> InventoryOutput:
        status_by_product_id = {
            status.product_id: status
            for status in statuses
        }
        filtered_products: list[Product] = []

        for product in products:
            status = status_by_product_id.get(product.product_id)
            if status is None or not status.available:
                continue
            filtered_products.append(
                product.model_copy(
                    update={"stock": status.stock},
                    deep=True,
                ),
            )

        return InventoryOutput(
            products=filtered_products,
            inventory_status=statuses,
            used_cache=used_cache,
        )
