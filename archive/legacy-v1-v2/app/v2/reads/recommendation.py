from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from app.v2.api.schemas import (
    RecommendationReadRequest,
    RecommendationReadResponse,
    SessionProductPreview,
)
from app.v2.background.refresh import utc_now
from app.v2.core.models import RecommendationSnapshot
from app.v2.core.persistence import EventStore, RecommendationSnapshotStore, UserProfileStore
from app.v2.core.tools import build_recommendation_request_refresh_handler

SUPPORTED_RECOMMENDATION_SCENES: tuple[str, ...] = (
    "default",
    "homepage",
    "product_page",
    "cart",
)


class RecommendationReadService:
    def __init__(
        self,
        *,
        snapshots: RecommendationSnapshotStore,
        user_profiles: UserProfileStore,
        events: EventStore,
        **_: Any,
    ) -> None:
        self.name = "recommendation_read"
        self.snapshots = snapshots
        self.user_profiles = user_profiles
        self.events = events
        self._request_refresh = build_recommendation_request_refresh_handler(events)

    async def read_recommendations(
        self,
        user_id: str,
        request: RecommendationReadRequest,
    ) -> RecommendationReadResponse:
        normalized = normalize_recommendation_request(request)
        primary_snapshot, fallback_snapshot = self._resolve_snapshots(
            user_id=user_id,
            normalized=normalized,
        )

        if primary_snapshot is not None:
            if _is_expired(primary_snapshot.expires_at):
                refresh = self._enqueue_homepage_refresh(user_id=user_id, trigger="snapshot_expired")
                return self._build_response(
                    snapshot=primary_snapshot,
                    stale=True,
                    pending_refresh=refresh,
                )
            return self._build_response(
                snapshot=primary_snapshot,
                stale=False,
                pending_refresh=False,
            )

        if fallback_snapshot is not None:
            if _is_expired(fallback_snapshot.expires_at):
                refresh = self._enqueue_homepage_refresh(user_id=user_id, trigger="snapshot_expired")
                return self._build_response(
                    snapshot=fallback_snapshot,
                    stale=True,
                    pending_refresh=refresh,
                )
            return self._build_response(
                snapshot=fallback_snapshot,
                stale=False,
                pending_refresh=False,
            )

        refresh = self._enqueue_homepage_refresh(user_id=user_id, trigger="snapshot_miss")
        return RecommendationReadResponse(
            user_id=user_id,
            scene="homepage" if normalized["scene"] == "default" else normalized["scene"],
            products=[],
            copies=[],
            generated_at=None,
            stale=True,
            pending_refresh=refresh,
        )

    def _resolve_snapshots(
        self,
        *,
        user_id: str,
        normalized: dict[str, Any],
    ) -> tuple[RecommendationSnapshot | None, RecommendationSnapshot | None]:
        scene = normalized["scene"]
        scene_context = normalized["scene_context"]

        if scene == "homepage":
            return (
                self.snapshots.get_latest(user_id=user_id, scene="homepage"),
                None,
            )

        if scene == "default":
            default_snapshot = self.snapshots.get_latest(user_id=user_id, scene="default")
            homepage_snapshot = self.snapshots.get_latest(user_id=user_id, scene="homepage")
            return default_snapshot, homepage_snapshot

        contextual_snapshot = self.snapshots.get_latest(
            user_id=user_id,
            scene=scene,
            scene_context=scene_context,
        )
        homepage_snapshot = self.snapshots.get_latest(user_id=user_id, scene="homepage")
        return contextual_snapshot, homepage_snapshot

    def _enqueue_homepage_refresh(self, *, user_id: str, trigger: str) -> bool:
        payload = self._request_refresh(
            {
                "user_id": user_id,
                "scene": "homepage",
                "trigger": trigger,
                "preferences": {},
                "changed_categories": [],
                "stable_categories": [],
                "conflict_categories": [],
            },
        )
        return bool(payload.get("accepted"))

    @staticmethod
    def _build_response(
        *,
        snapshot: RecommendationSnapshot,
        stale: bool,
        pending_refresh: bool,
    ) -> RecommendationReadResponse:
        return RecommendationReadResponse(
            user_id=snapshot.user_id,
            scene=snapshot.scene,
            products=_build_product_previews(snapshot.products),
            copies=[copy.model_copy(deep=True) for copy in snapshot.copies],
            generated_at=snapshot.generated_at,
            stale=stale,
            pending_refresh=pending_refresh,
        )


def normalize_recommendation_request(request: RecommendationReadRequest) -> dict[str, Any]:
    scene = _normalize_scene(request.scene)
    if scene not in SUPPORTED_RECOMMENDATION_SCENES:
        scene = "default"

    if scene == "product_page":
        product_id = _as_text(request.product_id)
        if product_id is None:
            return {"scene": "product_page", "scene_context": {}}
        return {"scene": scene, "scene_context": {"product_id": product_id}}

    if scene == "cart":
        product_ids = _normalize_ids(request.product_ids)
        if not product_ids:
            return {"scene": "cart", "scene_context": {}}
        return {"scene": scene, "scene_context": {"product_ids": product_ids}}

    return {"scene": scene, "scene_context": {}}


def _build_product_previews(products: list[Any]) -> list[SessionProductPreview]:
    previews: list[SessionProductPreview] = []
    for product in products:
        previews.append(
            SessionProductPreview(
                product_id=product.product_id,
                name=product.name,
                price=product.price,
                category=product.category,
                brand=product.brand,
            ),
        )
    return previews


def _normalize_scene(value: Any) -> str:
    text = str(value or "default").strip()
    return text or "default"


def _normalize_ids(values: Iterable[str]) -> list[str]:
    identifiers: set[str] = set()
    for value in values:
        for part in str(value).split(","):
            text = part.strip()
            if text:
                identifiers.add(text)
    return sorted(identifiers)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _is_expired(expires_at: datetime | None) -> bool:
    return expires_at is not None and expires_at <= utc_now()


__all__ = [
    "RecommendationReadService",
    "SUPPORTED_RECOMMENDATION_SCENES",
    "normalize_recommendation_request",
]
