from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.shared.data.product_catalog import ProductCatalog
from app.v2.core.models import Event, FeedbackSummary, UserProfile
from app.v2.core.persistence import EventStore, SessionStore, UserProfileStore
from app.v2.workers.preference import build_user_profile, extract_preference_signals


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_session_read_memory_handler(session_store: SessionStore):
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            return {"found": False, "session_id": session_id, "memory": {}}
        session = session_store.get(session_id)
        if session is None:
            return {"found": False, "session_id": session_id, "memory": {}}
        return {
            "found": True,
            "session_id": session.session_id,
            "memory": dict(session.memory),
        }

    return handler


def build_profile_extract_preferences_handler():
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        message = str(payload.get("message", ""))
        source_turn = int(payload.get("source_turn", 1))
        signals = extract_preference_signals(message, source_turn=source_turn)
        return {
            "signals": [signal.model_dump(mode="json") for signal in signals],
            "categories": [signal.category for signal in signals],
        }

    return handler


def build_profile_upsert_handler(user_profiles: UserProfileStore):
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id", "")).strip()
        preferences = dict(payload.get("preferences") or {})
        existing_profile = _coerce_profile(payload.get("existing_profile"))
        if existing_profile is None and user_id:
            existing_profile = user_profiles.get(user_id)
        profile = build_user_profile(
            user_id=user_id,
            preferences=preferences,
            existing_profile=existing_profile,
        )
        user_profiles.save(profile, updated_at=utc_now())
        return {
            "user_id": profile.user_id,
            "profile": profile.model_dump(mode="json"),
        }

    return handler


def build_profile_request_projection_handler(events: EventStore):
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id", "")).strip()
        target_scene = _normalize_target_scene(payload)
        normalized_payload = {
            "session_id": _as_text(payload.get("session_id")),
            "turn_number": payload.get("turn_number"),
            "trigger": _as_text(payload.get("trigger")),
            "preferences": {
                str(key): str(value)
                for key, value in dict(payload.get("preferences") or {}).items()
                if _as_text(value) is not None
            },
            "changed_categories": list(payload.get("changed_categories") or []),
            "stable_categories": list(payload.get("stable_categories") or []),
            "conflict_categories": list(payload.get("conflict_categories") or []),
            "target_scene": target_scene,
            "scene": target_scene,
        }
        event, deduped = _enqueue_event(
            events,
            event_type="profile_projection",
            user_id=user_id,
            target_scene=target_scene,
            payload=normalized_payload,
        )
        return {
            "accepted": True,
            "deduped": deduped,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "trigger": event.payload.get("trigger"),
            "target_scene": target_scene,
        }

    return handler


def build_recommendation_request_refresh_handler(events: EventStore):
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id", "")).strip()
        target_scene = _normalize_target_scene(payload)
        event, deduped = _enqueue_event(
            events,
            event_type="recommendation_refresh",
            user_id=user_id,
            target_scene=target_scene,
            payload={
                key: value
                for key, value in dict(payload).items()
                if key != "user_id"
            },
        )
        return {
            "accepted": True,
            "deduped": deduped,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "target_scene": target_scene,
            "trigger": event.payload.get("trigger"),
        }

    return handler


def build_feedback_record_handler(events: EventStore):
    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        created_at = utc_now()
        event = Event(
            event_id=f"evt_{uuid4().hex[:12]}",
            event_type=str(payload.get("event_type", "")).strip(),
            user_id=str(payload.get("user_id", "")).strip(),
            payload={
                "scene": payload.get("scene", "default"),
                "product_id": payload.get("product_id"),
                "product_ids": list(payload.get("product_ids") or []),
                "metadata": dict(payload.get("metadata") or {}),
            },
            status="completed",
            created_at=created_at,
            processed_at=created_at,
        )
        events.save(event)
        return {
            "accepted": True,
            "event_id": event.event_id,
        }

    return handler


def build_feedback_summary_read_handler(
    events: EventStore,
    product_catalog: ProductCatalog,
):
    async def handler(payload: dict[str, Any]) -> dict[str, Any]:
        user_id = str(payload.get("user_id", "")).strip()
        limit = _coerce_limit(payload.get("limit"), default=50)
        summary = await aggregate_feedback_summary(
            events=events,
            product_catalog=product_catalog,
            user_id=user_id,
            limit=limit,
        )
        return summary.model_dump(mode="json")

    return handler


async def aggregate_feedback_summary(
    *,
    events: EventStore,
    product_catalog: ProductCatalog,
    user_id: str,
    limit: int = 50,
) -> FeedbackSummary:
    if not user_id:
        return FeedbackSummary()

    catalog = {
        product.product_id: product
        for product in await product_catalog.list_products()
    }
    boosted_categories: set[str] = set()
    boosted_brands: set[str] = set()
    suppressed_product_ids: set[str] = set()

    for event in events.list_completed_feedback_events(user_id, limit=limit):
        payload = dict(event.payload)
        if event.event_type in {"click", "purchase"}:
            product_id = _as_text(payload.get("product_id"))
            if product_id is not None:
                product = catalog.get(product_id)
                if product is not None:
                    if product.category:
                        boosted_categories.add(product.category)
                    if product.brand:
                        boosted_brands.add(product.brand)
                if event.event_type == "purchase":
                    suppressed_product_ids.add(product_id)
        if event.event_type == "skip":
            product_id = _as_text(payload.get("product_id"))
            if product_id is not None:
                suppressed_product_ids.add(product_id)
            for raw_product_id in list(payload.get("product_ids") or []):
                normalized = _as_text(raw_product_id)
                if normalized is not None:
                    suppressed_product_ids.add(normalized)

    return FeedbackSummary(
        boosted_categories=sorted(boosted_categories),
        boosted_brands=sorted(boosted_brands),
        suppressed_product_ids=sorted(suppressed_product_ids),
    )


def _coerce_profile(raw_profile: Any) -> UserProfile | None:
    if raw_profile is None:
        return None
    if isinstance(raw_profile, UserProfile):
        return raw_profile
    try:
        return UserProfile.model_validate(raw_profile)
    except Exception:  # noqa: BLE001
        return None


def _coerce_limit(value: Any, *, default: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(limit, 200))


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _normalize_target_scene(payload: dict[str, Any]) -> str:
    return str(payload.get("target_scene") or payload.get("scene") or "homepage").strip() or "homepage"


def _enqueue_event(
    events: EventStore,
    *,
    event_type: str,
    user_id: str,
    target_scene: str,
    payload: dict[str, Any],
) -> tuple[Event, bool]:
    existing = events.find_active_event(
        user_id=user_id,
        event_type=event_type,
        target_scene=target_scene,
    )
    if existing is not None:
        if existing.status == "pending":
            merged_payload = {
                **dict(existing.payload),
                **payload,
                "target_scene": target_scene,
                "scene": target_scene,
            }
            existing = existing.model_copy(update={"payload": merged_payload})
            events.save(existing)
        return existing, True

    event = Event(
        event_id=f"evt_{uuid4().hex[:12]}",
        event_type=event_type,
        user_id=user_id,
        payload={
            **payload,
            "target_scene": target_scene,
            "scene": target_scene,
        },
        created_at=utc_now(),
    )
    events.save(event)
    return event, False


__all__ = [
    "aggregate_feedback_summary",
    "build_feedback_record_handler",
    "build_feedback_summary_read_handler",
    "build_profile_extract_preferences_handler",
    "build_profile_request_projection_handler",
    "build_profile_upsert_handler",
    "build_recommendation_request_refresh_handler",
    "build_session_read_memory_handler",
]
