from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.shared.observability.logging_utils import JsonLogFormatter
from app.v2.api.session_service import V2SessionService
from main import app, get_v2_session_service


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_observability"
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


def test_v2_logs_include_trace_ids_and_structured_turn_metadata(v2_service: V2SessionService):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())

    root_logger = logging.getLogger()
    original_level = root_logger.level
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    try:
        with TestClient(app) as client:
            create_response = client.post(
                "/api/v2/sessions",
                headers={"X-Trace-ID": "trace-v2-obs-create"},
                json={"user_id": "u_v2_obs"},
            )
            assert create_response.status_code == 200
            session_id = create_response.json()["session_id"]

            message_response = client.post(
                f"/api/v2/sessions/{session_id}/messages",
                headers={"X-Trace-ID": "trace-v2-obs-message"},
                json={"message": "phone apple gaming"},
            )
            assert message_response.status_code == 200
            asyncio.run(v2_service.process_background_events())

            read_response = client.get(
                "/api/v2/users/u_v2_obs/recommendations",
                headers={"X-Trace-ID": "trace-v2-obs-read"},
                params={"scene": "homepage"},
            )
            assert read_response.status_code == 200
            product_id = read_response.json()["products"][0]["product_id"]

            feedback_response = client.post(
                "/api/v2/users/u_v2_obs/feedback-events",
                headers={"X-Trace-ID": "trace-v2-obs-feedback"},
                json={"event_type": "click", "scene": "homepage", "product_id": product_id, "metadata": {"position": 1}},
            )
            assert feedback_response.status_code == 200
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(original_level)

    assert create_response.headers["X-Trace-ID"] == "trace-v2-obs-create"
    assert message_response.headers["X-Trace-ID"] == "trace-v2-obs-message"
    assert read_response.headers["X-Trace-ID"] == "trace-v2-obs-read"
    assert feedback_response.headers["X-Trace-ID"] == "trace-v2-obs-feedback"

    records = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    assert records

    session_created = next(record for record in records if record.get("message") == "v2_session_created")
    assert session_created["trace_id"] == "trace-v2-obs-create"
    assert session_created["user_id"] == "u_v2_obs"
    assert session_created["session_id"] == session_id
    assert session_created["manager_type"] == "shopping"

    message_handled = next(record for record in records if record.get("message") == "v2_message_handled")
    assert message_handled["trace_id"] == "trace-v2-obs-message"
    assert message_handled["session_id"] == session_id
    assert message_handled["scene"] == "default"
    assert message_handled["terminal_state"] == "reply_ready"
    assert message_handled["workers_called"] == ["preference_worker"]
    assert message_handled["refresh_triggered"] is True
    assert message_handled["product_count"] == 0
    assert message_handled["comparison_count"] == 0
    assert message_handled["copy_count"] == 0
    assert message_handled["latency_ms"] >= 0

    recommendation_read = next(record for record in records if record.get("message") == "v2_recommendation_read")
    assert recommendation_read["trace_id"] == "trace-v2-obs-read"
    assert recommendation_read["user_id"] == "u_v2_obs"
    assert recommendation_read["scene_requested"] == "homepage"
    assert recommendation_read["scene_served"] == "homepage"
    assert recommendation_read["product_count"] > 0

    feedback_recorded = next(record for record in records if record.get("message") == "v2_feedback_recorded")
    assert feedback_recorded["trace_id"] == "trace-v2-obs-feedback"
    assert feedback_recorded["user_id"] == "u_v2_obs"
    assert feedback_recorded["event_type"] == "click"
    assert feedback_recorded["scene"] == "homepage"
    assert feedback_recorded["product_id"] == product_id
    assert feedback_recorded["event_id"].startswith("evt_")
