from __future__ import annotations

from statistics import median
from time import perf_counter
from typing import Any, Iterable

from pydantic import ValidationError

from app.shared.models.domain import InventoryStatus, Product
from app.v2.api.schemas import SessionComparisonProduct, SessionProductComparison
from app.v2.core.models import WorkerResult, WorkerTask
from app.v2.core.prompts import PromptRegistry, build_default_prompt_registry
from app.v2.core.runtime import Worker, WorkerExecutionContext


class ComparisonWorker(Worker):
    def __init__(self, *, prompt_registry: PromptRegistry | None = None) -> None:
        super().__init__(
            "comparison_worker",
            allowed_tools={"product.compare", "inventory.check"},
        )
        self._prompt_registry = prompt_registry or build_default_prompt_registry()

    async def execute(
        self,
        task: WorkerTask,
        context: WorkerExecutionContext,
    ) -> WorkerResult:
        started = perf_counter()
        products = _coerce_products(task.input.get("products"))
        focus = _resolve_comparison_focus(
            task.input.get("focus"),
            preferences=dict(task.input.get("preferences") or {}),
            message=str(task.input.get("message", "")),
        )
        prompt_render = self._prompt_registry.render(
            "comparison.summarize",
            variables={
                "focus": focus,
                "products": _comparison_prompt_products(products),
            },
        )

        inventory_payload = await context.call_tool(
            "inventory.check",
            {
                "products": [product.model_dump(mode="json") for product in products],
            },
        )
        compare_payload = await context.call_tool(
            "product.compare",
            {
                "products": [product.model_dump(mode="json") for product in products],
                "inventory_status": list(inventory_payload.get("inventory_status") or []),
                "focus": focus,
                "prompt_render": prompt_render,
            },
        )
        return WorkerResult(
            worker_name=self.name,
            payload=dict(compare_payload or {}),
            latency_ms=(perf_counter() - started) * 1000,
        )


def build_product_compare_handler():
    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        products = _coerce_products(payload.get("products"))
        statuses = _coerce_statuses(payload.get("inventory_status"))
        focus = _resolve_comparison_focus(
            payload.get("focus"),
            preferences={},
            message="",
        )
        if not products:
            return {
                "comparisons": [],
                "focus": focus,
                "prompt_render": str(payload.get("prompt_render", "")),
            }

        price_values = [product.price for product in products]
        lowest_price = min(price_values)
        highest_price = max(price_values)
        median_price = median(price_values)
        highest_score = max(product.score for product in products)
        status_by_product_id = {status.product_id: status for status in statuses}

        comparison_products = [
            SessionComparisonProduct(
                product_id=product.product_id,
                name=product.name,
                price=product.price,
                category=product.category,
                brand=product.brand,
                stock=_status_stock(status_by_product_id.get(product.product_id), product.stock),
                available=_status_available(status_by_product_id.get(product.product_id)),
                highlights=_build_comparison_highlights(
                    product,
                    focus=focus,
                    lowest_price=lowest_price,
                    highest_score=highest_score,
                    median_price=median_price,
                ),
                cautions=_build_comparison_cautions(
                    product,
                    focus=focus,
                    highest_price=highest_price,
                    status=status_by_product_id.get(product.product_id),
                ),
            )
            for product in products
        ]
        comparison = SessionProductComparison(
            focus=focus,
            summary=_build_comparison_summary(
                products,
                focus=focus,
                lowest_price=lowest_price,
                highest_score=highest_score,
            ),
            compared_product_ids=[product.product_id for product in products],
            products=comparison_products,
        )
        return {
            "comparisons": [comparison.model_dump(mode="json")],
            "focus": focus,
            "prompt_render": str(payload.get("prompt_render", "")),
        }

    return handler


def _comparison_prompt_products(products: list[Product]) -> str:
    if not products:
        return "[]"
    return " | ".join(
        f"{product.name}(price={product.price:.0f},brand={product.brand},tags={','.join(product.tags[:3])})"
        for product in products
    )


def _build_comparison_highlights(
    product: Product,
    *,
    focus: str,
    lowest_price: float,
    highest_score: float,
    median_price: float,
) -> list[str]:
    highlights: list[str] = []
    if product.price == lowest_price:
        highlights.append("价格门槛更低")
    if product.score == highest_score:
        highlights.append("综合评分更高")
    if product.price <= median_price:
        highlights.append("预算压力更可控")
    if "游戏" in focus and any("性能" in tag or "旗舰" in tag for tag in product.tags):
        highlights.append("更偏性能导向")
    if "办公" in focus and ("办公" in product.description or "商务" in product.description):
        highlights.append("更适合高频办公")
    if not highlights and product.tags:
        highlights.append(f"标签覆盖 {product.tags[0]}")
    if product.brand:
        highlights.append(f"{product.brand} 品牌识别度稳定")
    return _unique_texts(highlights)[:3]


def _build_comparison_cautions(
    product: Product,
    *,
    focus: str,
    highest_price: float,
    status: InventoryStatus | None,
) -> list[str]:
    cautions: list[str] = []
    if status is not None and not status.available:
        cautions.append("当前不可售")
    elif status is not None and status.low_stock:
        cautions.append("库存偏低")
    if status is not None and status.purchase_limit is not None:
        cautions.append(f"当前限购 {status.purchase_limit} 件")
    if product.price == highest_price:
        cautions.append("价格投入更高")
    if "预算" in focus and product.price > 5000:
        cautions.append("更适合放宽预算后考虑")
    return _unique_texts(cautions)[:3]


def _build_comparison_summary(
    products: list[Product],
    *,
    focus: str,
    lowest_price: float,
    highest_score: float,
) -> str:
    if len(products) == 1:
        product = products[0]
        return f"当前只有 {product.name} 一款候选，可继续补充偏好后再扩展比较。"

    budget_pick = min(products, key=lambda product: (product.price, -product.score, product.name))
    flagship_pick = max(products, key=lambda product: (product.score, -product.price, product.name))
    focus_pick = max(
        products,
        key=lambda product: (
            _focus_signal_score(product, focus),
            product.score,
            -product.price,
        ),
    )

    if "游戏" in focus:
        return (
            f"围绕 {focus} 看，{focus_pick.name} 更偏性能与体验完整度，"
            f"{budget_pick.name} 更适合作为预算友好的选择。"
        )
    if "办公" in focus:
        return (
            f"围绕 {focus} 看，{focus_pick.name} 更适合稳定办公场景，"
            f"{budget_pick.name} 在成本控制上更轻。"
        )
    if "预算" in focus or lowest_price <= 3000:
        return (
            f"当前候选里 {budget_pick.name} 的价格门槛最低，"
            f"{flagship_pick.name} 则在配置完整度和综合评分上更强。"
        )

    best_score_names = [
        product.name
        for product in products
        if product.score == highest_score
    ]
    return (
        f"当前比较更适合先在 {budget_pick.name} 的预算友好度与 "
        f"{best_score_names[0]} 的综合体验之间做取舍。"
    )


def _focus_signal_score(product: Product, focus: str) -> int:
    searchable = " ".join([product.name, product.description, " ".join(product.tags)])
    tokens = tuple(
        token
        for token in ("游戏", "性能", "办公", "商务", "拍照", "旗舰", "价格")
        if token in focus
    )
    return sum(token in searchable for token in tokens)


def _resolve_comparison_focus(
    explicit_focus: Any,
    *,
    preferences: dict[str, str],
    message: str,
) -> str:
    text = str(explicit_focus or "").strip()
    if text:
        return text
    use_case = str(preferences.get("use_case", "")).strip()
    if use_case:
        return f"{use_case}场景"
    if str(preferences.get("budget", "")).strip():
        return "预算控制"
    if "比较" in message or "对比" in message:
        return "差异对比"
    return "综合体验"


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


def _coerce_statuses(raw_statuses: Any) -> list[InventoryStatus]:
    if not isinstance(raw_statuses, Iterable) or isinstance(raw_statuses, (str, bytes, dict)):
        return []

    statuses: list[InventoryStatus] = []
    for item in raw_statuses:
        try:
            statuses.append(InventoryStatus.model_validate(item))
        except ValidationError:
            continue
    return statuses


def _status_stock(status: InventoryStatus | None, fallback: int) -> int:
    if status is None:
        return max(0, int(fallback))
    return status.stock


def _status_available(status: InventoryStatus | None) -> bool:
    if status is None:
        return True
    return status.available


def _unique_texts(values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        unique.append(text)
        seen.add(text)
    return unique


__all__ = [
    "ComparisonWorker",
    "build_product_compare_handler",
]
