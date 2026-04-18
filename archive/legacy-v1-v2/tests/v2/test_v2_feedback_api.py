from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.v2.api.session_service import V2SessionService
from main import app, get_v2_session_service


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_feedback_api"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir()
    return path


@pytest.fixture
def v2_service() -> V2SessionService:
    tempdir = _workspace_tempdir()
    service = V2SessionService(tempdir / "v2.sqlite3")
    get_v2_session_service.cache_clear()
    app.dependency_overrides[get_v2_session_service] = lambda: service
    yield service
    app.dependency_overrides.clear()
    get_v2_session_service.cache_clear()


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("click", {"scene": "homepage", "product_id": "sku-redmi-k80", "metadata": {"position": 3}}),
        ("skip", {"scene": "cart", "product_ids": ["sku-ipad-air-6", "sku-iphone-16-pro"], "metadata": {"reason": "already_owned"}}),
        ("purchase", {"scene": "product_page", "product_id": "sku-iphone-16-pro", "metadata": {"order_id": "ord_1001"}}),
    ],
)
def test_v2_feedback_api_accepts_supported_events_and_persists_structure(v2_service: V2SessionService, event_type: str, payload: dict[str, object]):
    with TestClient(app) as client:
        response = client.post("/api/v2/users/u_feedback/feedback-events", json={"event_type": event_type, **payload})

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["event_id"].startswith("evt_")

    stored = v2_service.events.get(body["event_id"])
    assert stored is not None
    assert stored.event_type == event_type
    assert stored.user_id == "u_feedback"
    assert stored.status == "completed"
    assert stored.payload == {
        "scene": payload["scene"],
        "product_id": payload.get("product_id"),
        "product_ids": payload.get("product_ids", []),
        "metadata": payload.get("metadata", {}),
    }


@pytest.mark.asyncio
async def test_v2_feedback_api_enqueues_homepage_refresh_only_and_dedupes(v2_service: V2SessionService):
    with TestClient(app) as client:
        first = client.post(
            "/api/v2/users/u_no_learning/feedback-events",
            json={"event_type": "click", "scene": "homepage", "product_id": "sku-watch-fit-4", "metadata": {"position": 1}},
        )
        second = client.post(
            "/api/v2/users/u_no_learning/feedback-events",
            json={"event_type": "click", "scene": "homepage", "product_id": "sku-redmi-k80", "metadata": {"position": 2}},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert v2_service.user_profiles.get("u_no_learning") is None
    assert v2_service.snapshots.get_latest(user_id="u_no_learning", scene="homepage") is None

    pending_events = v2_service.events.list_by_status("pending")
    assert len(pending_events) == 1
    assert pending_events[0].event_type == "recommendation_refresh"
    assert pending_events[0].payload["scene"] == "homepage"
    assert pending_events[0].payload["trigger"] == "feedback_event"

    processed = await v2_service.process_background_events()
    assert [event.event_type for event in processed] == ["recommendation_refresh"]
    assert v2_service.snapshots.get_latest(user_id="u_no_learning", scene="homepage") is not None
    assert v2_service.snapshots.get_latest(user_id="u_no_learning", scene="default") is None


@pytest.mark.parametrize(
    ("payload", "expected_fragment"),
    [
        ({"event_type": "click", "scene": "product_page"}, "product_page feedback requires product_id"),
        ({"event_type": "skip", "scene": "cart"}, "cart feedback requires product_ids"),
    ],
)
def test_v2_feedback_api_validates_required_scene_context(v2_service: V2SessionService, payload: dict[str, str], expected_fragment: str):
    with TestClient(app) as client:
        response = client.post("/api/v2/users/u_invalid/feedback-events", json=payload)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(expected_fragment in error["msg"] for error in detail)
