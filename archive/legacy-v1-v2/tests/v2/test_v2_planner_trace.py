from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.v2.api.schemas import FeedbackEventRequest, RecommendationReadRequest
from app.v2.api.session_service import V2SessionService
from app.v2.core.models import ToolSpec, TurnPlan, TurnPlanStep, UserProfile
from app.v2.core.policy import PolicyDecision
from app.v2.core.runtime import ToolRegistry, WorkerTask
from app.v2.managers.planning import ShoppingTurnPlanner
from app.v2.workers.catalog import CatalogWorker
from app.v2.workers.preference import PreferenceWorker
from main import app, get_v2_session_service


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_planner_trace"
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


def _create_session(client: TestClient, user_id: str = "u_trace") -> str:
    response = client.post("/api/v2/sessions", json={"user_id": user_id})
    assert response.status_code == 200
    return response.json()["session_id"]


def test_v2_planner_builds_expected_sequences_and_step_cap_fallback():
    planner = ShoppingTurnPlanner()
    allow = PolicyDecision(decision="allow", code="allowed", reason="ok")
    clarify = PolicyDecision(decision="clarify", code="missing_scene_context", reason="need context", missing_fields=("product_id",))

    advisory_plan = planner.build_plan(scene="default", message="budget 3000", decision=allow)
    assert advisory_plan.intent == "advisory"
    assert [step.name for step in advisory_plan.steps] == [
        "preference_worker",
        "profile.request_projection",
    ]

    recommendation_plan = planner.build_plan(scene="default", message="recommend a phone under 3000", decision=allow)
    assert recommendation_plan.intent == "recommendation"
    assert [step.name for step in recommendation_plan.steps] == [
        "preference_worker",
        "catalog_worker",
        "inventory_worker",
        "copy_worker",
        "profile.request_projection",
    ]

    comparison_plan = planner.build_plan(scene="product_page", message="compare this", decision=allow)
    assert comparison_plan.intent == "comparison"
    assert [step.name for step in comparison_plan.steps] == [
        "preference_worker",
        "catalog_worker",
        "inventory_worker",
        "comparison_worker",
        "profile.request_projection",
    ]

    clarify_plan = planner.build_plan(scene="product_page", message="compare this", decision=clarify)
    assert clarify_plan.terminal_state == "needs_clarification"
    assert [step.name for step in clarify_plan.steps] == ["clarify"]

    capped = planner.validate(
        TurnPlan(
            intent="recommendation",
            terminal_state="reply_ready",
            steps=[
                TurnPlanStep(name="preference_worker", step=1),
                TurnPlanStep(name="catalog_worker", step=2),
                TurnPlanStep(name="inventory_worker", step=3),
                TurnPlanStep(name="comparison_worker", step=4),
                TurnPlanStep(name="copy_worker", step=5),
                TurnPlanStep(name="profile.request_projection", step=6),
                TurnPlanStep(name="copy_worker", step=7),
                TurnPlanStep(name="inventory_worker", step=8),
                TurnPlanStep(name="catalog_worker", step=9),
            ],
        ),
    )
    assert capped.terminal_state == "fallback_used"
    assert capped.fallback_reason == "step_cap_exceeded"


@pytest.mark.asyncio
async def test_preference_worker_reads_memory_and_extracts_preferences_via_tools():
    observed: list[str] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="session.read_memory", description="read memory", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"),
        lambda payload: observed.append("session.read_memory") or {"memory": {"preferences": {}}},
    )
    registry.register(
        ToolSpec(name="profile.extract_preferences", description="extract preferences", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"),
        lambda payload: observed.append("profile.extract_preferences") or {
            "signals": [{"category": "budget", "value": "3000", "confidence": 0.9, "source_turn": 1}],
        },
    )

    worker = PreferenceWorker()
    result = await worker.run(
        WorkerTask(task_id="task_pref_tools", worker_name="preference_worker", step=1, intent="extract_preferences", input={"message": "budget 3000", "source_turn": 1, "session_id": "sess_1"}),
        registry,
        manager_name="shopping",
        session_id="sess_1",
        turn_id="turn_1",
    )

    assert observed == ["session.read_memory", "profile.extract_preferences"]
    assert result.signals[0].category == "budget"


@pytest.mark.asyncio
async def test_catalog_worker_reads_feedback_summary_before_search():
    observed: list[str] = []
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="feedback.read_summary", description="read feedback summary", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"),
        lambda payload: observed.append("feedback.read_summary") or {
            "boosted_categories": ["手机"],
            "boosted_brands": [],
            "suppressed_product_ids": [],
        },
    )
    registry.register(
        ToolSpec(name="catalog.search_products", description="search products", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"),
        lambda payload: observed.append("catalog.search_products") or {"products": []},
    )

    worker = CatalogWorker()
    await worker.run(
        WorkerTask(task_id="task_catalog_feedback", worker_name="catalog_worker", step=2, intent="select_products", input={"scene": "default", "scene_context": {}, "user_id": "u_1", "preferences": {}}),
        registry,
        manager_name="shopping",
        session_id="sess_1",
        turn_id="turn_1",
    )

    assert observed == ["feedback.read_summary", "catalog.search_products"]


def test_v2_trace_api_returns_plan_tasks_and_projection(v2_service: V2SessionService):
    with TestClient(app) as client:
        session_id = _create_session(client)
        message_response = client.post(
            f"/api/v2/sessions/{session_id}/messages",
            json={"message": "phone apple gaming"},
        )
        assert message_response.status_code == 200

        trace_response = client.get(f"/api/v2/sessions/{session_id}/turns/1/trace")

    assert trace_response.status_code == 200
    payload = trace_response.json()
    assert payload["session_id"] == session_id
    assert payload["user_turn_number"] == 1
    assert payload["terminal_state"] == "reply_ready"
    assert [step["name"] for step in payload["plan"]["steps"]] == [
        "preference_worker",
        "profile.request_projection",
    ]
    assert payload["projection"]["requested"] is True
    assert payload["projection"]["event_type"] == "profile_projection"
    assert payload["projection"]["event_id"]
    assert payload["projection"]["trigger"] == "preference_stable"
    assert {task["record_type"] for task in payload["tasks"]} == {"conversation", "worker", "tool"}
    assert any(task["tool_name"] == "session.read_memory" for task in payload["tasks"])
    assert any(task["tool_name"] == "profile.request_projection" for task in payload["tasks"])


@pytest.mark.asyncio
async def test_feedback_skip_suppresses_later_homepage_snapshot_results(v2_service: V2SessionService):
    v2_service.user_profiles.save(UserProfile(user_id="u_feedback_ranking", preferred_categories=["手机"], cold_start=False))

    miss = await v2_service.read_recommendations("u_feedback_ranking", RecommendationReadRequest(scene="homepage"))
    assert miss.products == []
    assert miss.pending_refresh is True
    await v2_service.process_background_events()

    before = await v2_service.read_recommendations("u_feedback_ranking", RecommendationReadRequest(scene="homepage"))
    skipped_product_id = before.products[0].product_id

    await v2_service.record_feedback_event(
        "u_feedback_ranking",
        FeedbackEventRequest(event_type="skip", scene="homepage", product_id=skipped_product_id),
    )
    await v2_service.process_background_events()

    after = await v2_service.read_recommendations("u_feedback_ranking", RecommendationReadRequest(scene="homepage"))
    assert skipped_product_id not in [product.product_id for product in after.products]
