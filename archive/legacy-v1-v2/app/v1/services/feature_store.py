from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from app.shared.models.domain import BehaviorEvent, RFMScore, UserBehaviorSummary


class FeatureStore:
    def __init__(
        self,
        seed_events: dict[str, list[BehaviorEvent]] | None = None,
        hot_categories: list[str] | None = None,
    ) -> None:
        self._events = seed_events or self._build_seed_events()
        self._hot_categories = hot_categories or ["手机", "耳机", "配件"]

    async def get_user_behavior(self, user_id: str) -> UserBehaviorSummary:
        events = [event.model_copy(deep=True) for event in self._events.get(user_id, [])]
        views = [event for event in events if event.action == "view"]
        clicks = [event for event in events if event.action == "click"]
        purchases = [event for event in events if event.action == "purchase"]

        category_counter = Counter(event.category for event in events)
        average_view_price = self._average_price(views)
        average_purchase_price = self._average_price(purchases)

        return UserBehaviorSummary(
            user_id=user_id,
            views=views,
            clicks=clicks,
            purchases=purchases,
            top_categories=[
                category
                for category, _count in category_counter.most_common(3)
            ],
            average_view_price=average_view_price,
            average_purchase_price=average_purchase_price,
            rfm_score=self._compute_rfm(purchases),
        )

    def get_hot_categories(self) -> list[str]:
        return list(self._hot_categories)

    def _build_seed_events(self) -> dict[str, list[BehaviorEvent]]:
        now = datetime.now(timezone.utc)

        def days_ago(days: int, *, hours: int = 0) -> datetime:
            return now - timedelta(days=days, hours=hours)

        return {
            "u_high_value": [
                BehaviorEvent(
                    action="view",
                    item_id="sku-iphone-16-pro",
                    category="手机",
                    price=7999,
                    occurred_at=days_ago(1),
                ),
                BehaviorEvent(
                    action="click",
                    item_id="sku-ipad-air-m3",
                    category="平板",
                    price=4799,
                    occurred_at=days_ago(2),
                ),
                BehaviorEvent(
                    action="purchase",
                    item_id="sku-iphone-16-pro",
                    category="手机",
                    price=7999,
                    occurred_at=days_ago(3),
                ),
                BehaviorEvent(
                    action="purchase",
                    item_id="sku-watch-ultra-3",
                    category="穿戴",
                    price=5999,
                    occurred_at=days_ago(10),
                ),
                BehaviorEvent(
                    action="view",
                    item_id="sku-airpods-pro-3",
                    category="耳机",
                    price=1899,
                    occurred_at=days_ago(0, hours=12),
                ),
            ],
            "u_price_sensitive": [
                BehaviorEvent(
                    action="view",
                    item_id="sku-gan-65w",
                    category="配件",
                    price=129,
                    occurred_at=days_ago(1),
                ),
                BehaviorEvent(
                    action="view",
                    item_id="sku-anker-140w",
                    category="配件",
                    price=399,
                    occurred_at=days_ago(2),
                ),
                BehaviorEvent(
                    action="click",
                    item_id="sku-xiaomi-pad-7-pro",
                    category="平板",
                    price=2499,
                    occurred_at=days_ago(2),
                ),
                BehaviorEvent(
                    action="purchase",
                    item_id="sku-gan-65w",
                    category="配件",
                    price=129,
                    occurred_at=days_ago(4),
                ),
            ],
            "u_churn_risk": [
                BehaviorEvent(
                    action="view",
                    item_id="sku-u2724d",
                    category="显示器",
                    price=3299,
                    occurred_at=days_ago(42),
                ),
                BehaviorEvent(
                    action="purchase",
                    item_id="sku-u2724d",
                    category="显示器",
                    price=3299,
                    occurred_at=days_ago(65),
                ),
            ],
        }

    def _average_price(self, events: list[BehaviorEvent]) -> float:
        prices = [event.price for event in events if event.price > 0]
        if not prices:
            return 0.0
        return round(sum(prices) / len(prices), 2)

    def _compute_rfm(self, purchases: list[BehaviorEvent]) -> RFMScore:
        if not purchases:
            return RFMScore()

        now = datetime.now(timezone.utc)
        most_recent_purchase = max(event.occurred_at for event in purchases)
        days_since_last_purchase = max((now - most_recent_purchase).days, 0)
        average_purchase = self._average_price(purchases)

        recency = max(0.0, 1.0 - min(days_since_last_purchase / 30.0, 1.0))
        frequency = min(len(purchases) / 3.0, 1.0)
        monetary = min(average_purchase / 5000.0, 1.0)

        return RFMScore(
            recency=round(recency, 3),
            frequency=round(frequency, 3),
            monetary=round(monetary, 3),
        )
