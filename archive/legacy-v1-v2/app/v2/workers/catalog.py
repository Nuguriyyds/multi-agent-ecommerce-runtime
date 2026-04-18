from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Iterable

from pydantic import ValidationError

from app.shared.data.product_catalog import ProductCatalog
from app.shared.models.domain import Product
from app.v2.core.models import FeedbackSummary, UserProfile, WorkerResult, WorkerTask
from app.v2.core.runtime import Worker, WorkerExecutionContext

_DEFAULT_LIMIT = 3
_COMPLEMENTARY_CATEGORIES: dict[str, tuple[str, ...]] = {
    "手机": ("耳机", "配件", "穿戴"),
    "平板": ("配件", "耳机"),
    "笔记本": ("配件", "显示器"),
    "耳机": ("配件",),
    "穿戴": ("配件",),
    "家电": ("配件",),
}


@dataclass(frozen=True, slots=True)
class PreferenceView:
    categories: tuple[str, ...]
    brands: tuple[str, ...]
    use_cases: tuple[str, ...]
    exclusions: tuple[str, ...]
    budget_ceiling: float | None
    feedback_summary: FeedbackSummary


class CatalogWorker(Worker):
    def __init__(self) -> None:
        super().__init__(
            "catalog_worker",
            allowed_tools={
                "catalog.search_products",
                "feedback.read_summary",
            },
        )

    async def execute(
        self,
        task: WorkerTask,
        context: WorkerExecutionContext,
    ) -> WorkerResult:
        started = perf_counter()
        scene = _normalize_scene(task.input.get("scene"))
        scene_context = dict(task.input.get("scene_context") or {})
        user_id = str(task.input.get("user_id", "")).strip()
        feedback_summary = FeedbackSummary()

        if user_id:
            feedback_payload = await context.call_tool(
                "feedback.read_summary",
                {
                    "user_id": user_id,
                    "limit": 50,
                },
            )
            feedback_summary = FeedbackSummary.model_validate(feedback_payload)

        search_payload = await context.call_tool(
            "catalog.search_products",
            {
                "scene": scene,
                "scene_context": scene_context,
                "preferences": dict(task.input.get("preferences") or {}),
                "user_profile": task.input.get("user_profile"),
                "feedback_summary": feedback_summary.model_dump(mode="json"),
                "limit": task.input.get("limit", _DEFAULT_LIMIT),
            },
        )
        return WorkerResult(
            worker_name=self.name,
            payload=dict(search_payload or {}),
            latency_ms=(perf_counter() - started) * 1000,
        )


def build_catalog_search_handler(product_catalog: ProductCatalog):
    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        scene = _normalize_scene(payload.get("scene"))
        scene_context = dict(payload.get("scene_context") or {})
        profile = _coerce_profile(payload.get("user_profile"))
        feedback_summary = _coerce_feedback_summary(payload.get("feedback_summary"))
        preferences = _build_preference_view(
            preferences=dict(payload.get("preferences") or {}),
            profile=profile,
            feedback_summary=feedback_summary,
        )
        limit = _coerce_limit(payload.get("limit"), default=_DEFAULT_LIMIT)
        candidates = await product_catalog.list_products()
        selected = _select_products(
            candidates=candidates,
            scene=scene,
            scene_context=scene_context,
            preferences=preferences,
            limit=limit,
        )
        return {
            "scene": scene,
            "scene_context": scene_context,
            "source": "generated",
            "selection_reason": _selection_reason(scene),
            "products": [_dump_product(product) for product in selected],
        }

    return handler


def _select_products(
    *,
    candidates: list[Product],
    scene: str,
    scene_context: dict[str, Any],
    preferences: PreferenceView,
    limit: int,
) -> list[Product]:
    filtered = [
        product
        for product in candidates
        if not _is_excluded(product, preferences.exclusions)
    ]
    working_set = filtered or list(candidates)
    if not working_set:
        return []

    if scene == "product_page":
        return _rank_product_page(working_set, scene_context, preferences, limit)
    if scene == "cart":
        return _rank_cart(working_set, scene_context, preferences, limit)
    return _rank_default_scene(working_set, preferences, limit)


def _rank_default_scene(
    candidates: list[Product],
    preferences: PreferenceView,
    limit: int,
) -> list[Product]:
    scored = [
        (
            _score_with_preferences(product, preferences),
            product.price,
            product.name,
            product,
        )
        for product in candidates
    ]
    return _take_sorted(scored, limit)


def _rank_product_page(
    candidates: list[Product],
    scene_context: dict[str, Any],
    preferences: PreferenceView,
    limit: int,
) -> list[Product]:
    anchor_id = str(scene_context.get("product_id", "")).strip()
    anchor = next((product for product in candidates if product.product_id == anchor_id), None)
    if anchor is None:
        return _rank_default_scene(candidates, preferences, limit)

    related = [product for product in candidates if product.product_id != anchor.product_id]
    scored: list[tuple[float, float, str, Product]] = []
    anchor_tags = {tag.casefold() for tag in anchor.tags}
    for product in related:
        score = _score_with_preferences(product, preferences)
        if product.category == anchor.category:
            score += 2.2
        if product.brand and product.brand == anchor.brand:
            score += 0.6
        if anchor_tags:
            overlap = anchor_tags.intersection(tag.casefold() for tag in product.tags)
            score += min(len(overlap) * 0.2, 0.8)
        score += _price_proximity_bonus(price=product.price, reference=anchor.price)
        scored.append((round(score, 3), product.price, product.name, product))
    return _take_sorted(scored, limit)


def _rank_cart(
    candidates: list[Product],
    scene_context: dict[str, Any],
    preferences: PreferenceView,
    limit: int,
) -> list[Product]:
    cart_ids = {
        product_id
        for product_id in _normalize_ids(scene_context.get("product_ids"))
        if product_id
    }
    cart_products = [product for product in candidates if product.product_id in cart_ids]
    if not cart_products:
        return _rank_default_scene(candidates, preferences, limit)

    related = [product for product in candidates if product.product_id not in cart_ids]
    cart_categories = {product.category for product in cart_products}
    cart_brands = {product.brand for product in cart_products if product.brand}
    cart_tags = {tag.casefold() for product in cart_products for tag in product.tags}
    complementary = {
        category
        for cart_category in cart_categories
        for category in _COMPLEMENTARY_CATEGORIES.get(cart_category, ())
    }

    scored: list[tuple[float, float, str, Product]] = []
    for product in related:
        score = _score_with_preferences(product, preferences)
        if product.category in complementary:
            score += 1.8
        elif product.category in cart_categories:
            score += 0.6
        if product.brand and product.brand in cart_brands:
            score += 0.3
        if cart_tags:
            overlap = cart_tags.intersection(tag.casefold() for tag in product.tags)
            score += min(len(overlap) * 0.15, 0.6)
        scored.append((round(score, 3), product.price, product.name, product))
    return _take_sorted(scored, limit)


def _score_with_preferences(product: Product, preferences: PreferenceView) -> float:
    score = float(product.score)

    if preferences.categories:
        if product.category == preferences.categories[0]:
            score += 2.1
        elif product.category in preferences.categories:
            score += 1.3

    if preferences.brands:
        if product.brand == preferences.brands[0]:
            score += 1.2
        elif product.brand in preferences.brands:
            score += 0.7

    if preferences.use_cases:
        searchable = " ".join([product.name, product.description, " ".join(product.tags)]).casefold()
        for use_case in preferences.use_cases:
            if use_case.casefold() in searchable:
                score += 0.7

    if preferences.budget_ceiling is not None:
        score += _budget_bonus(product.price, preferences.budget_ceiling)

    if product.category in preferences.feedback_summary.boosted_categories:
        score += 0.9
    if product.brand and product.brand in preferences.feedback_summary.boosted_brands:
        score += 0.5
    if product.product_id in preferences.feedback_summary.suppressed_product_ids:
        score -= 5.0

    return round(score, 3)


def _budget_bonus(price: float, ceiling: float) -> float:
    if ceiling <= 0:
        return 0.0
    if price <= ceiling:
        return 1.0

    gap_ratio = (price - ceiling) / max(ceiling, 1.0)
    return max(-1.0, 0.6 - (gap_ratio * 1.4))


def _price_proximity_bonus(*, price: float, reference: float) -> float:
    if reference <= 0:
        return 0.0
    gap_ratio = abs(price - reference) / max(reference, 1.0)
    return max(0.0, 0.8 - gap_ratio)


def _build_preference_view(
    *,
    preferences: dict[str, Any],
    profile: UserProfile | None,
    feedback_summary: FeedbackSummary,
) -> PreferenceView:
    categories = _merge_unique(
        [_as_text(preferences.get("product_category"))],
        profile.preferred_categories if profile is not None else [],
    )
    brands = _merge_unique(
        [_as_text(preferences.get("brand"))],
        profile.preferred_brands if profile is not None else [],
    )
    use_cases = _merge_unique(
        [_as_text(preferences.get("use_case"))],
        profile.use_cases if profile is not None else [],
    )
    exclusions = _merge_unique(
        [_as_text(preferences.get("exclusion"))],
        profile.excluded_terms if profile is not None else [],
    )
    budget_ceiling = _parse_float(preferences.get("budget"))
    if budget_ceiling is None and profile is not None and profile.price_range is not None:
        budget_ceiling = float(profile.price_range[1])

    return PreferenceView(
        categories=tuple(categories),
        brands=tuple(brands),
        use_cases=tuple(use_cases),
        exclusions=tuple(exclusions),
        budget_ceiling=budget_ceiling,
        feedback_summary=feedback_summary,
    )


def _coerce_profile(raw: Any) -> UserProfile | None:
    if raw is None:
        return None
    if isinstance(raw, UserProfile):
        return raw
    try:
        return UserProfile.model_validate(raw)
    except ValidationError:
        return None


def _coerce_feedback_summary(raw: Any) -> FeedbackSummary:
    if raw is None:
        return FeedbackSummary()
    if isinstance(raw, FeedbackSummary):
        return raw
    try:
        return FeedbackSummary.model_validate(raw)
    except ValidationError:
        return FeedbackSummary()


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


def _take_sorted(
    scored: list[tuple[float, float, str, Product]],
    limit: int,
) -> list[Product]:
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [product for _, _, _, product in scored[:limit]]


def _merge_unique(first: list[str | None], second: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*first, *second]:
        text = (value or "").strip()
        if not text or text in seen:
            continue
        merged.append(text)
        seen.add(text)
    return merged


def _normalize_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if not isinstance(value, Iterable):
        return []
    identifiers: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            identifiers.append(text)
    return identifiers


def _is_excluded(product: Product, exclusions: tuple[str, ...]) -> bool:
    if not exclusions:
        return False
    searchable = " ".join(
        [product.name, product.brand, product.description, " ".join(product.tags)],
    ).casefold()
    return any(exclusion.casefold() in searchable for exclusion in exclusions)


def _normalize_scene(value: Any) -> str:
    text = str(value or "default").strip()
    return text or "default"


def _coerce_limit(value: Any, *, default: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, 10))


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _selection_reason(scene: str) -> str:
    if scene == "homepage":
        return "homepage_profile_seeded"
    if scene == "product_page":
        return "product_page_related"
    if scene == "cart":
        return "cart_complementary"
    return "default_profile_seeded"


def _dump_product(product: Product) -> dict[str, Any]:
    return product.model_dump(mode="json")


__all__ = [
    "CatalogWorker",
    "build_catalog_search_handler",
]
