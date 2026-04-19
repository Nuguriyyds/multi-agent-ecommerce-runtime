from __future__ import annotations

import pytest

from app.v3.agents import CollaborationRouter, LLMClient, MainAgent
from app.v3.models import (
    AgentDecision,
    LoopState,
    Observation,
    ReplyToUserAction,
    SessionState,
    TurnRuntimeContext,
    TurnTaskBoard,
)
from app.v3.runtime import ContextPacketBuilder


def make_context(
    message: str,
    *,
    session: SessionState | None = None,
    observations: list[Observation] | None = None,
) -> TurnRuntimeContext:
    resolved_session = session or SessionState(session_id="session-route")
    task_board = TurnTaskBoard.create()
    context_packet = ContextPacketBuilder().compress(
        resolved_session,
        task_board,
        latest_user_message=message,
    )
    return TurnRuntimeContext(
        session=resolved_session,
        loop_state=LoopState(
            step_number=0,
            current_node="need_expression",
            observations=observations or [],
        ),
        context_packet=context_packet,
        task_board=task_board,
        trace_id="trace-route",
    )


def obs(source: str, observation_id: str | None = None) -> Observation:
    return Observation(
        observation_id=observation_id or f"obs-{source}",
        source=source,
        summary=f"{source} observation",
        payload={},
        evidence_source=f"test:{source}",
    )


def test_router_returns_same_action_kind_for_same_context() -> None:
    router = CollaborationRouter()
    context = make_context("3000 左右通勤降噪耳机")

    first = router.route(context)
    second = router.route(context)

    assert first.required_action_kind == second.required_action_kind
    assert first.route_key == second.route_key


def test_router_routes_missing_budget_to_clarification() -> None:
    route = CollaborationRouter().route(make_context("帮我推荐一款降噪耳机"))

    assert route.required_action_kind == "ask_clarification"
    assert route.rewrite_action is not None
    assert route.rewrite_action.kind == "ask_clarification"


def test_router_routes_checkout_request_to_fallback() -> None:
    route = CollaborationRouter().route(make_context("就这个了，帮我下单"))

    assert route.required_action_kind == "fallback"
    assert route.rewrite_action is not None
    assert route.rewrite_action.kind == "fallback"


@pytest.mark.parametrize(
    ("sources", "expected_capability", "expected_kind"),
    [
        ([], "catalog_search", "call_tool"),
        (["catalog_search"], "inventory_check", "call_tool"),
        (["catalog_search", "inventory_check"], "rag_product_knowledge", "call_tool"),
        (
            ["catalog_search", "inventory_check", "rag_product_knowledge"],
            "preference_profile_update",
            "call_tool",
        ),
        (
            ["catalog_search", "inventory_check", "rag_product_knowledge", "preference_profile_update"],
            "marketing_copy_generate",
            "call_tool",
        ),
    ],
)
def test_router_routes_v31_lite_tool_chain(
    sources: list[str],
    expected_capability: str,
    expected_kind: str,
) -> None:
    route = CollaborationRouter().route(
        make_context(
            "V3.1 演示：根据我的通勤耳机偏好，召回商品、查库存、生成首页推荐文案",
            observations=[obs(source) for source in sources],
        )
    )

    assert route.required_action_kind == expected_kind
    assert route.rewrite_action is not None
    assert route.rewrite_action.kind == "call_tool"
    assert route.rewrite_action.capability_name == expected_capability


def test_router_routes_v31_lite_to_reply_after_tool_chain() -> None:
    route = CollaborationRouter().route(
        make_context(
            "V3.1 演示：根据我的通勤耳机偏好，召回商品、查库存、生成首页推荐文案",
            observations=[obs(source) for source in (
                "catalog_search",
                "inventory_check",
                "rag_product_knowledge",
                "preference_profile_update",
                "marketing_copy_generate",
            )],
        )
    )

    assert route.required_action_kind == "reply_to_user"
    assert route.rewrite_action is not None
    assert route.rewrite_action.kind == "reply_to_user"


@pytest.mark.parametrize(
    ("sources", "expected_capability"),
    [
        ([], "shopping_brief_specialist"),
        (["shopping_brief_specialist"], "candidate_analysis_specialist"),
        (["shopping_brief_specialist", "candidate_analysis_specialist"], "comparison_specialist"),
        (
            ["shopping_brief_specialist", "candidate_analysis_specialist", "comparison_specialist"],
            "recommendation_rationale_specialist",
        ),
    ],
)
def test_router_routes_specialist_chain(sources: list[str], expected_capability: str) -> None:
    route = CollaborationRouter().route(
        make_context(
            "完整演示：3000 左右通勤降噪耳机，不要 Beats，帮我给出最终推荐",
            observations=[obs(source) for source in sources],
        )
    )

    assert route.required_action_kind == "call_sub_agent"
    assert route.rewrite_action is not None
    assert route.rewrite_action.kind == "call_sub_agent"
    assert route.rewrite_action.capability_name == expected_capability


def test_router_routes_specialist_chain_to_reply_when_done() -> None:
    route = CollaborationRouter().route(
        make_context(
            "完整演示：3000 左右通勤降噪耳机，不要 Beats，帮我给出最终推荐",
            observations=[
                obs("shopping_brief_specialist"),
                obs("candidate_analysis_specialist"),
                obs("comparison_specialist"),
                obs("recommendation_rationale_specialist"),
            ],
        )
    )

    assert route.required_action_kind == "reply_to_user"
    assert route.rewrite_action is not None
    assert route.rewrite_action.kind == "reply_to_user"


@pytest.mark.asyncio
async def test_main_agent_rewrites_llm_action_that_violates_route() -> None:
    llm_client = LLMClient(
        api_key="",
        mock_responses={
            "missing_budget": {
                "action": {
                    "kind": "reply_to_user",
                    "message": "不该直接回答。",
                    "observation_ids": ["obs-missing"],
                },
                "rationale": "bad action",
                "next_task_label": "bad_reply",
                "continue_loop": False,
            }
        },
    )
    agent = MainAgent(llm_client=llm_client)

    decision = await agent.decide(make_context("帮我推荐一款降噪耳机"))

    assert decision.action.kind == "ask_clarification"
    assert "route_policy_rewrite" in decision.rationale
    assert decision.routing_metadata["route_result"] == "rewrite"
    assert decision.routing_metadata["required_action_kind"] == "ask_clarification"
    assert decision.routing_metadata["actual_action_kind"] == "reply_to_user"


@pytest.mark.asyncio
async def test_main_agent_allows_llm_action_that_matches_route() -> None:
    llm_client = LLMClient(
        api_key="",
        mock_responses={
            "default": {
                "action": {
                    "kind": "reply_to_user",
                    "message": "基于已有 observation 回复。",
                    "observation_ids": ["obs-ready"],
                },
                "rationale": "matched route",
                "next_task_label": "reply",
                "continue_loop": False,
            }
        },
    )
    agent = MainAgent(llm_client=llm_client)

    decision = await agent.decide(
        make_context(
            "继续",
            observations=[obs("catalog_search", "obs-ready")],
        )
    )

    assert isinstance(decision.action, ReplyToUserAction)
    assert decision.routing_metadata["route_result"] == "allow"
    assert decision.routing_metadata["required_action_kind"] == "reply_to_user"


def test_agent_decision_accepts_routing_metadata() -> None:
    decision = AgentDecision(
        action=ReplyToUserAction(message="ok", observation_ids=["obs-1"]),
        rationale="test",
        routing_metadata={"route_result": "allow"},
    )

    assert decision.routing_metadata == {"route_result": "allow"}


def test_router_asks_only_for_scene_when_budget_and_category_are_known() -> None:
    session = SessionState(
        session_id="session-scene-only",
        session_working_memory={"active_constraints": {"category": "earphones", "budget_max": 3000}},
        durable_user_memory={"budget": {"max": 3000}},
    )

    route = CollaborationRouter().route(
        make_context("帮我看看 3000 左右的降噪耳机", session=session, observations=[])
    )

    assert route.required_action_kind == "ask_clarification"
    assert route.rewrite_action is not None
    assert route.rewrite_action.kind == "ask_clarification"
    assert route.rewrite_action.missing_slots == ["scene"]
    assert "场景" in route.rewrite_action.question
