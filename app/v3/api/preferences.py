from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from app.v3.memory import get_preference_profile, revoke_preference
from app.v3.models import Product, ProductCategory
from app.v3.models.base import V3Model
from app.v3.observability import log_event
from app.v3.tools.catalog_search import catalog_search

from .sessions import SessionStore

router = APIRouter(tags=["v3"])
_LOGGER = logging.getLogger(__name__)

_PICK_LIMIT = 3


class RevokePreferenceRequest(V3Model):
    key: str = Field(min_length=1)
    reason: str | None = None


@router.get("/api/v3/sessions/{session_id}/preferences")
async def get_preferences(session_id: str, request: Request) -> dict[str, object]:
    session_store: SessionStore = request.app.state.v3_session_store
    record = session_store.get(session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found", "session_id": session_id},
        )

    profile = get_preference_profile(record.state)
    log_event(
        _LOGGER,
        "api.preferences.fetched",
        trace_id=getattr(request.state, "trace_id", None),
        session_id=session_id,
        turn_number=record.state.turn_count,
        payload={"entry_count": len(profile)},
    )
    return {
        "session_id": session_id,
        "turn_count": record.state.turn_count,
        "entries": profile,
    }


@router.post("/api/v3/sessions/{session_id}/preferences/revoke")
async def post_revoke_preference(
    session_id: str,
    payload: RevokePreferenceRequest,
    request: Request,
) -> dict[str, object]:
    session_store: SessionStore = request.app.state.v3_session_store
    record = session_store.get(session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found", "session_id": session_id},
        )

    hook_bus = getattr(request.app.state, "v3_hook_bus", None)
    trace_id = getattr(request.state, "trace_id", None)
    revoked = await revoke_preference(
        record.state,
        payload.key,
        reason=payload.reason or "user revoked via /preferences/revoke",
        hook_bus=hook_bus,
        trace_id=trace_id,
        turn_number=record.state.turn_count,
    )

    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "preference_key_not_found",
                "session_id": session_id,
                "key": payload.key,
            },
        )

    log_event(
        _LOGGER,
        "api.preferences.revoked",
        trace_id=trace_id,
        session_id=session_id,
        turn_number=record.state.turn_count,
        payload={"key": payload.key, "reason": payload.reason},
    )
    remaining = get_preference_profile(record.state)
    return {
        "session_id": session_id,
        "revoked_key": payload.key,
        "remaining_entries": remaining,
    }


@router.get("/api/v3/sessions/{session_id}/personalized_picks")
async def get_personalized_picks(session_id: str, request: Request) -> dict[str, object]:
    session_store: SessionStore = request.app.state.v3_session_store
    record = session_store.get(session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found", "session_id": session_id},
        )

    profile = get_preference_profile(record.state)
    prefs: dict[str, Any] = {entry["key"]: entry["value"] for entry in profile}

    if not prefs:
        return {
            "session_id": session_id,
            "preferences_snapshot": [],
            "picks": [],
            "hint": "先在对话中告诉 AI 你的偏好(预算 / 场景 / 排斥品牌),档案会实时投影到这里。",
        }

    filters, query = _build_search(prefs)
    results = catalog_search(query, filters)

    picks = [_format_pick(product, prefs) for product in results[:_PICK_LIMIT]]

    log_event(
        _LOGGER,
        "api.preferences.picks_generated",
        trace_id=getattr(request.state, "trace_id", None),
        session_id=session_id,
        turn_number=record.state.turn_count,
        payload={"query": query, "filter_keys": sorted(filters.keys()), "pick_count": len(picks)},
    )
    return {
        "session_id": session_id,
        "preferences_snapshot": profile,
        "query": query,
        "filters": filters,
        "picks": picks,
    }


def _build_search(prefs: dict[str, Any]) -> tuple[dict[str, Any], str]:
    filters: dict[str, Any] = {"limit": _PICK_LIMIT * 2}
    query_parts: list[str] = []

    category_value = prefs.get("category")
    if isinstance(category_value, str):
        try:
            filters["category"] = ProductCategory(category_value)
            query_parts.append(category_value)
        except ValueError:
            pass

    scene_value = prefs.get("scene")
    if isinstance(scene_value, str) and scene_value:
        filters["scene"] = scene_value
        query_parts.append(scene_value)

    budget = prefs.get("budget")
    if isinstance(budget, dict):
        budget_max = budget.get("max")
        if isinstance(budget_max, int) and budget_max > 0:
            filters["price_max"] = budget_max
        budget_min = budget.get("min")
        if isinstance(budget_min, int) and budget_min > 0:
            filters["price_min"] = budget_min

    exclude_brands = prefs.get("exclude_brands")
    if isinstance(exclude_brands, list) and exclude_brands:
        filters["exclude_brands"] = [str(item) for item in exclude_brands]

    query = " ".join(query_parts) if query_parts else "personalized"
    return filters, query


def _format_pick(product: Product, prefs: dict[str, Any]) -> dict[str, Any]:
    return {
        "sku": product.sku,
        "name": product.name,
        "brand": product.brand,
        "category": product.category.value,
        "subcategory": product.subcategory,
        "price": product.price,
        "currency": product.currency,
        "rating": product.rating,
        "description": product.description,
        "scene_tags": list(product.scene_tags),
        "match_reason": _build_match_reason(product, prefs),
    }


def _build_match_reason(product: Product, prefs: dict[str, Any]) -> str:
    parts: list[str] = []

    scene = prefs.get("scene")
    if isinstance(scene, str) and scene in {tag.lower() for tag in product.scene_tags}:
        parts.append(f"匹配 {scene} 场景")

    budget = prefs.get("budget")
    if isinstance(budget, dict):
        budget_max = budget.get("max")
        if isinstance(budget_max, int) and product.price <= budget_max:
            parts.append(f"价格 {product.price} 在 {budget_max} 预算内")

    exclude_brands = prefs.get("exclude_brands")
    if isinstance(exclude_brands, list) and exclude_brands:
        parts.append(f"避开了 {', '.join(str(item) for item in exclude_brands)}")

    if product.rating >= 4.5:
        parts.append(f"评分 {product.rating}")

    if not parts:
        return "基于你的偏好匹配"
    return " · ".join(parts)


__all__ = ["RevokePreferenceRequest", "router"]
