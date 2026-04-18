from __future__ import annotations

from app.v3.hardening import HardeningGate
from app.v3.models import (
    CallToolAction,
    CapabilityDescriptor,
    CapabilityKind,
    Observation,
    PermissionPolicy,
    ReplyToUserAction,
    TraceRecord,
)


def make_trace() -> TraceRecord:
    return TraceRecord(
        trace_id="trace-1",
        session_id="session-1",
        turn_number=1,
    )


def make_observation() -> Observation:
    return Observation(
        observation_id="obs-1",
        source="catalog_search",
        summary="Found one matching candidate.",
        payload={"sku": "sku-1"},
        evidence_source="tool:catalog_search",
    )


def make_tool_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name="catalog_search",
        kind=CapabilityKind.tool,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "budget_max": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        output_schema={"type": "array"},
        timeout=3.0,
        permission_tag="catalog.read",
    )


def make_inventory_descriptor() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name="inventory_check",
        kind=CapabilityKind.tool,
        input_schema={
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        timeout=3.0,
        permission_tag="inventory.read",
    )


def test_hardening_gate_rejects_illegal_action_in_clarification_node() -> None:
    gate = HardeningGate()
    trace = make_trace()

    result = gate.evaluate(
        ReplyToUserAction(message="Sony XM5 is the best fit.", observation_ids=["obs-1"]),
        current_node="clarification",
        topic="clarification",
        observations=[make_observation()],
        trace=trace,
    )

    assert result.decision == "fallback"
    assert result.guardrail == "illegal_action"
    assert trace.fallback_reason == "gate:illegal_action"
    assert trace.terminal_state == "fallback"


def test_hardening_gate_rejects_schema_validation_failure() -> None:
    gate = HardeningGate()
    trace = make_trace()

    result = gate.evaluate(
        CallToolAction(capability_name="catalog_search", arguments={"budget_max": 3000}),
        actor="main_agent",
        current_node="candidate_search",
        topic="candidate_search",
        capability=make_tool_descriptor(),
        trace=trace,
    )

    assert result.decision == "fallback"
    assert result.guardrail == "schema_validation"
    assert "missing required keys: query" in (result.reason or "")
    assert trace.fallback_reason == "gate:schema_validation"


def test_hardening_gate_rejects_reply_without_evidence() -> None:
    gate = HardeningGate()
    trace = make_trace()

    result = gate.evaluate(
        ReplyToUserAction(message="Sony XM5 is the best fit.", observation_ids=["obs-missing"]),
        current_node="advice",
        topic="advice",
        observations=[make_observation()],
        trace=trace,
    )

    assert result.decision == "fallback"
    assert result.guardrail == "evidence_missing"
    assert trace.fallback_reason == "gate:evidence_missing"


def test_hardening_gate_rejects_out_of_scope_topic() -> None:
    gate = HardeningGate()
    trace = make_trace()

    result = gate.evaluate(
        CallToolAction(capability_name="catalog_search", arguments={"query": "sony xm5"}),
        actor="main_agent",
        current_node="candidate_search",
        topic="checkout",
        capability=make_tool_descriptor(),
        trace=trace,
    )

    assert result.decision == "fallback"
    assert result.guardrail == "business_boundary"
    assert trace.fallback_reason == "gate:business_boundary"


def test_permission_policy_denies_specialist_tool_outside_allowed_capabilities() -> None:
    gate = HardeningGate()
    trace = make_trace()
    policy = PermissionPolicy(
        actor="comparison_specialist",
        allowed_capabilities=["product_compare"],
        notes="Comparison specialist can only use the comparison tool.",
    )

    result = gate.evaluate(
        CallToolAction(capability_name="inventory_check", arguments={"sku": "sku-1"}),
        actor="comparison_specialist",
        current_node="comparison",
        topic="comparison",
        capability=make_inventory_descriptor(),
        permission_policy=policy,
        trace=trace,
    )

    assert result.decision == "fallback"
    assert result.guardrail == "capability_not_allowed"
    assert trace.fallback_reason == "permission:capability_not_allowed"


def test_permission_policy_check_allows_listed_capability() -> None:
    policy = PermissionPolicy(
        actor="comparison_specialist",
        allowed_capabilities=["product_compare"],
    )

    decision = policy.check("comparison_specialist", "product_compare")

    assert decision.decision == "allow"
    assert decision.capability_name == "product_compare"
