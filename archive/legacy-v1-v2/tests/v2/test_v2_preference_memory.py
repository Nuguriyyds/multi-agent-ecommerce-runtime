from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.v2.api.session_service import V2SessionService
from main import app, get_v2_session_service


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_preference_memory"
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


def _create_session(client: TestClient, user_id: str = "u_pref") -> str:
    response = client.post("/api/v2/sessions", json={"user_id": user_id})
    assert response.status_code == 200
    return response.json()["session_id"]


def test_preference_worker_extracts_supported_categories_across_multiple_turns(v2_service: V2SessionService):
    with TestClient(app) as client:
        session_id = _create_session(client)
        first = client.post(f"/api/v2/sessions/{session_id}/messages", json={"message": "budget 3000"})
        second = client.post(
            f"/api/v2/sessions/{session_id}/messages",
            json={"message": "phone apple office"},
        )

    assert first.status_code == 200
    assert second.status_code == 200

    extracted = {
        (item["category"], item["value"])
        for payload in (first.json(), second.json())
        for item in payload["preferences_extracted"]
    }
    assert extracted == {
        ("budget", "3000"),
        ("product_category", "手机"),
        ("brand", "Apple"),
        ("use_case", "办公"),
    }

    session = v2_service.sessions.get(session_id)
    assert session is not None
    assert session.memory["preferences"] == {
        "budget": "3000",
        "product_category": "手机",
        "brand": "Apple",
        "use_case": "办公",
    }
    assert len(session.memory["preference_history"]) == 4
    assert v2_service.user_profiles.get("u_pref") is None


@pytest.mark.asyncio
async def test_stable_preferences_enqueue_profile_projection_and_background_writes_profile(v2_service: V2SessionService):
    with TestClient(app) as client:
        session_id = _create_session(client)
        first = client.post(f"/api/v2/sessions/{session_id}/messages", json={"message": "budget 3000"})
        second = client.post(
            f"/api/v2/sessions/{session_id}/messages",
            json={"message": "phone apple gaming"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["recommendation_refresh_triggered"] is False
    assert second.json()["recommendation_refresh_triggered"] is True

    events = v2_service.events.list_by_status("pending")
    assert len(events) == 1
    assert events[0].event_type == "profile_projection"
    assert events[0].payload["trigger"] == "preference_stable"
    assert events[0].payload["preferences"] == {
        "budget": "3000",
        "product_category": "手机",
        "brand": "Apple",
        "use_case": "游戏",
    }

    session = v2_service.sessions.get(session_id)
    assert session is not None
    assert session.memory["last_projection_trigger"] == "preference_stable"
    assert session.memory["preference_status"]["stable"] is True
    assert v2_service.user_profiles.get("u_pref") is None

    processed = await v2_service.process_background_events()
    assert [event.event_type for event in processed] == [
        "profile_projection",
        "recommendation_refresh",
    ]

    profile = v2_service.user_profiles.get("u_pref")
    assert profile is not None
    assert profile.price_range == (0.0, 3000.0)
    assert profile.preferred_categories == ["手机"]
    assert profile.preferred_brands == ["Apple"]
    assert profile.use_cases == ["游戏"]
    assert v2_service.snapshots.get_latest(user_id="u_pref", scene="homepage") is not None


@pytest.mark.asyncio
async def test_preference_projection_tracks_corrections_in_memory_before_background_write(v2_service: V2SessionService):
    with TestClient(app) as client:
        session_id = _create_session(client)
        first = client.post(
            f"/api/v2/sessions/{session_id}/messages",
            json={"message": "budget 3000 phone apple"},
        )
        await v2_service.process_background_events()
        second = client.post(
            f"/api/v2/sessions/{session_id}/messages",
            json={"message": "office huawei"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["recommendation_refresh_triggered"] is True
    assert second.json()["recommendation_refresh_triggered"] is True

    session = v2_service.sessions.get(session_id)
    assert session is not None
    assert session.memory["preferences"]["brand"] == "Huawei"
    assert session.memory["preferences"]["use_case"] == "办公"
    assert session.memory["last_projection_trigger"] == "preference_corrected"
    assert session.memory["preference_status"]["changed_categories"] == ["brand"]
    assert session.memory["preference_status"]["conflict_categories"] == ["brand"]

    pending = v2_service.events.list_by_status("pending")
    assert len(pending) == 1
    assert pending[0].event_type == "profile_projection"
    assert pending[0].payload["trigger"] == "preference_corrected"
    assert pending[0].payload["preferences"]["brand"] == "Huawei"

    assert v2_service.user_profiles.get("u_pref").preferred_brands == ["Apple"]
    await v2_service.process_background_events()
    assert v2_service.user_profiles.get("u_pref").preferred_brands == ["Apple", "Huawei"]
