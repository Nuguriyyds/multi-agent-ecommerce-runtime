from __future__ import annotations

import json
from collections.abc import Callable
from enum import Enum

import pytest
from pydantic import BaseModel, TypeAdapter

from app.v3.models import (
    ACTION_TYPE_ADAPTER,
    AgentDecision,
    AgentRole,
    AgentTeam,
    AskClarificationAction,
    BackgroundTask,
    BackgroundTaskStatus,
    CallSubAgentAction,
    CallToolAction,
    CapabilityDescriptor,
    CapabilityKind,
    CompressionPolicy,
    ContextPacket,
    DelegationPolicy,
    ErrorCategory,
    FallbackAction,
    HardeningGateResult,
    HookEvent,
    HookPoint,
    HookResult,
    InvocationRecord,
    LLMErrorObservation,
    LoopState,
    MemoryEntry,
    MemoryLayer,
    MemorySource,
    MemoryStatus,
    MemoryWriteDecision,
    Observation,
    PermissionDecision,
    PermissionPolicy,
    PluginCapability,
    PluginManifest,
    PromptLayer,
    ReplyToUserAction,
    RetryPolicy,
    SchedulePolicy,
    SessionState,
    SkillDefinition,
    SkillExecutionContext,
    SpecialistBrief,
    SpecialistObservation,
    TaskRecord,
    TaskStatus,
    TeamTask,
    TeamTaskResult,
    TraceRecord,
    TurnResult,
    TurnRuntimeContext,
    TurnTask,
    TurnTaskBoard,
)


def make_memory_entry() -> MemoryEntry:
    return MemoryEntry(
        key="budget",
        value={"max": 3000, "currency": "CNY"},
        source=MemorySource.user_confirmed,
        layer=MemoryLayer.durable_user,
        observation_id="obs-budget",
        rationale="Explicit user constraint.",
    )


def make_memory_write_decision() -> MemoryWriteDecision:
    return MemoryWriteDecision(
        decision="allow",
        target_layer=MemoryLayer.durable_user,
        memory_key="budget",
        reason="Confirmed by the user.",
    )


def make_observation() -> Observation:
    return Observation(
        observation_id="obs-1",
        source="catalog_search",
        summary="Found two matching headphones.",
        payload={"skus": ["sku-1", "sku-2"]},
        evidence_source="tool:catalog_search",
    )


def make_reply_action() -> ReplyToUserAction:
    return ReplyToUserAction(
        message="XM5 is the best overall fit.",
        observation_ids=["obs-1"],
    )


def make_ask_action() -> AskClarificationAction:
    return AskClarificationAction(
        question="Do you prefer over-ear or in-ear?",
        missing_slots=["wear_style"],
    )


def make_tool_action() -> CallToolAction:
    return CallToolAction(
        capability_name="catalog_search",
        arguments={"category": "headphones", "budget_max": 3000},
    )


def make_sub_agent_action() -> CallSubAgentAction:
    return CallSubAgentAction(
        capability_name="candidate_analysis",
        brief={"candidate_ids": ["sku-1"], "goal": "summarize fit"},
    )


def make_fallback_action() -> FallbackAction:
    return FallbackAction(
        reason="out_of_scope",
        user_message="I can help with shopping advice, not returns or refunds.",
    )


def make_agent_decision() -> AgentDecision:
    return AgentDecision(
        action=make_tool_action(),
        rationale="Need factual candidates before recommending.",
        next_task_label="search_catalog",
        continue_loop=True,
    )


def make_invocation_record() -> InvocationRecord:
    return InvocationRecord(
        invocation_id="inv-1",
        task_id="task-1",
        capability_name="catalog_search",
        capability_kind=CapabilityKind.tool,
        status="succeeded",
        arguments={"budget_max": 3000},
        observation_id="obs-1",
    )


def make_task_record() -> TaskRecord:
    return TaskRecord(
        task_id="task-1",
        task_name="search_catalog",
        status="done",
        invocation_ids=["inv-1"],
        notes=["search completed"],
    )


def make_turn_task() -> TurnTask:
    return TurnTask(
        task_id="task-1",
        name="search_catalog",
        status=TaskStatus.ready,
        depends_on=["task-0"],
        invocations=[make_invocation_record()],
        description="Find candidate products.",
    )


def make_turn_task_board() -> TurnTaskBoard:
    return TurnTaskBoard(
        tasks=[make_turn_task()],
        current_task_id="task-1",
        ready_task_ids=["task-1"],
    )


def make_trace_record() -> TraceRecord:
    return TraceRecord(
        trace_id="trace-1",
        session_id="session-1",
        turn_number=1,
        decisions=[make_agent_decision()],
        task_records=[make_task_record()],
        invocations=[make_invocation_record()],
        observations=[make_observation()],
        guardrail_hits=["schema_validation"],
        memory_reads=["budget"],
        memory_denials=["nickname"],
        fallback_reason=None,
        terminal_state="reply",
    )


def make_compression_policy() -> CompressionPolicy:
    return CompressionPolicy(
        max_messages=6,
        keep_sections=["constraints", "recent_observations"],
        pinned_keys=["budget"],
    )


def make_session_state() -> SessionState:
    return SessionState(
        session_id="session-1",
        user_id="user-1",
        turn_count=3,
        session_working_memory={"last_category": "headphones"},
        durable_user_memory={"budget": {"max": 3000}},
        last_turn_status="clarification",
    )


def make_loop_state() -> LoopState:
    return LoopState(
        step_number=2,
        current_node="candidate_search",
        current_task_id="task-1",
        ready_task_ids=["task-1"],
        blocked_task_ids=["task-2"],
        observations=[make_observation()],
    )


def make_context_packet() -> ContextPacket:
    return ContextPacket(
        session_id="session-1",
        latest_user_message="I want the best ANC around 3000 RMB.",
        active_constraints={"category": "headphones", "budget_max": 3000},
        session_working_memory={"scene": "commute"},
        durable_user_memory={"brand_preference": "Sony"},
        recent_observation_ids=["obs-1"],
        compression_policy=make_compression_policy(),
    )


def make_turn_result() -> TurnResult:
    return TurnResult(
        session_id="session-1",
        turn_number=3,
        status="reply",
        message="Sony XM5 is the stronger all-around pick.",
        action=make_reply_action(),
        trace_id="trace-1",
        completed_steps=2,
    )


def make_turn_runtime_context() -> TurnRuntimeContext:
    return TurnRuntimeContext(
        session=make_session_state(),
        loop_state=make_loop_state(),
        context_packet=make_context_packet(),
        task_board=make_turn_task_board(),
        turn_result=make_turn_result(),
        trace_id="trace-1",
    )


def make_capability_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name="catalog_search",
        kind=CapabilityKind.tool,
        input_schema={"type": "object"},
        output_schema={"type": "array"},
        timeout=5.0,
        permission_tag="catalog.read",
        description="Search mock product catalog.",
    )


def make_plugin_capability() -> PluginCapability:
    return PluginCapability(
        name="rag_product_knowledge",
        kind=CapabilityKind.mcp_tool,
        permission_tag="rag.read",
        description="Read-only product knowledge.",
    )


def make_delegation_policy() -> DelegationPolicy:
    return DelegationPolicy(
        preferred_roles=[AgentRole.shopping_brief, AgentRole.comparison],
        fallback_to_tool=True,
        rationale="Use specialists for synthesis, tools for facts.",
    )


def make_specialist_brief() -> SpecialistBrief:
    return SpecialistBrief(
        brief_id="brief-1",
        task_id="task-1",
        role=AgentRole.candidate_analysis,
        goal="Analyze shortlist fit.",
        constraints={"budget_max": 3000},
        allowed_capabilities=["catalog_search"],
        context_packet=make_context_packet(),
    )


def make_specialist_observation() -> SpecialistObservation:
    return SpecialistObservation(
        observation_id="obs-specialist-1",
        role=AgentRole.comparison,
        brief_id="brief-1",
        summary="XM5 wins on battery and call quality.",
        payload={"winner": "XM5"},
        evidence_source="specialist:comparison",
    )


def make_team_task() -> TeamTask:
    return TeamTask(
        team_task_id="team-task-1",
        role=AgentRole.comparison,
        description="Compare XM5 with QC Ultra.",
        allowed_capabilities=["product_compare"],
        brief=make_specialist_brief(),
    )


def make_team_task_result() -> TeamTaskResult:
    return TeamTaskResult(
        team_task_id="team-task-1",
        status="done",
        observation_summary="Comparison completed.",
        observation=make_specialist_observation(),
    )


def make_agent_team() -> AgentTeam:
    return AgentTeam(
        team_id="team-1",
        roles=[AgentRole.shopping_brief, AgentRole.comparison],
        delegation_policy=make_delegation_policy(),
        capability_map={"comparison": ["product_compare"]},
    )


def make_hook_event() -> HookEvent:
    return HookEvent(
        hook_point=HookPoint.decision,
        session_id="session-1",
        trace_id="trace-1",
        turn_number=3,
        payload={"action": "call_tool"},
    )


def make_hook_result() -> HookResult:
    return HookResult(
        handler_name="audit_hook",
        accepted=True,
        metadata={"latency_ms": 3},
        note="Recorded decision event.",
    )


def make_permission_policy() -> PermissionPolicy:
    return PermissionPolicy(
        actor="comparison_specialist",
        allowed_capabilities=["product_compare"],
        denied_capabilities=["inventory_check"],
        notes="Specialist only compares products.",
    )


def make_permission_decision() -> PermissionDecision:
    return PermissionDecision(
        decision="allow",
        actor="main_agent",
        capability_name="catalog_search",
    )


def make_background_task() -> BackgroundTask:
    return BackgroundTask(
        task_id="bg-1",
        task_type="reindex_catalog",
        status=BackgroundTaskStatus.pending,
        payload={"catalog_version": "2026-04-18"},
    )


def make_schedule_policy() -> SchedulePolicy:
    return SchedulePolicy(
        name="nightly_maintenance",
        cadence="0 0 * * *",
        enabled=True,
    )


def make_skill_definition() -> SkillDefinition:
    return SkillDefinition(
        name="shopping_brief_flow",
        steps=["extract_constraints", "check_missing_slots"],
        required_capabilities=["catalog_search"],
        applicability_notes=["Use when user asks for recommendations."],
    )


def make_skill_execution_context() -> SkillExecutionContext:
    return SkillExecutionContext(
        skill_name="shopping_brief_flow",
        session_id="session-1",
        step_index=1,
        state={"missing_slots": ["budget"]},
    )


def make_plugin_manifest() -> PluginManifest:
    return PluginManifest(
        name="rag-plugin",
        version="0.1.0",
        capabilities=[make_plugin_capability()],
        metadata={"owner": "tests"},
    )


def make_retry_policy() -> RetryPolicy:
    return RetryPolicy(
        max_retries=2,
        backoff_seconds=0.5,
        retryable_categories=[ErrorCategory.provider, ErrorCategory.llm],
    )


def make_hardening_gate_result() -> HardeningGateResult:
    return HardeningGateResult(
        decision="fallback",
        reason="business_boundary",
        guardrail="out_of_scope",
        memory_write_decision=make_memory_write_decision(),
        fallback_action=make_fallback_action(),
    )


def make_llm_error_observation() -> LLMErrorObservation:
    return LLMErrorObservation(
        observation_id="obs-llm-1",
        summary="LLM returned malformed JSON.",
        payload={"response_id": "resp-1"},
        evidence_source="llm:mock",
        retryable=True,
        raw_output="{not-json}",
    )


MODEL_FACTORIES: list[Callable[[], BaseModel]] = [
    make_session_state,
    make_loop_state,
    make_context_packet,
    make_compression_policy,
    make_turn_result,
    make_turn_runtime_context,
    make_invocation_record,
    make_task_record,
    make_trace_record,
    make_turn_task,
    make_turn_task_board,
    make_memory_entry,
    make_memory_write_decision,
    make_observation,
    make_agent_decision,
    make_reply_action,
    make_ask_action,
    make_tool_action,
    make_sub_agent_action,
    make_fallback_action,
    make_hardening_gate_result,
    make_capability_descriptor,
    make_plugin_capability,
    make_agent_team,
    make_team_task,
    make_team_task_result,
    make_specialist_brief,
    make_specialist_observation,
    make_delegation_policy,
    make_hook_event,
    make_hook_result,
    make_permission_policy,
    make_permission_decision,
    make_background_task,
    make_schedule_policy,
    make_skill_definition,
    make_skill_execution_context,
    make_plugin_manifest,
    make_retry_policy,
    make_llm_error_observation,
]

ENUM_CASES: list[tuple[type[Enum], Enum]] = [
    (AgentRole, AgentRole.shopping_brief),
    (BackgroundTaskStatus, BackgroundTaskStatus.pending),
    (CapabilityKind, CapabilityKind.tool),
    (ErrorCategory, ErrorCategory.provider),
    (HookPoint, HookPoint.turn_start),
    (MemoryLayer, MemoryLayer.session_working),
    (MemorySource, MemorySource.user_confirmed),
    (MemoryStatus, MemoryStatus.active),
    (PromptLayer, PromptLayer.platform),
    (TaskStatus, TaskStatus.ready),
]


def test_roundtrip_coverage_threshold() -> None:
    covered_type_count = len(MODEL_FACTORIES) + len(ENUM_CASES) + 1
    assert covered_type_count >= 32


@pytest.mark.parametrize("factory", MODEL_FACTORIES, ids=lambda factory: factory.__name__)
def test_model_roundtrip(factory: Callable[[], BaseModel]) -> None:
    instance = factory()
    payload = instance.model_dump_json()
    restored = type(instance).model_validate_json(payload)

    assert restored == instance


@pytest.mark.parametrize(
    ("enum_type", "value"),
    ENUM_CASES,
    ids=[enum_type.__name__ for enum_type, _ in ENUM_CASES],
)
def test_enum_roundtrip(enum_type: type[Enum], value: Enum) -> None:
    adapter = TypeAdapter(enum_type)
    payload = adapter.dump_json(value)
    restored = adapter.validate_json(payload)

    assert restored is value


@pytest.mark.parametrize(
    ("payload", "expected_type"),
    [
        ({"kind": "reply_to_user", "message": "done", "observation_ids": ["obs-1"]}, ReplyToUserAction),
        (
            {"kind": "ask_clarification", "question": "budget?", "missing_slots": ["budget"]},
            AskClarificationAction,
        ),
        (
            {"kind": "call_tool", "capability_name": "catalog_search", "arguments": {"q": "anc"}},
            CallToolAction,
        ),
        (
            {
                "kind": "call_sub_agent",
                "capability_name": "comparison",
                "brief": {"candidate_ids": ["sku-1", "sku-2"]},
            },
            CallSubAgentAction,
        ),
        (
            {
                "kind": "fallback",
                "reason": "out_of_scope",
                "user_message": "I can only help with shopping guidance.",
            },
            FallbackAction,
        ),
    ],
    ids=["reply_to_user", "ask_clarification", "call_tool", "call_sub_agent", "fallback"],
)
def test_action_discriminated_union_dispatch(payload: dict[str, object], expected_type: type[BaseModel]) -> None:
    action = ACTION_TYPE_ADAPTER.validate_json(json.dumps(payload))

    assert isinstance(action, expected_type)
    assert ACTION_TYPE_ADAPTER.validate_json(ACTION_TYPE_ADAPTER.dump_json(action)) == action
