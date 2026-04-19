from __future__ import annotations

import importlib
from uuid import UUID

import pytest

from tests.v3.smoke.helpers import create_smoke_client

demo_responses = importlib.import_module("app.v3.agents.demo_responses")
specialist_base_module = importlib.import_module("app.v3.specialists.base")

_FULL_CHAIN_MESSAGE = "完整演示：3000 左右通勤降噪耳机，不要 Beats，帮我给出最终推荐"


@pytest.mark.asyncio
async def test_scenario_d_full_specialist_chain_records_sub_agent_trace(monkeypatch) -> None:
    uuid_values = iter(
        [
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID("00000000-0000-0000-0000-000000000002"),
            UUID("00000000-0000-0000-0000-000000000003"),
            UUID("00000000-0000-0000-0000-aaaaaaaaaaaa"),
        ]
    )
    monkeypatch.setattr(specialist_base_module, "uuid4", lambda: next(uuid_values))

    app, client = await create_smoke_client(
        {
            _FULL_CHAIN_MESSAGE: demo_responses.DEMO_MOCK_RESPONSES[_FULL_CHAIN_MESSAGE],
        }
    )
    try:
        create_response = await client.post("/api/v3/sessions")
        session_id = create_response.json()["session_id"]
        turn_response = await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": _FULL_CHAIN_MESSAGE},
        )
        trace_response = await client.get(f"/api/v3/sessions/{session_id}/turns/1/trace")
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    turn_body = turn_response.json()
    trace_body = trace_response.json()

    assert turn_response.status_code == 200
    assert turn_body["status"] == "reply"
    assert turn_body["completed_steps"] == 5
    assert "Sony WH-1000XM5" in turn_body["message"]

    assert trace_response.status_code == 200
    assert trace_body["terminal_state"] == "reply"
    assert [item["action"]["kind"] for item in trace_body["decisions"]] == [
        "call_sub_agent",
        "call_sub_agent",
        "call_sub_agent",
        "call_sub_agent",
        "reply_to_user",
    ]
    assert [item["capability_name"] for item in trace_body["invocations"]] == [
        "shopping_brief_specialist",
        "candidate_analysis_specialist",
        "comparison_specialist",
        "recommendation_rationale_specialist",
    ]
    assert all(item["capability_kind"] == "sub_agent" for item in trace_body["invocations"])
    assert [item["source"] for item in trace_body["observations"]] == [
        "shopping_brief_specialist",
        "candidate_analysis_specialist",
        "comparison_specialist",
        "recommendation_rationale_specialist",
    ]
    assert all(item["evidence_source"] for item in trace_body["observations"])
    assert trace_body["observations"][-1]["observation_id"] == (
        demo_responses.FULL_CHAIN_FINAL_OBSERVATION_ID
    )
    assert trace_body["decisions"][-1]["action"]["observation_ids"] == [
        demo_responses.FULL_CHAIN_FINAL_OBSERVATION_ID
    ]
