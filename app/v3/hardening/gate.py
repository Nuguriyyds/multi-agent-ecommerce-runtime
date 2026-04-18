from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from app.v3.models import (
    ACTION_TYPE_ADAPTER,
    Action,
    AskClarificationAction,
    CallSubAgentAction,
    CallToolAction,
    CapabilityDescriptor,
    CapabilityKind,
    FallbackAction,
    HardeningGateResult,
    Observation,
    PermissionPolicy,
    ReplyToUserAction,
    TraceRecord,
)
from app.v3.permissions import check_permission

ALLOWED_ACTION_KINDS = frozenset(
    {"reply_to_user", "ask_clarification", "call_tool", "call_sub_agent", "fallback"}
)
ALLOWED_BUSINESS_TOPICS = frozenset(
    {"need_expression", "clarification", "candidate_search", "comparison", "advice"}
)
DEFAULT_NODE_ACTION_WHITELIST: dict[str, frozenset[str]] = {
    "need_expression": frozenset({"ask_clarification", "call_tool", "call_sub_agent", "fallback"}),
    "clarification": frozenset({"ask_clarification", "call_tool", "call_sub_agent", "fallback"}),
    "candidate_search": frozenset({"reply_to_user", "call_tool", "call_sub_agent", "fallback"}),
    "comparison": frozenset({"reply_to_user", "call_tool", "call_sub_agent", "fallback"}),
    "advice": frozenset({"reply_to_user", "call_tool", "call_sub_agent", "fallback"}),
}
OUT_OF_SCOPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "checkout": ("下单", "buy it for me", "place the order", "submit the order"),
    "payment": ("支付", "付款", "pay for it", "charge my card"),
    "account": ("账户", "账号", "log in", "reset my password"),
    "after_sales": ("售后", "退货", "退款", "refund", "return", "complaint", "投诉"),
}

InvocationAction = CallToolAction | CallSubAgentAction


class HardeningGate:
    def __init__(
        self,
        *,
        allowed_actions: Sequence[str] | None = None,
        allowed_topics: Sequence[str] | None = None,
        node_action_whitelist: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        self._allowed_actions = frozenset(allowed_actions or ALLOWED_ACTION_KINDS)
        self._allowed_topics = frozenset(allowed_topics or ALLOWED_BUSINESS_TOPICS)
        raw_node_whitelist = node_action_whitelist or DEFAULT_NODE_ACTION_WHITELIST
        self._node_action_whitelist = {
            node: frozenset(actions) for node, actions in raw_node_whitelist.items()
        }
        self._logger = logging.getLogger(__name__)

    def evaluate(
        self,
        action: Action | Mapping[str, Any],
        *,
        actor: str = "main_agent",
        current_node: str | None = None,
        topic: str | None = None,
        user_message: str | None = None,
        observations: Sequence[Observation] = (),
        capability: CapabilityDescriptor | None = None,
        permission_policy: PermissionPolicy | None = None,
        step_number: int | None = None,
        max_steps: int | None = None,
        trace: TraceRecord | None = None,
    ) -> HardeningGateResult:
        parsed_action = self._coerce_action(action, trace=trace)
        if parsed_action is None:
            return self._reject(
                guardrail="illegal_action",
                reason="action payload does not match the V3 action union",
                user_message="I need to re-check the next step before I continue.",
                source="gate",
                trace=trace,
            )

        if isinstance(parsed_action, (CallToolAction, CallSubAgentAction)) and permission_policy is not None:
            permission_result = self.check_permission(
                actor,
                capability or parsed_action.capability_name,
                permission_policy=permission_policy,
                trace=trace,
            )
            if permission_result.decision != "allow":
                return permission_result

        action_result = self._check_action_legality(
            parsed_action,
            current_node=current_node,
            step_number=step_number,
            max_steps=max_steps,
            trace=trace,
        )
        if action_result is not None:
            return action_result

        schema_result = self._check_schema(parsed_action, capability=capability, trace=trace)
        if schema_result is not None:
            return schema_result

        evidence_result = self._check_evidence(parsed_action, observations=observations, trace=trace)
        if evidence_result is not None:
            return evidence_result

        business_result = self._check_business_boundary(
            parsed_action,
            topic=topic,
            user_message=user_message,
            trace=trace,
        )
        if business_result is not None:
            return business_result

        self._logger.info("Hardening gate allowed %s for actor=%s", parsed_action.kind, actor)
        return HardeningGateResult(decision="allow")

    def check_permission(
        self,
        actor: str,
        capability: CapabilityDescriptor | str,
        *,
        permission_policy: PermissionPolicy,
        trace: TraceRecord | None = None,
    ) -> HardeningGateResult:
        decision = check_permission(permission_policy, actor, capability)
        if decision.decision == "allow":
            self._logger.info(
                "Permission allow actor=%s capability=%s",
                decision.actor,
                decision.capability_name,
            )
            return HardeningGateResult(decision="allow")

        return self._reject(
            guardrail="capability_not_allowed",
            reason=decision.reason or "capability denied by permission policy",
            user_message="I can only use approved capabilities for this step.",
            source="permission",
            trace=trace,
        )

    def _coerce_action(
        self,
        action: Action | Mapping[str, Any],
        *,
        trace: TraceRecord | None = None,
    ) -> Action | None:
        if hasattr(action, "kind"):
            return action  # type: ignore[return-value]

        try:
            return ACTION_TYPE_ADAPTER.validate_python(action)
        except Exception:
            self._mark_trace(trace, source="gate", guardrail="illegal_action")
            self._logger.warning("Rejected non-conforming action payload")
            return None

    def _check_action_legality(
        self,
        action: Action,
        *,
        current_node: str | None,
        step_number: int | None,
        max_steps: int | None,
        trace: TraceRecord | None,
    ) -> HardeningGateResult | None:
        if action.kind not in self._allowed_actions:
            return self._reject(
                guardrail="illegal_action",
                reason=f"action kind {action.kind} is not in the V3 whitelist",
                user_message="I need to re-check the next step before I continue.",
                source="gate",
                trace=trace,
            )

        if max_steps is not None and step_number is not None and step_number >= max_steps and action.kind != "fallback":
            return self._reject(
                guardrail="step_limit",
                reason=f"step limit reached: {step_number} >= {max_steps}",
                user_message="I need to stop here because this turn reached the maximum number of steps.",
                source="gate",
                trace=trace,
            )

        if current_node is None:
            return None

        allowed_actions = self._node_action_whitelist.get(current_node)
        if allowed_actions is None or action.kind in allowed_actions:
            return None

        return self._reject(
            guardrail="illegal_action",
            reason=f"action {action.kind} is not allowed while current_node={current_node}",
            user_message="I need to gather the missing details before I answer directly.",
            source="gate",
            trace=trace,
        )

    def _check_schema(
        self,
        action: Action,
        *,
        capability: CapabilityDescriptor | None,
        trace: TraceRecord | None,
    ) -> HardeningGateResult | None:
        if capability is None:
            return None

        if isinstance(action, CallToolAction):
            if capability.kind not in {CapabilityKind.tool, CapabilityKind.mcp_tool}:
                return self._reject(
                    guardrail="illegal_action",
                    reason=f"capability {capability.name} is not a tool or mcp_tool",
                    user_message="I need to re-check which capability should handle this request.",
                    source="gate",
                    trace=trace,
                )
            payload = action.arguments
        elif isinstance(action, CallSubAgentAction):
            if capability.kind is not CapabilityKind.sub_agent:
                return self._reject(
                    guardrail="illegal_action",
                    reason=f"capability {capability.name} is not a sub_agent",
                    user_message="I need to re-check which specialist should handle this request.",
                    source="gate",
                    trace=trace,
                )
            payload = action.brief
        else:
            return None

        schema_error = self._validate_schema(payload, capability.input_schema)
        if schema_error is None:
            return None

        return self._reject(
            guardrail="schema_validation",
            reason=schema_error,
            user_message="I need to gather the request in the expected format before I continue.",
            source="gate",
            trace=trace,
        )

    def _check_evidence(
        self,
        action: Action,
        *,
        observations: Sequence[Observation],
        trace: TraceRecord | None,
    ) -> HardeningGateResult | None:
        if not isinstance(action, ReplyToUserAction):
            return None

        if not action.observation_ids:
            return self._reject(
                guardrail="evidence_missing",
                reason="reply_to_user requires at least one observation_id",
                user_message="I need tool-backed evidence before I can make that recommendation.",
                source="gate",
                trace=trace,
            )

        observation_ids = {item.observation_id for item in observations}
        missing_ids = [item for item in action.observation_ids if item not in observation_ids]
        if not missing_ids:
            return None

        return self._reject(
            guardrail="evidence_missing",
            reason=f"unknown observation_ids referenced: {', '.join(missing_ids)}",
            user_message="I need tool-backed evidence before I can make that recommendation.",
            source="gate",
            trace=trace,
        )

    def _check_business_boundary(
        self,
        action: Action,
        *,
        topic: str | None,
        user_message: str | None,
        trace: TraceRecord | None,
    ) -> HardeningGateResult | None:
        normalized_topic = self._normalize_topic(topic, user_message=user_message)
        if normalized_topic is None or normalized_topic in self._allowed_topics:
            return None

        if isinstance(action, (AskClarificationAction, FallbackAction)):
            return None

        return self._reject(
            guardrail="business_boundary",
            reason=f"topic {normalized_topic} is outside the V3.0 shopping-assistant boundary",
            user_message="I can help with shopping guidance, but not orders, payments, accounts, or after-sales issues.",
            source="gate",
            trace=trace,
        )

    def _normalize_topic(self, topic: str | None, *, user_message: str | None) -> str | None:
        if topic is not None:
            return topic.strip().lower()

        if not user_message:
            return None

        lowered_message = user_message.lower()
        for inferred_topic, keywords in OUT_OF_SCOPE_KEYWORDS.items():
            if any(keyword in lowered_message for keyword in keywords):
                return inferred_topic
        return None

    def _validate_schema(self, payload: Any, schema: Mapping[str, Any]) -> str | None:
        if not schema:
            return None
        return self._validate_against_schema(payload, schema, path="input")

    def _validate_against_schema(
        self,
        payload: Any,
        schema: Mapping[str, Any],
        *,
        path: str,
    ) -> str | None:
        expected_type = schema.get("type")
        if expected_type == "object":
            if not isinstance(payload, dict):
                return f"{path} must be an object"

            properties = schema.get("properties", {})
            required_keys = schema.get("required", [])
            missing_keys = [key for key in required_keys if key not in payload]
            if missing_keys:
                return f"{path} is missing required keys: {', '.join(missing_keys)}"

            if schema.get("additionalProperties", True) is False:
                extra_keys = [key for key in payload if key not in properties]
                if extra_keys:
                    return f"{path} has unexpected keys: {', '.join(extra_keys)}"

            for key, value in payload.items():
                property_schema = properties.get(key)
                if property_schema is None:
                    continue
                error = self._validate_against_schema(value, property_schema, path=f"{path}.{key}")
                if error is not None:
                    return error
            return None

        if expected_type == "array":
            if not isinstance(payload, list):
                return f"{path} must be an array"

            item_schema = schema.get("items")
            if not isinstance(item_schema, Mapping):
                return None

            for index, item in enumerate(payload):
                error = self._validate_against_schema(item, item_schema, path=f"{path}[{index}]")
                if error is not None:
                    return error
            return None

        if expected_type == "string" and not isinstance(payload, str):
            return f"{path} must be a string"
        if expected_type == "integer" and (not isinstance(payload, int) or isinstance(payload, bool)):
            return f"{path} must be an integer"
        if expected_type == "number" and not isinstance(payload, (int, float)):
            return f"{path} must be a number"
        if expected_type == "boolean" and not isinstance(payload, bool):
            return f"{path} must be a boolean"

        if expected_type == "null" and payload is not None:
            return f"{path} must be null"
        return None

    def _reject(
        self,
        *,
        guardrail: str,
        reason: str,
        user_message: str,
        source: str,
        trace: TraceRecord | None,
    ) -> HardeningGateResult:
        self._mark_trace(trace, source=source, guardrail=guardrail)
        self._logger.warning("Hardening reject source=%s guardrail=%s reason=%s", source, guardrail, reason)
        return HardeningGateResult(
            decision="fallback",
            reason=reason,
            guardrail=guardrail,
            fallback_action=FallbackAction(reason=guardrail, user_message=user_message),
        )

    def _mark_trace(self, trace: TraceRecord | None, *, source: str, guardrail: str) -> None:
        if trace is None:
            return
        marker = f"{source}:{guardrail}"
        trace.fallback_reason = marker
        if guardrail not in trace.guardrail_hits:
            trace.guardrail_hits.append(guardrail)
        trace.terminal_state = "fallback"


__all__ = [
    "ALLOWED_ACTION_KINDS",
    "ALLOWED_BUSINESS_TOPICS",
    "DEFAULT_NODE_ACTION_WHITELIST",
    "HardeningGate",
]
