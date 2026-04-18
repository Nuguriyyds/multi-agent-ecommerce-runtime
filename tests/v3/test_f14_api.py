from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.v3.config import Settings


def _configure_mock_responses(app, mock_responses: dict[str, object]) -> None:
    llm_client = app.state.v3_main_agent.llm_client
    llm_client._mock_responses = {  # noqa: SLF001 - test-only deterministic mock injection
        key: llm_client._normalize_sequence(value)  # noqa: SLF001
        for key, value in mock_responses.items()
    }
    llm_client._mock_cursors.clear()  # noqa: SLF001
    llm_client.prompt_history.clear()
    llm_client.scenario_history.clear()


async def _create_client(mock_responses: dict[str, object] | None = None):
    settings = Settings(openai_api_key="", app_debug=False)
    app = create_app(settings)
    if mock_responses is not None:
        _configure_mock_responses(app, mock_responses)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://testserver")
    return app, client


@pytest.mark.asyncio
async def test_create_session_endpoint_returns_session_id_trace_header_and_latency() -> None:
    app, client = await _create_client()
    try:
        response = await client.post("/api/v3/sessions")
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert response.status_code == 201
    body = response.json()

    assert body["session_id"].startswith("session-")
    assert isinstance(body["latency_ms"], int)
    assert response.headers["X-Trace-ID"].startswith("http-")
    assert app.state.v3_session_store.get(body["session_id"]) is not None


@pytest.mark.asyncio
async def test_message_endpoint_runs_one_turn_and_returns_turn_result() -> None:
    app, client = await _create_client(
        {
            "missing_budget": {
                "action": {
                    "kind": "ask_clarification",
                    "question": "你的预算大概是多少？",
                    "missing_slots": ["budget"],
                },
                "rationale": "Budget is required before the search can continue safely.",
                "next_task_label": "clarify_budget",
                "continue_loop": False,
            }
        }
    )
    try:
        create_response = await client.post("/api/v3/sessions")
        session_id = create_response.json()["session_id"]
        response = await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": "帮我推荐一款降噪耳机。"},
        )
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert response.status_code == 200
    body = response.json()

    assert body["session_id"] == session_id
    assert body["status"] == "clarification"
    assert body["message"] == "你的预算大概是多少？"
    assert body["completed_steps"] == 1
    assert isinstance(body["latency_ms"], int)
    assert response.headers["X-Trace-ID"] == body["trace_id"]


@pytest.mark.asyncio
async def test_trace_endpoint_returns_saved_trace_and_latency() -> None:
    app, client = await _create_client(
        {
            "missing_budget": {
                "action": {
                    "kind": "ask_clarification",
                    "question": "你的预算大概是多少？",
                    "missing_slots": ["budget"],
                },
                "rationale": "Budget is required before the search can continue safely.",
                "next_task_label": "clarify_budget",
                "continue_loop": False,
            }
        }
    )
    try:
        create_response = await client.post("/api/v3/sessions")
        session_id = create_response.json()["session_id"]
        message_response = await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": "帮我推荐一款降噪耳机。"},
        )
        turn_number = message_response.json()["turn_number"]
        response = await client.get(f"/api/v3/sessions/{session_id}/turns/{turn_number}/trace")
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert response.status_code == 200
    body = response.json()

    assert body["session_id"] == session_id
    assert body["turn_number"] == 1
    assert body["terminal_state"] == "clarification"
    assert body["decisions"][0]["action"]["kind"] == "ask_clarification"
    assert isinstance(body["latency_ms"], int)
    assert response.headers["X-Trace-ID"] == body["trace_id"]


@pytest.mark.asyncio
async def test_message_endpoint_returns_409_after_max_turns_are_reached() -> None:
    app, client = await _create_client()
    try:
        create_response = await client.post("/api/v3/sessions")
        session_id = create_response.json()["session_id"]
        record = app.state.v3_session_store.get(session_id)
        assert record is not None
        record.state.turn_count = app.state.settings.session_max_turns
        record.state.session_working_memory = {"budget": {"max": 3000}}

        response = await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": "继续推荐吧"},
        )
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert response.status_code == 409
    body = response.json()

    assert body["detail"]["code"] == "session_expired"
    assert body["detail"]["reason"] == "max_turns_reached"
    assert isinstance(body["latency_ms"], int)
    assert response.headers["X-Trace-ID"].startswith("http-")
    assert record.expired_reason == "max_turns_reached"
    assert record.state.session_working_memory == {}


@pytest.mark.asyncio
async def test_message_endpoint_returns_409_after_idle_timeout() -> None:
    app, client = await _create_client()
    try:
        create_response = await client.post("/api/v3/sessions")
        session_id = create_response.json()["session_id"]
        record = app.state.v3_session_store.get(session_id)
        assert record is not None
        record.state.session_working_memory = {"active_constraints": {"category": "headphones"}}
        record.last_activity_at = record.last_activity_at - timedelta(
            minutes=app.state.settings.session_idle_minutes + 1
        )

        response = await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": "继续推荐吧"},
        )
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert response.status_code == 409
    body = response.json()

    assert body["detail"]["code"] == "session_expired"
    assert body["detail"]["reason"] == "idle_timeout"
    assert isinstance(body["latency_ms"], int)
    assert response.headers["X-Trace-ID"].startswith("http-")
    assert record.expired_reason == "idle_timeout"
    assert record.state.session_working_memory == {}
