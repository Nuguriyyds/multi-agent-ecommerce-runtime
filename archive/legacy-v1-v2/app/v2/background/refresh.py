from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from app.shared.data.inventory_store import InventoryStore
from app.shared.data.product_catalog import ProductCatalog
from app.shared.models.domain import MarketingCopy, Product
from app.v2.core.hooks import HookBus
from app.v2.core.models import Event, RecommendationSnapshot, TaskRecord, ToolSpec, UserProfile, WorkerTask
from app.v2.core.persistence import (
    EventStore,
    RecommendationSnapshotStore,
    TaskRecordStore,
    UserProfileStore,
)
from app.v2.core.prompts import PromptRegistry, build_default_prompt_registry
from app.v2.core.runtime import ToolRegistry
from app.v2.core.tools import (
    build_feedback_summary_read_handler,
    build_recommendation_request_refresh_handler,
)
from app.v2.workers.catalog import CatalogWorker, build_catalog_search_handler
from app.v2.workers.copy import CopyWorker, build_copy_generate_handler
from app.v2.workers.inventory import InventoryWorker, build_inventory_check_handler
from app.v2.workers.preference import build_user_profile

SNAPSHOT_TTL = timedelta(hours=24)
REFRESH_SCENES: tuple[str, ...] = ("homepage",)
SUPPORTED_BACKGROUND_EVENT_TYPES: tuple[str, ...] = (
    "profile_projection",
    "recommendation_refresh",
)
INTERRUPTED_ERROR_MESSAGE = "background worker interrupted"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BackgroundRefreshProcessor:
    def __init__(
        self,
        *,
        events: EventStore,
        user_profiles: UserProfileStore,
        snapshots: RecommendationSnapshotStore,
        tasks: TaskRecordStore,
        hook_bus: HookBus | None = None,
        product_catalog: ProductCatalog | None = None,
        inventory_store: InventoryStore | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self.name = "background_refresh"
        self.events = events
        self.user_profiles = user_profiles
        self.snapshots = snapshots
        self.tasks = tasks
        self._hook_bus = hook_bus
        self._prompt_registry = prompt_registry or build_default_prompt_registry()
        self._catalog_worker = CatalogWorker()
        self._inventory_worker = InventoryWorker()
        self._copy_worker = CopyWorker()
        catalog = product_catalog or ProductCatalog()
        self._tool_registry = ToolRegistry(hook_bus=self._hook_bus)
        self._tool_registry.register(
            ToolSpec(
                name="catalog.search_products",
                description="Search deterministic catalog candidates for a refresh scene",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                side_effect_level="none",
            ),
            build_catalog_search_handler(catalog),
        )
        self._tool_registry.register(
            ToolSpec(
                name="inventory.check",
                description="Read inventory availability for refresh candidates",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                side_effect_level="none",
            ),
            build_inventory_check_handler(inventory_store or InventoryStore()),
        )
        self._tool_registry.register(
            ToolSpec(
                name="feedback.read_summary",
                description="Read recent completed feedback summary for refresh selection",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                side_effect_level="none",
            ),
            build_feedback_summary_read_handler(events, catalog),
        )
        self._tool_registry.register(
            ToolSpec(
                name="copy.generate",
                description="Generate prompt-backed snapshot copy",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                side_effect_level="none",
            ),
            build_copy_generate_handler(prompt_registry=self._prompt_registry),
        )
        self._request_refresh = build_recommendation_request_refresh_handler(events)

    async def process_pending_events(self, *, limit: int = 100) -> tuple[Event, ...]:
        processed: list[Event] = []
        while len(processed) < limit:
            pending = next(
                (
                    event
                    for event in self.events.list_by_status("pending", limit=max(limit, 1))
                    if event.event_type in SUPPORTED_BACKGROUND_EVENT_TYPES
                ),
                None,
            )
            if pending is None:
                break
            processed.append(await self.process_event(pending))
        return tuple(processed)

    async def process_event(self, event: Event | str) -> Event:
        stored = self._load_event(event)
        if stored.event_type not in SUPPORTED_BACKGROUND_EVENT_TYPES:
            raise ValueError(f"unsupported background event type: {stored.event_type}")

        started = perf_counter()
        attempt = stored.retry_count + 1
        task_id = f"bg_{stored.event_id}_attempt_{attempt}"
        created_at = utc_now()
        pending_task = TaskRecord(
            task_id=task_id,
            task_scope="background",
            session_id=_as_text(stored.payload.get("session_id")),
            event_id=stored.event_id,
            manager_name=self.name,
            step=1,
            status="pending",
            input={
                "event_type": stored.event_type,
                "user_id": stored.user_id,
                "payload": dict(stored.payload),
            },
        )
        self.tasks.save(pending_task, created_at=created_at, updated_at=created_at)

        processing_event = stored.model_copy(
            update={
                "status": "processing",
                "error": None,
                "processed_at": None,
            },
        )
        self.events.save(processing_event)

        running_task = pending_task.model_copy(update={"status": "running"})
        self.tasks.save(running_task, created_at=created_at, updated_at=utc_now())

        task_snapshot = {
            "processor_name": self.name,
            "event": processing_event.model_dump(mode="json"),
            "task": running_task.model_dump(mode="json"),
        }
        if self._hook_bus is not None:
            await self._hook_bus.emit("background_task.started", task_snapshot)

        try:
            output = await self._process_event_impl(processing_event)
            completed_event = processing_event.model_copy(
                update={
                    "status": "completed",
                    "error": None,
                    "processed_at": utc_now(),
                },
            )
            self.events.save(completed_event)

            completed_task = running_task.model_copy(
                update={
                    "status": "completed",
                    "output": output,
                    "latency_ms": (perf_counter() - started) * 1000,
                },
            )
            self.tasks.save(completed_task, created_at=created_at, updated_at=utc_now())

            if self._hook_bus is not None:
                await self._hook_bus.emit(
                    "background_task.finished",
                    {
                        **task_snapshot,
                        "output": dict(output),
                    },
                )
            return completed_event
        except asyncio.CancelledError:
            requeued_event = processing_event.model_copy(
                update={"status": "pending", "error": None, "processed_at": None},
            )
            self.events.save(requeued_event)
            interrupted_task = running_task.model_copy(
                update={
                    "status": "failed",
                    "error": INTERRUPTED_ERROR_MESSAGE,
                    "latency_ms": (perf_counter() - started) * 1000,
                },
            )
            self.tasks.save(interrupted_task, created_at=created_at, updated_at=utc_now())
            if self._hook_bus is not None:
                await self._hook_bus.emit(
                    "background_task.failed",
                    {**task_snapshot, "error": INTERRUPTED_ERROR_MESSAGE},
                )
            raise
        except Exception as exc:  # noqa: BLE001
            error_message = _error_message(exc)
            failed_event = processing_event.model_copy(
                update={
                    "status": "failed",
                    "retry_count": processing_event.retry_count + 1,
                    "error": error_message,
                    "processed_at": utc_now(),
                },
            )
            self.events.save(failed_event)
            failed_task = running_task.model_copy(
                update={
                    "status": "failed",
                    "error": error_message,
                    "latency_ms": (perf_counter() - started) * 1000,
                },
            )
            self.tasks.save(failed_task, created_at=created_at, updated_at=utc_now())
            if self._hook_bus is not None:
                await self._hook_bus.emit(
                    "background_task.failed",
                    {**task_snapshot, "error": error_message},
                )
            return failed_event

    async def _process_event_impl(self, event: Event) -> dict[str, Any]:
        if event.event_type == "profile_projection":
            return await self._process_profile_projection(event)
        if event.event_type == "recommendation_refresh":
            return await self._process_recommendation_refresh(event)
        raise ValueError(f"unsupported background event type: {event.event_type}")

    async def _process_profile_projection(self, event: Event) -> dict[str, Any]:
        preferences = _normalize_preferences(event.payload.get("preferences"))
        existing_profile = self.user_profiles.get(event.user_id)
        profile = build_user_profile(
            user_id=event.user_id,
            preferences=preferences,
            existing_profile=existing_profile,
        )
        self.user_profiles.save(profile, updated_at=utc_now())

        refresh_payload = self._request_refresh(
            {
                "user_id": event.user_id,
                "scene": "homepage",
                "trigger": "profile_projection",
                "session_id": event.payload.get("session_id"),
                "preferences": preferences,
                "changed_categories": list(event.payload.get("changed_categories") or []),
                "stable_categories": list(event.payload.get("stable_categories") or []),
                "conflict_categories": list(event.payload.get("conflict_categories") or []),
            },
        )
        return {
            "event_type": event.event_type,
            "profile_updated": True,
            "preferred_categories": list(profile.preferred_categories),
            "preferred_brands": list(profile.preferred_brands),
            "price_range": list(profile.price_range) if profile.price_range is not None else None,
            "enqueued_refresh_event_id": refresh_payload.get("event_id"),
            "target_scene": "homepage",
        }

    async def _process_recommendation_refresh(self, event: Event) -> dict[str, Any]:
        profile = self.user_profiles.get(event.user_id)
        if profile is None:
            profile = UserProfile(user_id=event.user_id, cold_start=True)

        snapshot = await self._build_homepage_snapshot(event=event, profile=profile)
        self.snapshots.save(snapshot)
        if self._hook_bus is not None:
            await self._hook_bus.emit(
                "snapshot.refreshed",
                {
                    "processor_name": self.name,
                    "event_id": event.event_id,
                    "snapshot": snapshot.model_dump(mode="json"),
                },
            )
        return {
            "event_type": event.event_type,
            "scene": snapshot.scene,
            "snapshot_id": snapshot.snapshot_id,
            "product_count": len(snapshot.products),
            "copy_count": len(snapshot.copies),
        }

    async def _build_homepage_snapshot(
        self,
        *,
        event: Event,
        profile: UserProfile,
    ) -> RecommendationSnapshot:
        preferences = _normalize_preferences(event.payload.get("preferences"))
        catalog_result = await self._catalog_worker.run(
            WorkerTask(
                task_id=f"catalog_{event.event_id}_homepage",
                worker_name=self._catalog_worker.name,
                step=1,
                intent="refresh_catalog_candidates",
                input={
                    "scene": "homepage",
                    "scene_context": {},
                    "user_id": event.user_id,
                    "preferences": preferences,
                    "user_profile": profile.model_dump(mode="json"),
                    "allow_snapshot_read": False,
                    "limit": 3,
                },
            ),
            self._tool_registry,
            manager_name=self.name,
            session_id=_as_text(event.payload.get("session_id")),
            hook_bus=self._hook_bus,
        )
        candidate_products = _coerce_products(catalog_result.payload.get("products"))

        inventory_result = await self._inventory_worker.run(
            WorkerTask(
                task_id=f"inventory_{event.event_id}_homepage",
                worker_name=self._inventory_worker.name,
                step=2,
                intent="refresh_inventory_filter",
                input={
                    "products": [product.model_dump(mode="json") for product in candidate_products],
                },
            ),
            self._tool_registry,
            manager_name=self.name,
            session_id=_as_text(event.payload.get("session_id")),
            hook_bus=self._hook_bus,
        )
        available_products = _coerce_products(inventory_result.payload.get("products"))

        copy_result = await self._copy_worker.run(
            WorkerTask(
                task_id=f"copy_{event.event_id}_homepage",
                worker_name=self._copy_worker.name,
                step=3,
                intent="refresh_snapshot_copy",
                input={
                    "message": "",
                    "scene": "homepage",
                    "preferences": preferences,
                    "user_profile": profile.model_dump(mode="json"),
                    "products": [product.model_dump(mode="json") for product in available_products],
                },
            ),
            self._tool_registry,
            manager_name=self.name,
            session_id=_as_text(event.payload.get("session_id")),
            hook_bus=self._hook_bus,
        )
        copies = _coerce_copies(copy_result.payload.get("copies"))
        generated_at = utc_now()
        return RecommendationSnapshot(
            snapshot_id=f"snap_{uuid4().hex[:12]}",
            user_id=event.user_id,
            scene="homepage",
            scene_context={},
            products=available_products,
            copies=copies,
            generated_at=generated_at,
            expires_at=generated_at + SNAPSHOT_TTL,
        )

    def _load_event(self, event: Event | str) -> Event:
        if isinstance(event, Event):
            return event
        stored = self.events.get(event)
        if stored is None:
            raise KeyError(event)
        return stored


def _normalize_preferences(raw_preferences: Any) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in dict(raw_preferences or {}).items()
        if _as_text(value) is not None
    }


def _coerce_products(raw_products: Any) -> list[Product]:
    if not isinstance(raw_products, list):
        return []

    products: list[Product] = []
    for item in raw_products:
        try:
            products.append(Product.model_validate(item))
        except ValidationError:
            continue
    return products


def _coerce_copies(raw_copies: Any) -> list[MarketingCopy]:
    if not isinstance(raw_copies, list):
        return []

    copies: list[MarketingCopy] = []
    for item in raw_copies:
        try:
            copies.append(MarketingCopy.model_validate(item))
        except ValidationError:
            continue
    return copies


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


def _error_message(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return text
    return exc.__class__.__name__


__all__ = [
    "BackgroundRefreshProcessor",
    "REFRESH_SCENES",
    "SNAPSHOT_TTL",
    "SUPPORTED_BACKGROUND_EVENT_TYPES",
    "utc_now",
]
