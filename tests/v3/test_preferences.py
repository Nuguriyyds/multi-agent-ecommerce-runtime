from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.v3.config import Settings
from app.v3.hooks import HookBus
from app.v3.memory import (
    extract_and_store_preferences,
    extract_preferences,
    get_preference_profile,
    revoke_preference,
)
from app.v3.models import HookEvent, HookPoint, HookResult, SessionState


def _make_session(session_id: str = "session-x", user_id: str | None = None) -> SessionState:
    return SessionState(session_id=session_id, user_id=user_id)


# ---------------------------------------------------------------------------
# Unit tests for extract_preferences (pure regex pass)
# ---------------------------------------------------------------------------


def test_extract_empty_message_returns_empty_dict() -> None:
    assert extract_preferences("") == {}
    assert extract_preferences("   ") == {}


def test_extract_budget_from_yuan_phrase() -> None:
    result = extract_preferences("帮我找 3000 元左右的降噪耳机")
    assert result["budget"] == {"max": 3000, "currency": "CNY"}
    assert result["category"] == "earphones"


def test_extract_budget_from_ceiling_phrase_without_currency_word() -> None:
    result = extract_preferences("1500 内的耳机")
    assert result["budget"] == {"max": 1500, "currency": "CNY"}


def test_extract_scene_commute() -> None:
    result = extract_preferences("我需要通勤用的耳机")
    assert result["scene"] == "commute"
    assert result["category"] == "earphones"


def test_extract_scene_gift_via_chinese_synonym() -> None:
    result = extract_preferences("送礼用的手机")
    assert result["scene"] == "gift"
    assert result["category"] == "phone"


def test_extract_exclusion_from_negation_prefix() -> None:
    result = extract_preferences("通勤用，不要 Beats")
    assert result["scene"] == "commute"
    assert result["exclude_brands"] == ["Beats"]


def test_extract_multiple_brand_exclusions() -> None:
    result = extract_preferences("不要 Sony，也不要 Bose")
    assert set(result["exclude_brands"]) == {"Sony", "Bose"}


def test_extract_brand_mention_without_negation_is_not_exclusion() -> None:
    # 用户只是提到品牌名,没说"不要",不应该当作排斥
    result = extract_preferences("Sony 和 Bose 哪个好")
    assert "exclude_brands" not in result


def test_extract_unrelated_message_returns_empty() -> None:
    assert extract_preferences("你好") == {}


# ---------------------------------------------------------------------------
# extract_and_store_preferences — merges into session_working_memory, emits hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_and_store_writes_to_session_working_memory() -> None:
    bus = HookBus()
    events: list[HookEvent] = []

    async def recorder(event: HookEvent) -> HookResult:
        events.append(event.model_copy(deep=True))
        return HookResult(handler_name="recorder")

    bus.register(HookPoint.memory_write, recorder)
    session = _make_session()

    written = await extract_and_store_preferences(
        session,
        "帮我找 3000 元左右的通勤降噪耳机，不要 Beats",
        hook_bus=bus,
        trace_id="trace-1",
        turn_number=1,
    )

    confirmed = session.session_working_memory["confirmed_preferences"]
    assert confirmed["budget"] == {"max": 3000, "currency": "CNY"}
    assert confirmed["category"] == "earphones"
    assert confirmed["scene"] == "commute"
    assert confirmed["exclude_brands"] == ["Beats"]
    assert set(written.keys()) == {"budget", "category", "scene", "exclude_brands"}
    assert len(events) == 4
    for event in events:
        assert event.hook_point == HookPoint.memory_write
        assert event.payload["decision"] == "allow"
        assert event.payload["source"] == "user_confirmed"
        assert event.payload["target_layer"] == "session_working"


@pytest.mark.asyncio
async def test_extract_and_store_is_idempotent_for_unchanged_values() -> None:
    bus = HookBus()
    events: list[HookEvent] = []

    async def recorder(event: HookEvent) -> HookResult:
        events.append(event.model_copy(deep=True))
        return HookResult(handler_name="recorder")

    bus.register(HookPoint.memory_write, recorder)
    session = _make_session()

    first = await extract_and_store_preferences(session, "通勤用的耳机", hook_bus=bus)
    assert "scene" in first and "category" in first

    # Same input — no new writes, no extra hooks
    second = await extract_and_store_preferences(session, "通勤用的耳机", hook_bus=bus)
    assert second == {}
    assert len(events) == 2  # from the first call only


@pytest.mark.asyncio
async def test_extract_and_store_noop_when_no_preferences_detected() -> None:
    session = _make_session()
    result = await extract_and_store_preferences(session, "你好")
    assert result == {}
    # should not create confirmed_preferences key unnecessarily
    assert "confirmed_preferences" not in session.session_working_memory


# ---------------------------------------------------------------------------
# get_preference_profile — merged view across layers
# ---------------------------------------------------------------------------


def test_profile_view_prefers_session_over_durable_on_key_collision() -> None:
    session = _make_session()
    session.session_working_memory["confirmed_preferences"] = {"budget": {"max": 2000}}
    session.durable_user_memory["budget"] = {"max": 5000}
    session.durable_user_memory["brand_preference"] = "Sony"

    profile = get_preference_profile(session)
    entries_by_key = {entry["key"]: entry for entry in profile}
    assert entries_by_key["budget"]["value"] == {"max": 2000}
    assert entries_by_key["budget"]["layer"] == "session_working"
    assert entries_by_key["brand_preference"]["value"] == "Sony"
    assert entries_by_key["brand_preference"]["layer"] == "durable_user"
    assert all(entry["status"] == "active" for entry in profile)


def test_profile_view_empty_when_both_layers_empty() -> None:
    session = _make_session()
    assert get_preference_profile(session) == []


# ---------------------------------------------------------------------------
# revoke_preference — removes entry, emits revoke hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_removes_session_layer_entry_and_emits_hook() -> None:
    bus = HookBus()
    events: list[HookEvent] = []

    async def recorder(event: HookEvent) -> HookResult:
        events.append(event.model_copy(deep=True))
        return HookResult(handler_name="recorder")

    bus.register(HookPoint.memory_write, recorder)

    session = _make_session()
    session.session_working_memory["confirmed_preferences"] = {
        "scene": "commute",
        "category": "earphones",
    }

    revoked = await revoke_preference(session, "scene", reason="user changed mind", hook_bus=bus)
    assert revoked is True
    assert session.session_working_memory["confirmed_preferences"] == {"category": "earphones"}
    assert len(events) == 1
    payload = events[0].payload
    assert payload["decision"] == "revoke"
    assert payload["memory_key"] == "scene"
    assert payload["target_layer"] == "session_working"
    assert payload["status"] == "revoked"


@pytest.mark.asyncio
async def test_revoke_removes_durable_layer_entry_when_session_absent() -> None:
    session = _make_session(user_id="user-42")
    session.durable_user_memory["brand_preference"] = "Sony"
    revoked = await revoke_preference(session, "brand_preference")
    assert revoked is True
    assert "brand_preference" not in session.durable_user_memory


@pytest.mark.asyncio
async def test_revoke_returns_false_when_key_missing() -> None:
    session = _make_session()
    revoked = await revoke_preference(session, "nonexistent")
    assert revoked is False


# ---------------------------------------------------------------------------
# HTTP API integration tests
# ---------------------------------------------------------------------------


async def _create_client():
    settings = Settings(openai_api_key="", app_debug=False)
    app = create_app(settings)
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://testserver")
    return app, client


@pytest.mark.asyncio
async def test_preferences_endpoint_returns_empty_on_fresh_session() -> None:
    app, client = await _create_client()
    try:
        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]
        response = await client.get(f"/api/v3/sessions/{session_id}/preferences")
        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == session_id
        assert body["entries"] == []
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()


@pytest.mark.asyncio
async def test_preferences_populated_after_message_with_budget_and_exclusion() -> None:
    app, client = await _create_client()
    try:
        # Install a minimal mock for the MainAgent so run_turn returns a simple clarification
        # (the preference extraction runs before run_turn, so any valid turn result works here)
        app.state.v3_main_agent.llm_client.install_mock_responses(
            {
                "default": {
                    "action": {"kind": "ask_clarification", "question": "ok", "missing_slots": []},
                    "rationale": "test",
                    "next_task_label": "noop",
                    "continue_loop": False,
                }
            }
        )

        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]

        await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": "帮我找 3000 元左右的通勤降噪耳机，不要 Beats"},
        )

        resp = await client.get(f"/api/v3/sessions/{session_id}/preferences")
        body = resp.json()
        entries_by_key = {entry["key"]: entry for entry in body["entries"]}
        assert entries_by_key["budget"]["value"] == {"max": 3000, "currency": "CNY"}
        assert entries_by_key["category"]["value"] == "earphones"
        assert entries_by_key["scene"]["value"] == "commute"
        assert entries_by_key["exclude_brands"]["value"] == ["Beats"]
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()


@pytest.mark.asyncio
async def test_revoke_endpoint_removes_preference_and_returns_remaining() -> None:
    app, client = await _create_client()
    try:
        app.state.v3_main_agent.llm_client.install_mock_responses(
            {
                "default": {
                    "action": {"kind": "ask_clarification", "question": "ok", "missing_slots": []},
                    "rationale": "test",
                    "next_task_label": "noop",
                    "continue_loop": False,
                }
            }
        )
        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]
        await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": "3000 元以内的通勤耳机"},
        )

        resp = await client.post(
            f"/api/v3/sessions/{session_id}/preferences/revoke",
            json={"key": "scene"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["revoked_key"] == "scene"
        remaining_keys = {entry["key"] for entry in body["remaining_entries"]}
        assert "scene" not in remaining_keys
        assert "category" in remaining_keys
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()


@pytest.mark.asyncio
async def test_revoke_endpoint_returns_404_for_unknown_key() -> None:
    app, client = await _create_client()
    try:
        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]
        resp = await client.post(
            f"/api/v3/sessions/{session_id}/preferences/revoke",
            json={"key": "nonexistent"},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "preference_key_not_found"
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()


@pytest.mark.asyncio
async def test_personalized_picks_returns_hint_when_no_preferences() -> None:
    app, client = await _create_client()
    try:
        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]
        resp = await client.get(f"/api/v3/sessions/{session_id}/personalized_picks")
        assert resp.status_code == 200
        body = resp.json()
        assert body["picks"] == []
        assert "hint" in body
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()


@pytest.mark.asyncio
async def test_personalized_picks_returns_three_cards_for_commute_earphones() -> None:
    app, client = await _create_client()
    try:
        app.state.v3_main_agent.llm_client.install_mock_responses(
            {
                "default": {
                    "action": {"kind": "ask_clarification", "question": "ok", "missing_slots": []},
                    "rationale": "test",
                    "next_task_label": "noop",
                    "continue_loop": False,
                }
            }
        )
        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]
        await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": "3000 元通勤用的降噪耳机，不要 Beats"},
        )

        resp = await client.get(f"/api/v3/sessions/{session_id}/personalized_picks")
        assert resp.status_code == 200
        body = resp.json()
        picks = body["picks"]
        assert 1 <= len(picks) <= 3
        for pick in picks:
            assert pick["category"] == "earphones"
            assert pick["price"] <= 3000
            assert pick["brand"] != "Beats"
            assert pick["match_reason"]  # non-empty
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()


@pytest.mark.asyncio
async def test_preferences_endpoint_returns_404_for_unknown_session() -> None:
    app, client = await _create_client()
    try:
        resp = await client.get("/api/v3/sessions/nonexistent/preferences")
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "session_not_found"
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()
