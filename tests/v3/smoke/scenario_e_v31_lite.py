from __future__ import annotations

import importlib
from uuid import UUID

import pytest

from tests.v3.smoke.helpers import create_smoke_client

catalog_search_module = importlib.import_module("app.v3.tools.catalog_search")
marketing_copy_module = importlib.import_module("app.v3.tools.marketing_copy_generate")

_V31_LITE_MESSAGE = "V3.1 演示：根据我的通勤耳机偏好，召回商品、查库存、生成首页推荐文案"


@pytest.mark.asyncio
async def test_scenario_e_v31_lite_records_tool_chain_trace(monkeypatch) -> None:
    monkeypatch.setattr(
        catalog_search_module,
        "uuid4",
        lambda: UUID("11111111-1111-1111-1111-111111111111"),
    )
    monkeypatch.setattr(
        marketing_copy_module,
        "uuid4",
        lambda: UUID("bbbbbbbb-bbbb-0000-0000-000000000000"),
    )

    demo_responses = importlib.import_module("app.v3.agents.demo_responses")
    app, client = await create_smoke_client(
        {
            _V31_LITE_MESSAGE: demo_responses.DEMO_MOCK_RESPONSES[_V31_LITE_MESSAGE],
        }
    )
    try:
        create_response = await client.post("/api/v3/sessions")
        session_id = create_response.json()["session_id"]

        turn_response = await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": _V31_LITE_MESSAGE},
        )
        trace_response = await client.get(f"/api/v3/sessions/{session_id}/turns/1/trace")
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    turn_body = turn_response.json()
    trace_body = trace_response.json()

    assert turn_response.status_code == 200
    assert turn_body["status"] == "reply"
    assert turn_body["completed_steps"] == 6
    assert "V3.1 Lite" in turn_body["message"]

    assert trace_response.status_code == 200
    assert trace_body["terminal_state"] == "reply"
    assert [item["action"]["kind"] for item in trace_body["decisions"]] == [
        "call_tool",
        "call_tool",
        "call_tool",
        "call_tool",
        "call_tool",
        "reply_to_user",
    ]
    assert [item["capability_name"] for item in trace_body["invocations"]] == [
        "catalog_search",
        "inventory_check",
        "rag_product_knowledge",
        "preference_profile_update",
        "marketing_copy_generate",
    ]
    assert trace_body["observations"][-1]["observation_id"] == (
        demo_responses.V31_LITE_FINAL_OBSERVATION_ID
    )
    assert trace_body["decisions"][-1]["action"]["observation_ids"] == [
        demo_responses.V31_LITE_FINAL_OBSERVATION_ID
    ]
