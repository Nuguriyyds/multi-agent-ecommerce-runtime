from __future__ import annotations

import logging

from app.shared.data.product_catalog import ProductCatalog
from app.shared.models.domain import InventoryStatus, Product

logger = logging.getLogger(__name__)

HOT_ITEM_TAGS = {"旗舰", "高客单价", "新品"}


class InventoryStore:
    def __init__(
        self,
        seed_inventory: dict[str, int] | None = None,
        *,
        safety_stock_threshold: int = 5,
        fail_live_queries: bool = False,
    ) -> None:
        seed_products = ProductCatalog().get_fallback_products(limit=100)
        self._inventory = {
            product.product_id: max(
                0,
                int(seed_inventory.get(product.product_id, product.stock))
                if seed_inventory is not None
                else product.stock,
            )
            for product in seed_products
        }
        self.safety_stock_threshold = max(0, safety_stock_threshold)
        self.fail_live_queries = fail_live_queries
        self._cache = {
            product.product_id: self._build_status(
                product,
                self._inventory.get(product.product_id, product.stock),
            )
            for product in seed_products
        }

    async def get_inventory_statuses(self, products: list[Product]) -> list[InventoryStatus]:
        if self.fail_live_queries:
            raise RuntimeError("inventory backend unavailable")

        statuses = [
            self._build_status(
                product,
                self._inventory.get(product.product_id, product.stock),
            )
            for product in products
        ]
        self._update_cache(statuses)
        logger.info("inventory_store live lookup count=%s", len(statuses))
        return [status.model_copy(deep=True) for status in statuses]

    def get_cached_inventory_statuses(self, products: list[Product]) -> list[InventoryStatus]:
        statuses: list[InventoryStatus] = []
        for product in products:
            cached_status = self._cache.get(product.product_id)
            if cached_status is None:
                continue
            statuses.append(cached_status.model_copy(deep=True))

        logger.warning("inventory_store cache fallback count=%s", len(statuses))
        return statuses

    def _update_cache(self, statuses: list[InventoryStatus]) -> None:
        for status in statuses:
            self._cache[status.product_id] = status.model_copy(deep=True)

    def _build_status(self, product: Product, stock: int) -> InventoryStatus:
        normalized_stock = max(0, int(stock))
        available = normalized_stock > 0
        low_stock = available and normalized_stock <= self.safety_stock_threshold

        return InventoryStatus(
            product_id=product.product_id,
            available=available,
            stock=normalized_stock,
            low_stock=low_stock,
            purchase_limit=self._calculate_purchase_limit(product, normalized_stock),
        )

    def _calculate_purchase_limit(self, product: Product, stock: int) -> int | None:
        if stock <= 0:
            return None
        if stock <= self.safety_stock_threshold:
            return 1
        if stock <= self.safety_stock_threshold * 2 and HOT_ITEM_TAGS.intersection(product.tags):
            return 2
        return None
