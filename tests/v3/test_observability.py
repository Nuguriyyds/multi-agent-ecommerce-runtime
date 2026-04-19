from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.v3.config import Settings
from app.v3.models import InvocationRecord, TraceRecord
from app.v3.models.capability import CapabilityKind
from app.v3.models.decision import AgentDecision, ReplyToUserAction
from app.v3.models.trace import TaskRecord
from app.v3.observability import ObservabilityStore


def test_observability_store_summarizes_trace_metrics() -> None:
    store = ObservabilityStore()
    trace = TraceRecord(
        trace_id="trace-test",
        session_id="session-test",
        turn_number=1,
        decisions=[
            AgentDecision(
                action=ReplyToUserAction(message="ok"),
                rationale="terminal",
            )
        ],
        invocations=[
            InvocationRecord(
                invocation_id="inv-1",
                task_id="task-1",
                capability_name="catalog_search",
                capability_kind=CapabilityKind.tool,
                status="succeeded",
            ),
            InvocationRecord(
                invocation_id="inv-2",
                task_id="task-2",
                capability_name="rag_product_knowledge",
                capability_kind=CapabilityKind.mcp_tool,
                status="succeeded",
            ),
        ],
        task_records=[
            TaskRecord(task_id="task-1", task_name="catalog_search", status="done"),
        ],
        guardrail_hits=["business_boundary"],
        terminal_state="reply",
    )

    store.record_turn("session-test", trace, latency_ms=42)
    snapshot = store.snapshot("session-test")

    assert snapshot.runtime.turn_count == 1
    assert snapshot.runtime.avg_turn_latency_ms == 42
    assert snapshot.runtime.total_decisions == 1
    assert snapshot.runtime.total_invocations == 2
    assert snapshot.runtime.guardrail_hit_count == 1
    assert snapshot.runtime.capability_counts == {
        "catalog_search": 1,
        "rag_product_knowledge": 1,
    }
    assert snapshot.recent_turns[0].trace_id == "trace-test"


def test_observability_store_summarizes_feedback_without_durable_memory() -> None:
    store = ObservabilityStore()

    store.record_feedback("session-test", sku="sku-a", signal="interested", source="test")
    store.record_feedback("session-test", sku="sku-a", signal="clicked", source="test")
    store.record_feedback("session-test", sku="sku-b", signal="not_interested", source="test")

    snapshot = store.snapshot("session-test")

    assert snapshot.feedback.total_events == 3
    assert snapshot.feedback.positive_events == 2
    assert snapshot.feedback.negative_events == 1
    assert snapshot.feedback.interest_rate == pytest.approx(0.667)
    assert snapshot.feedback.sku_scores == {"sku-a": 2, "sku-b": -1}


async def _create_client():
    app = create_app(Settings(openai_api_key="", app_debug=False))
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://testserver")
    return app, client


@pytest.mark.asyncio
async def test_observability_endpoint_returns_empty_snapshot_for_new_session() -> None:
    app, client = await _create_client()
    try:
        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]

        resp = await client.get(f"/api/v3/sessions/{session_id}/observability")
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["runtime"]["turn_count"] == 0
    assert body["feedback"]["total_events"] == 0


@pytest.mark.asyncio
async def test_observability_endpoint_updates_after_message_turn() -> None:
    app, client = await _create_client()
    try:
        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]
        await client.post(
            f"/api/v3/sessions/{session_id}/messages",
            json={"message": "V3.1 演示：根据我的通勤耳机偏好，召回商品、查库存、生成首页推荐文案"},
        )

        resp = await client.get(f"/api/v3/sessions/{session_id}/observability")
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["runtime"]["turn_count"] == 1
    assert body["runtime"]["total_decisions"] >= 1
    assert body["runtime"]["total_invocations"] >= 1
    assert "rag_product_knowledge" in body["runtime"]["capability_counts"]
    assert body["recent_turns"][0]["trace_id"].startswith("trace-")


@pytest.mark.asyncio
async def test_recommendation_feedback_updates_observability_without_memory_write() -> None:
    app, client = await _create_client()
    try:
        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]
        record = app.state.v3_session_store.get(session_id)
        assert record is not None

        resp = await client.post(
            f"/api/v3/sessions/{session_id}/recommendation_feedback",
            json={"sku": "sony-wh-1000xm5", "signal": "interested", "source": "test"},
        )
        snapshot = await client.get(f"/api/v3/sessions/{session_id}/observability")
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert resp.status_code == 200
    body = resp.json()
    assert body["memory_policy"] == "session_metric_only"
    assert body["event"]["signal"] == "interested"
    assert record.state.durable_user_memory == {}
    assert snapshot.json()["feedback"]["total_events"] == 1
    assert snapshot.json()["feedback"]["interest_rate"] == 1.0


@pytest.mark.asyncio
async def test_observability_mcp_tool_is_registered_and_callable() -> None:
    app, client = await _create_client()
    try:
        create = await client.post("/api/v3/sessions")
        session_id = create.json()["session_id"]
        provider = app.state.v3_registry.get("observability_metrics_query")

        observation = await provider.invoke({"session_id": session_id})
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert observation.evidence_source == "mcp:observability_metrics_query"
    assert observation.payload["tool_name"] == "observability_metrics_query"
    assert observation.payload["snippet_count"] == 1
    assert observation.payload["snippets"][0]["session_id"] == session_id


@pytest.mark.asyncio
async def test_observability_endpoints_return_404_for_unknown_session() -> None:
    app, client = await _create_client()
    try:
        get_resp = await client.get("/api/v3/sessions/missing/observability")
        post_resp = await client.post(
            "/api/v3/sessions/missing/recommendation_feedback",
            json={"sku": "sku-a", "signal": "clicked"},
        )
    finally:
        await client.aclose()
        await app.state.v3_main_agent.llm_client.aclose()

    assert get_resp.status_code == 404
    assert post_resp.status_code == 404
