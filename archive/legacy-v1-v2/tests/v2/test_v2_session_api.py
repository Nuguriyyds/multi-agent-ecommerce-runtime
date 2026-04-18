from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.v2.api.session_service import V2SessionService
from main import app, get_v2_session_service


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_session_api"
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


def _create_session(client: TestClient, user_id: str = "u_12345") -> str:
    response = client.post("/api/v2/sessions", json={"user_id": user_id})
    assert response.status_code == 200
    return response.json()["session_id"]


def test_v2_session_api_creates_and_persists_sessions(v2_service: V2SessionService):
    with TestClient(app) as client:
        response = client.post("/api/v2/sessions", json={"user_id": "u_12345"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["manager_type"] == "shopping"
    assert payload["session_id"].startswith("sess_")
    assert payload["created_at"]

    session = v2_service.sessions.get(payload["session_id"])
    assert session is not None
    assert session.user_id == "u_12345"
    assert session.status == "active"


def test_v2_session_api_advisory_turn_updates_memory_and_enqueues_projection(v2_service: V2SessionService):
    with TestClient(app) as client:
        session_id = _create_session(client)
        response = client.post(
            f"/api/v2/sessions/{session_id}/messages",
            json={"message": "phone apple gaming"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == session_id
    assert payload["reply"]
    assert payload["clarification"] is None
    assert payload["products"] == []
    assert payload["comparisons"] == []
    assert payload["copies"] == []
    assert payload["recommendation_refresh_triggered"] is True
    assert payload["agent_details"]["terminal_state"] == "reply_ready"
    assert payload["agent_details"]["workers_called"] == ["preference_worker"]
    assert payload["agent_details"]["steps_executed"] == 1

    extracted = {(item["category"], item["value"]) for item in payload["preferences_extracted"]}
    assert extracted == {
        ("product_category", "手机"),
        ("brand", "Apple"),
        ("use_case", "游戏"),
    }

    turns = v2_service.turns.list_for_session(session_id)
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[1].role == "assistant"

    session = v2_service.sessions.get(session_id)
    assert session is not None
    assert session.memory["preferences"] == {
        "product_category": "手机",
        "brand": "Apple",
        "use_case": "游戏",
    }
    assert session.memory["last_terminal_state"] == "reply_ready"
    assert session.memory["last_projection_trigger"] == "preference_stable"

    assert v2_service.user_profiles.get("u_12345") is None
    pending_events = v2_service.events.list_by_status("pending")
    assert len(pending_events) == 1
    assert pending_events[0].event_type == "profile_projection"


def test_v2_session_api_recommendation_turn_returns_products_but_keeps_profile_async(v2_service: V2SessionService):
    with TestClient(app) as client:
        session_id = _create_session(client)
        response = client.post(
            f"/api/v2/sessions/{session_id}/messages",
            json={"message": "recommend a phone under 3000"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"]
    assert payload["products"]
    assert payload["comparisons"] == []
    assert payload["copies"]
    assert payload["recommendation_refresh_triggered"] is True
    assert payload["agent_details"]["workers_called"] == [
        "preference_worker",
        "catalog_worker",
        "inventory_worker",
        "copy_worker",
    ]
    assert {copy["product_id"] for copy in payload["copies"]} == {
        product["product_id"] for product in payload["products"]
    }
    assert v2_service.user_profiles.get("u_12345") is None
    assert [event.event_type for event in v2_service.events.list_by_status("pending")] == [
        "profile_projection",
    ]


def test_v2_session_api_message_returns_needs_clarification_when_scene_context_missing(v2_service: V2SessionService):
    with TestClient(app) as client:
        session_id = _create_session(client)
        response = client.post(
            f"/api/v2/sessions/{session_id}/messages",
            json={"message": "compare this", "scene": "product_page"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == ""
    assert payload["clarification"]
    assert "product_id" in payload["clarification"]
    assert payload["agent_details"]["terminal_state"] == "needs_clarification"
    assert payload["agent_details"]["workers_called"] == []


def test_v2_session_api_message_returns_fallback_used_for_unsupported_requests(v2_service: V2SessionService):
    with TestClient(app) as client:
        session_id = _create_session(client)
        response = client.post(
            f"/api/v2/sessions/{session_id}/messages",
            json={"message": "merchant refund workflow"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["clarification"] is None
    assert "shopping guidance" in payload["reply"]
    assert payload["preferences_extracted"] == []
    assert payload["agent_details"]["terminal_state"] == "fallback_used"
    assert payload["agent_details"]["workers_called"] == []
