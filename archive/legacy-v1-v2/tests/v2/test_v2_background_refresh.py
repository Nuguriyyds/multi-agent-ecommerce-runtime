from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.shared.data.product_catalog import ProductCatalog
from app.v2 import BackgroundRefreshProcessor
from app.v2.api.schemas import SessionMessageRequest
from app.v2.api.session_service import V2SessionService
from app.v2.core.models import RecommendationSnapshot


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_background_refresh"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir()
    return path


def _catalog_by_id():
    return {product.product_id: product for product in ProductCatalog().get_fallback_products(limit=100)}


class BrokenCatalog:
    async def list_products(self):
        raise RuntimeError("catalog refresh unavailable")


async def _emit_projection_event(service: V2SessionService, *, user_id: str):
    session_id = service.create_session(user_id).session_id
    first = await service.handle_message(session_id, SessionMessageRequest(message="budget 3000"))
    second = await service.handle_message(session_id, SessionMessageRequest(message="phone apple gaming"))
    assert first.recommendation_refresh_triggered is False
    assert second.recommendation_refresh_triggered is True
    pending_events = service.events.list_by_status("pending")
    assert len(pending_events) == 1
    assert pending_events[0].event_type == "profile_projection"
    return pending_events[0]


@pytest.mark.asyncio
async def test_v2_background_refresh_consumes_projection_chain_and_persists_homepage_snapshot():
    service = V2SessionService(_workspace_tempdir() / "v2.sqlite3")
    event = await _emit_projection_event(service, user_id="u_bg_success")

    processed = await service.process_background_events()

    assert [item.event_type for item in processed] == ["profile_projection", "recommendation_refresh"]
    assert processed[0].event_id == event.event_id
    profile = service.user_profiles.get("u_bg_success")
    assert profile is not None
    assert profile.preferred_categories == ["手机"]

    homepage_snapshot = service.snapshots.get_latest(user_id="u_bg_success", scene="homepage")
    assert homepage_snapshot is not None
    assert homepage_snapshot.products
    assert homepage_snapshot.copies
    assert homepage_snapshot.expires_at == homepage_snapshot.generated_at + timedelta(hours=24)

    assert service.tasks.get(f"bg_{event.event_id}_attempt_1").status == "completed"
    refresh_event = next(item for item in processed if item.event_type == "recommendation_refresh")
    assert service.tasks.get(f"bg_{refresh_event.event_id}_attempt_1").status == "completed"


@pytest.mark.asyncio
async def test_v2_background_refresh_failure_retains_old_snapshot():
    service = V2SessionService(_workspace_tempdir() / "v2.sqlite3")
    projection_event = await _emit_projection_event(service, user_id="u_bg_failure")
    await service.background_processor.process_event(projection_event)

    catalog = _catalog_by_id()
    existing_snapshot = RecommendationSnapshot(
        snapshot_id="snap_existing_homepage",
        user_id="u_bg_failure",
        scene="homepage",
        products=[catalog["sku-redmi-k80"].model_copy(deep=True)],
        generated_at=datetime(2026, 4, 17, 8, 0, tzinfo=timezone.utc),
        expires_at=datetime(2026, 4, 18, 8, 0, tzinfo=timezone.utc),
    )
    service.snapshots.save(existing_snapshot)

    failing_processor = BackgroundRefreshProcessor(
        events=service.events,
        user_profiles=service.user_profiles,
        snapshots=service.snapshots,
        tasks=service.tasks,
        hook_bus=service.hook_bus,
        prompt_registry=service.prompt_registry,
        product_catalog=BrokenCatalog(),
    )

    processed = await failing_processor.process_pending_events()
    assert [item.event_type for item in processed] == ["recommendation_refresh"]

    refresh_event = processed[0]
    stored_event = service.events.get(refresh_event.event_id)
    assert stored_event is not None
    assert stored_event.status == "failed"
    assert stored_event.retry_count == 1
    assert "catalog refresh unavailable" in str(stored_event.error)

    task = service.tasks.get(f"bg_{refresh_event.event_id}_attempt_1")
    assert task is not None
    assert task.status == "failed"
    assert "catalog refresh unavailable" in str(task.error)

    homepage_snapshot = service.snapshots.get_latest(user_id="u_bg_failure", scene="homepage")
    assert homepage_snapshot is not None
    assert homepage_snapshot.snapshot_id == existing_snapshot.snapshot_id


@pytest.mark.asyncio
async def test_v2_background_refresh_retry_uses_new_attempt_task_record():
    service = V2SessionService(_workspace_tempdir() / "v2.sqlite3")
    projection_event = await _emit_projection_event(service, user_id="u_bg_retry")
    await service.background_processor.process_event(projection_event)
    refresh_event = service.events.list_by_status("pending")[0]

    failing_processor = BackgroundRefreshProcessor(
        events=service.events,
        user_profiles=service.user_profiles,
        snapshots=service.snapshots,
        tasks=service.tasks,
        hook_bus=service.hook_bus,
        prompt_registry=service.prompt_registry,
        product_catalog=BrokenCatalog(),
    )
    first_attempt = await failing_processor.process_event(refresh_event)
    assert first_attempt.status == "failed"
    assert first_attempt.retry_count == 1

    service.events.save(first_attempt.model_copy(update={"status": "pending", "error": None, "processed_at": None}))

    processed = await service.process_background_events()
    assert [item.event_type for item in processed] == ["recommendation_refresh"]

    stored_event = service.events.get(refresh_event.event_id)
    assert stored_event is not None
    assert stored_event.status == "completed"
    assert stored_event.retry_count == 1

    first_task = service.tasks.get(f"bg_{refresh_event.event_id}_attempt_1")
    second_task = service.tasks.get(f"bg_{refresh_event.event_id}_attempt_2")
    assert first_task is not None
    assert second_task is not None
    assert first_task.status == "failed"
    assert second_task.status == "completed"

    homepage_snapshot = service.snapshots.get_latest(user_id="u_bg_retry", scene="homepage")
    assert homepage_snapshot is not None
    assert homepage_snapshot.expires_at == homepage_snapshot.generated_at + timedelta(hours=24)
