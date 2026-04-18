from __future__ import annotations

from time import perf_counter
from typing import Any, Iterable

from pydantic import ValidationError

from app.shared.models.domain import MarketingCopy, Product
from app.v2.core.models import UserProfile, WorkerResult, WorkerTask
from app.v2.core.prompts import PromptRegistry, build_default_prompt_registry
from app.v2.core.runtime import Worker, WorkerExecutionContext


class CopyWorker(Worker):
    def __init__(self) -> None:
        super().__init__(
            "copy_worker",
            allowed_tools={"copy.generate"},
        )

    async def execute(
        self,
        task: WorkerTask,
        context: WorkerExecutionContext,
    ) -> WorkerResult:
        started = perf_counter()
        payload = await context.call_tool(
            "copy.generate",
            {
                "products": list(task.input.get("products") or []),
                "preferences": dict(task.input.get("preferences") or {}),
                "user_profile": task.input.get("user_profile"),
                "scene": task.input.get("scene", "default"),
                "message": task.input.get("message", ""),
            },
        )
        return WorkerResult(
            worker_name=self.name,
            payload=dict(payload or {}),
            latency_ms=(perf_counter() - started) * 1000,
        )


def build_copy_generate_handler(
    prompt_registry: PromptRegistry | None = None,
):
    registry = prompt_registry or build_default_prompt_registry()

    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        products = _coerce_products(payload.get("products"))
        preferences = {
            str(key): str(value)
            for key, value in dict(payload.get("preferences") or {}).items()
            if str(value).strip()
        }
        profile = _coerce_profile(payload.get("user_profile"))
        scene = _normalize_scene(payload.get("scene"))
        audience = _resolve_copy_audience(
            preferences=preferences,
            profile=profile,
            scene=scene,
            message=str(payload.get("message", "")),
        )

        copies: list[dict[str, Any]] = []
        prompt_renders: dict[str, str] = {}
        for product in products:
            selling_points = _build_selling_points(
                product,
                preferences=preferences,
                scene=scene,
            )
            prompt_renders[product.product_id] = registry.render(
                "copy.generate",
                variables={
                    "audience": audience,
                    "product_name": product.name,
                    "selling_points": "；".join(selling_points),
                },
            )
            copies.append(
                MarketingCopy(
                    product_id=product.product_id,
                    copy_text=_compose_copy_text(
                        product,
                        audience=audience,
                        selling_points=selling_points,
                    ),
                ).model_dump(mode="json"),
            )

        return {
            "audience": audience,
            "copies": copies,
            "prompt_renders": prompt_renders,
        }

    return handler


def _resolve_copy_audience(
    *,
    preferences: dict[str, str],
    profile: UserProfile | None,
    scene: str,
    message: str,
) -> str:
    use_case = preferences.get("use_case", "")
    if use_case:
        return f"{use_case}用户"
    segments = set(profile.segments if profile is not None else [])
    if "price_sensitive" in segments or preferences.get("budget"):
        return "预算敏感用户"
    if "high_value" in segments:
        return "品质导向用户"
    lowered_message = message.lower()
    if "gift" in lowered_message or "送礼" in message:
        return "送礼场景用户"
    if scene == "homepage":
        return "首页浏览用户"
    return "购物咨询用户"


def _build_selling_points(
    product: Product,
    *,
    preferences: dict[str, str],
    scene: str,
) -> list[str]:
    points = [product.description or product.name]
    budget = _parse_budget(preferences.get("budget"))
    if budget is not None:
        if product.price <= budget:
            points.append(f"{product.price:.0f} 元可落在当前预算内")
        else:
            points.append(f"{product.price:.0f} 元定位高于当前预算")
    if product.brand:
        points.append(f"{product.brand} {product.category}")
    if scene == "product_page":
        points.append("适合作为当前商品的延展比较项")
    elif scene == "cart":
        points.append("适合作为购物车补充搭配")
    points.extend(product.tags[:2])
    return _unique_texts(points)[:4]


def _compose_copy_text(
    product: Product,
    *,
    audience: str,
    selling_points: list[str],
) -> str:
    lead = selling_points[0] if selling_points else (product.description or product.name)
    support = selling_points[1] if len(selling_points) > 1 else f"{product.price:.0f} 元定位"
    if "游戏" in audience:
        return f"{product.name} 把性能和沉浸体验放在前面，{lead}，{support}，更适合持续开黑。"
    if "办公" in audience:
        return f"{product.name} 更贴合高频办公使用，{lead}，{support}，日常协作会更稳。"
    if "预算敏感" in audience:
        return f"{product.name} 兼顾核心体验和预算控制，{lead}，{support}，作为当前价位段选择更省心。"
    if "品质导向" in audience:
        return f"{product.name} 把质感和完整配置拉得更满，{lead}，{support}，适合重视体验完成度的人。"
    if "送礼" in audience:
        return f"{product.name} 作为礼物更容易讲清卖点，{lead}，{support}，拿来送人也体面。"
    return f"{product.name} 把核心卖点收束得很清楚，{lead}，{support}，上手门槛低。"


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


def _coerce_profile(raw_profile: Any) -> UserProfile | None:
    if raw_profile is None:
        return None
    if isinstance(raw_profile, UserProfile):
        return raw_profile
    try:
        return UserProfile.model_validate(raw_profile)
    except ValidationError:
        return None


def _normalize_scene(value: Any) -> str:
    text = str(value or "default").strip()
    return text or "default"


def _parse_budget(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
    "CopyWorker",
    "build_copy_generate_handler",
]
