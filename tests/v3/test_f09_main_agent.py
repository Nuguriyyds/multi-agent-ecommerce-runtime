from __future__ import annotations

import httpx
import pytest
from httpx import AsyncClient

from app.v3.agents import LLMClient, MainAgent
from app.v3.config import Settings
from app.v3.models import (
    CapabilityDescriptor,
    CapabilityKind,
    LoopState,
    Observation,
    SessionState,
    TurnRuntimeContext,
    TurnTaskBoard,
)
from app.v3.registry import CapabilityRegistry, ToolProvider
from app.v3.runtime import ContextPacketBuilder


class MockCatalogSearchProvider(ToolProvider):
    def __init__(self) -> None:
        super().__init__(
            CapabilityDescriptor(
                name="catalog_search",
                kind=CapabilityKind.tool,
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                output_schema={"type": "object"},
                permission_tag="catalog.read",
            )
        )
        self.calls: list[dict[str, object]] = []

    async def invoke(self, args: dict[str, object]) -> Observation:
        self.calls.append(dict(args))
        return Observation(
            observation_id=f"obs-{len(self.calls)}",
            source="catalog_search",
            summary="Mock catalog result",
            payload={"query": args["query"], "rank": len(self.calls)},
            evidence_source="tool:catalog_search",
        )


def make_session(
    *,
    session_id: str,
    session_working_memory: dict[str, object] | None = None,
    durable_user_memory: dict[str, object] | None = None,
) -> SessionState:
    return SessionState(
        session_id=session_id,
        user_id="user-1",
        session_working_memory=session_working_memory or {},
        durable_user_memory=durable_user_memory or {},
    )


def make_decide_context(
    *,
    session_id: str,
    latest_user_message: str = "继续",
    observations: list[Observation] | None = None,
) -> TurnRuntimeContext:
    session = make_session(
        session_id=session_id,
        session_working_memory={"active_constraints": {"category": "headphones", "budget_max": 3000}},
        durable_user_memory={"budget": {"max": 3000}},
    )
    task_board = TurnTaskBoard.create()
    context_packet = ContextPacketBuilder().compress(
        session,
        task_board,
        latest_user_message=latest_user_message,
    )
    return TurnRuntimeContext(
        session=session,
        loop_state=LoopState(
            step_number=0,
            current_node="advice",
            observations=observations
            or [
                Observation(
                    observation_id="obs-ready",
                    source="catalog_search",
                    summary="Catalog result is ready.",
                    payload={},
                    evidence_source="test:catalog_search",
                )
            ],
        ),
        context_packet=context_packet,
        task_board=task_board,
        trace_id=f"trace-{session_id}",
    )


@pytest.mark.asyncio
async def test_main_agent_happy_path_calls_one_tool_then_replies() -> None:
    registry = CapabilityRegistry()
    tool_provider = MockCatalogSearchProvider()
    registry.register(tool_provider)
    llm_client = LLMClient(
        api_key="",
        mock_responses={
            "happy_path": [
                {
                    "action": {
                        "kind": "call_tool",
                        "capability_name": "catalog_search",
                        "arguments": {"query": "3000 左右 通勤 降噪耳机"},
                    },
                    "rationale": "Need one tool-backed candidate search before replying.",
                    "next_task_label": "search_catalog",
                    "continue_loop": True,
                },
                {
                    "action": {
                        "kind": "reply_to_user",
                        "message": "I found one product within budget and can continue with comparison.",
                        "observation_ids": ["obs-1"],
                    },
                    "rationale": "One catalog observation is enough for the first reply.",
                    "next_task_label": "reply_to_user",
                    "continue_loop": False,
                },
            ]
        },
    )
    agent = MainAgent(
        registry=registry,
        llm_client=llm_client,
    )
    session = make_session(
        session_id="session-happy",
        session_working_memory={"active_constraints": {"category": "headphones", "budget_max": 3000}},
        durable_user_memory={"budget": {"max": 3000, "currency": "CNY"}},
    )

    result = await agent.run_turn(session, "我想买 3000 左右通勤降噪耳机")

    assert result.status == "reply"
    assert result.completed_steps == 2
    assert tool_provider.calls == [{"query": "3000 左右 通勤 降噪耳机"}]
    assert llm_client.scenario_history == ["happy_path", "happy_path"]
    assert "return JSON only" in llm_client.prompt_history[0]

    trace = agent.trace_store.get("session-happy", 1)
    assert trace is not None
    assert trace.terminal_state == "reply"
    assert [decision.action.kind for decision in trace.decisions] == ["call_tool", "reply_to_user"]
    assert [observation.observation_id for observation in trace.observations] == ["obs-1"]


@pytest.mark.asyncio
async def test_main_agent_asks_for_clarification_when_budget_is_missing() -> None:
    llm_client = LLMClient(
        api_key="",
        mock_responses={
            "missing_budget": {
                "action": {
                    "kind": "ask_clarification",
                    "question": "What budget do you want to stay within?",
                    "missing_slots": ["budget"],
                },
                "rationale": "Budget is required before the search can continue safely.",
                "next_task_label": "clarify_budget",
                "continue_loop": False,
            }
        },
    )
    agent = MainAgent(llm_client=llm_client)
    session = make_session(
        session_id="session-clarification",
        session_working_memory={"active_constraints": {"category": "headphones"}},
    )

    result = await agent.run_turn(session, "帮我推荐一款耳机")

    assert result.status == "clarification"
    assert result.message == "What budget do you want to stay within?"
    assert result.completed_steps == 1
    assert llm_client.scenario_history == ["missing_budget"]

    trace = agent.trace_store.get("session-clarification", 1)
    assert trace is not None
    assert trace.terminal_state == "clarification"
    assert [decision.action.kind for decision in trace.decisions] == ["ask_clarification"]


@pytest.mark.asyncio
async def test_main_agent_forces_fallback_when_loop_reaches_max_steps() -> None:
    registry = CapabilityRegistry()
    tool_provider = MockCatalogSearchProvider()
    registry.register(tool_provider)
    llm_client = LLMClient(
        api_key="",
        mock_responses={
            "happy_path": [
                {
                    "action": {
                        "kind": "call_tool",
                        "capability_name": "catalog_search",
                        "arguments": {"query": f"candidate batch {index}"},
                    },
                    "rationale": f"Need another tool-backed observation #{index}.",
                    "next_task_label": f"search_batch_{index}",
                    "continue_loop": True,
                }
                for index in range(1, 9)
            ]
        },
    )
    agent = MainAgent(
        registry=registry,
        llm_client=llm_client,
        settings=Settings(max_steps=1),
    )
    session = make_session(
        session_id="session-loop-limit",
        session_working_memory={"active_constraints": {"category": "headphones", "budget_max": 3000}},
        durable_user_memory={"budget": {"max": 3000}},
    )

    result = await agent.run_turn(session, "我想买 3000 左右通勤降噪耳机")

    assert result.status == "fallback"
    assert result.action.kind == "fallback"
    assert result.action.reason == "loop_exhausted"
    assert result.completed_steps == 1
    assert len(tool_provider.calls) == 1

    trace = agent.trace_store.get("session-loop-limit", 1)
    assert trace is not None
    assert trace.terminal_state == "fallback"
    assert trace.fallback_reason == "runtime:loop_exhausted"
    assert len(trace.decisions) == 1


@pytest.mark.asyncio
async def test_main_agent_falls_back_when_llm_json_is_invalid() -> None:
    llm_client = LLMClient(
        api_key="",
        mock_responses={
            "happy_path": ['{"action":{"kind":"reply_to_user"},"rationale":42}'],
        },
    )
    agent = MainAgent(llm_client=llm_client)
    session = make_session(
        session_id="session-invalid-json",
        session_working_memory={"active_constraints": {"category": "headphones", "budget_max": 3000}},
        durable_user_memory={"budget": {"max": 3000}},
    )

    result = await agent.run_turn(session, "我想买 3000 左右通勤降噪耳机")

    assert result.status == "fallback"
    assert result.action.kind == "fallback"
    assert result.action.reason == "llm_invalid_response"
    assert result.error_summary is None

    trace = agent.trace_store.get("session-invalid-json", 1)
    assert trace is not None
    assert trace.terminal_state == "fallback"
    assert len(trace.decisions) == 1
    assert trace.decisions[0].action.kind == "fallback"


@pytest.mark.asyncio
async def test_main_agent_accepts_remote_payload_wrapped_in_code_fence() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": """```json
{"action":{"kind":"reply_to_user","message":"Grounded reply from fenced JSON.","observation_ids":["obs-ready"]},"rationale":"The latest observation is enough.","next_task_label":"reply","continue_loop":false}
```"""
                        }
                    }
                ]
            },
        )

    client = AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")
    llm_client = LLMClient(
        api_key="demo-key",
        base_url="https://example.test",
        model="demo-model",
        http_client=client,
    )
    agent = MainAgent(llm_client=llm_client)

    decision = await agent.decide(make_decide_context(session_id="session-remote-codefence"))

    assert decision.action.kind == "reply_to_user"
    assert decision.routing_metadata["route_result"] == "allow"
    await client.aclose()


@pytest.mark.asyncio
async def test_main_agent_accepts_remote_payload_wrapped_in_decision_object() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"decision":{"action":{"kind":"reply_to_user","message":"Wrapped decision object.","observation_ids":["obs-ready"]},"rationale":"Decision wrapper from provider.","next_task_label":"reply","continue_loop":false}}'
                        }
                    }
                ]
            },
        )

    client = AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")
    llm_client = LLMClient(
        api_key="demo-key",
        base_url="https://example.test",
        model="demo-model",
        http_client=client,
    )
    agent = MainAgent(llm_client=llm_client)

    decision = await agent.decide(make_decide_context(session_id="session-remote-wrapper"))

    assert decision.action.kind == "reply_to_user"
    assert decision.routing_metadata["route_result"] == "allow"
    await client.aclose()
