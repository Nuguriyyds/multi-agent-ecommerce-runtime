from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from app.v3.config import Settings, get_settings
from app.v3.hardening import HardeningGate
from app.v3.hooks import HookBus
from app.v3.models import (
    AgentDecision,
    FallbackAction,
    PermissionPolicy,
    PromptLayer,
    SessionState,
    TurnResult,
    TurnRuntimeContext,
)
from app.v3.prompts import PromptAlreadyRegistered, PromptRegistry
from app.v3.registry import CapabilityRegistry
from app.v3.runtime import SerialExecutor, TraceStore

from .collaboration_router import CollaborationRoute, CollaborationRouter
from .llm_client import LLMClient, LLMClientError

_SYSTEM_FALLBACK_REASONS = {"llm_unavailable", "llm_invalid_response"}

_DEFAULT_PROMPTS: tuple[tuple[PromptLayer, str, str, str], ...] = (
    (
        PromptLayer.platform,
        "main_agent_platform",
        "v1",
        (
            "You are the V3 main shopping assistant agent. "
            "Stay inside the bounded observe-decide-act loop and return JSON only."
        ),
    ),
    (
        PromptLayer.scenario,
        "shopping_assistant",
        "v1",
        (
            "You only handle shopping guidance. "
            "Prefer ask_clarification when key constraints are missing. "
            "Never fabricate evidence."
        ),
    ),
    (
        PromptLayer.role,
        "main_agent",
        "v1",
        (
            "Choose exactly one action from reply_to_user, ask_clarification, call_tool, "
            "call_sub_agent, fallback."
        ),
    ),
    (
        PromptLayer.task_brief,
        "decision_contract",
        "v1",
        (
            "Output one AgentDecision JSON object with fields: action, rationale, "
            "next_task_label, continue_loop. "
            "For reply_to_user include observation_ids. "
            "For call_tool include capability_name and arguments."
        ),
    ),
)


class MainAgent:
    def __init__(
        self,
        *,
        registry: CapabilityRegistry | None = None,
        llm_client: LLMClient | None = None,
        prompt_registry: PromptRegistry | None = None,
        hardening_gate: HardeningGate | None = None,
        trace_store: TraceStore | None = None,
        hook_bus: HookBus | None = None,
        permission_policy: PermissionPolicy | None = None,
        settings: Settings | None = None,
        collaboration_router: CollaborationRouter | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._registry = registry or CapabilityRegistry()
        self._prompt_registry = prompt_registry or PromptRegistry()
        self._llm_client = llm_client or LLMClient(
            api_key=self._settings.openai_api_key,
            base_url=self._settings.openai_base_url,
            model=self._settings.openai_model,
        )
        self._logger = logging.getLogger(__name__)
        self._collaboration_router = collaboration_router or CollaborationRouter()
        self._prompt_selection = {
            PromptLayer.platform: "main_agent_platform",
            PromptLayer.scenario: "shopping_assistant",
            PromptLayer.role: "main_agent",
            PromptLayer.task_brief: "decision_contract",
        }

        self._register_default_prompts()
        self._executor = SerialExecutor(
            decision_provider=self.decide,
            registry=self._registry,
            hardening_gate=hardening_gate or HardeningGate(),
            trace_store=trace_store or TraceStore(),
            hook_bus=hook_bus,
            permission_policy=permission_policy or PermissionPolicy(actor="main_agent"),
            initial_node="need_expression",
            max_steps=self._settings.max_steps,
        )

    @property
    def trace_store(self) -> TraceStore:
        return self._executor.trace_store

    @property
    def llm_client(self) -> LLMClient:
        return self._llm_client

    @property
    def prompt_registry(self) -> PromptRegistry:
        return self._prompt_registry

    async def run_turn(self, session: SessionState, user_message: str) -> TurnResult:
        return await self._executor.run_turn(session, user_message)

    async def decide(self, context: TurnRuntimeContext) -> AgentDecision:
        context.loop_state.current_node = self._infer_current_node(context)
        route = self._collaboration_router.route(context)
        prompt = self.build_prompt(context, route=route)
        self._logger.info(
            "MainAgent decision start trace=%s step=%s node=%s route=%s required_action=%s",
            context.trace_id,
            context.loop_state.step_number,
            context.loop_state.current_node,
            route.route_key,
            route.required_action_kind,
        )

        try:
            raw_output = await self._llm_client.complete_decision_json(
                prompt=prompt,
                context=context,
            )
            decision = AgentDecision.model_validate_json(raw_output)
        except LLMClientError as exc:
            self._logger.warning("MainAgent decision degraded due to LLM transport/mock failure: %s", exc)
            decision = self._fallback_decision(
                reason="llm_unavailable",
                user_message="I need to stop here because the decision engine is unavailable right now.",
                rationale=f"LLM call failed: {exc}",
            )
        except (ValidationError, ValueError, TypeError) as exc:
            self._logger.warning("MainAgent decision degraded due to invalid LLM payload: %s", exc)
            decision = self._fallback_decision(
                reason="llm_invalid_response",
                user_message="I need to stop here because the decision engine returned an invalid response.",
                rationale=f"LLM response could not be parsed: {exc}",
            )

        decision = self._apply_route_policy(decision, route)
        self._logger.info(
            "MainAgent decision success trace=%s action=%s route_result=%s",
            context.trace_id,
            decision.action.kind,
            decision.routing_metadata.get("route_result"),
        )
        return decision

    def build_prompt(self, context: TurnRuntimeContext, *, route: CollaborationRoute | None = None) -> str:
        route = route or self._collaboration_router.route(context)
        current_turn_context = json.dumps(
            {
                "loop_state": context.loop_state.model_dump(mode="json"),
                "context_packet": context.context_packet.model_dump(mode="json"),
                "collaboration_route": route.model_dump(mode="json"),
                "available_capabilities": [
                    descriptor.model_dump(mode="json")
                    for descriptor in self._registry.list()
                ],
                "observation_summaries": [
                    observation.model_dump(mode="json")
                    for observation in context.loop_state.observations
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        return self._prompt_registry.assemble(
            {
                **self._prompt_selection,
                "current_turn_context": current_turn_context,
            }
        )

    def _apply_route_policy(
        self,
        decision: AgentDecision,
        route: CollaborationRoute,
    ) -> AgentDecision:
        actual_kind = decision.action.kind
        metadata = {
            "route_key": route.route_key,
            "required_action_kind": route.required_action_kind,
            "actual_action_kind": actual_kind,
            "route_reason": route.reason,
        }
        if isinstance(decision.action, FallbackAction) and decision.action.reason in _SYSTEM_FALLBACK_REASONS:
            decision.routing_metadata = {
                **decision.routing_metadata,
                **metadata,
                "route_result": "system_fallback",
            }
            return decision

        if actual_kind == route.required_action_kind:
            decision.routing_metadata = {
                **decision.routing_metadata,
                **metadata,
                "route_result": "allow",
            }
            return decision

        if route.rewrite_action is None:
            rewritten = self._fallback_decision(
                reason="route_policy_missing_rewrite",
                user_message="I need to stop here because the collaboration route could not produce a safe action.",
                rationale=(
                    "route_policy_rewrite: "
                    f"original_action={actual_kind}, required_action={route.required_action_kind}, "
                    "rewrite_action_missing"
                ),
            )
        else:
            rewritten = AgentDecision(
                action=route.rewrite_action,
                rationale=(
                    "route_policy_rewrite: "
                    f"original_action={actual_kind}, required_action={route.required_action_kind}. "
                    f"{route.reason}"
                ),
                next_task_label=f"route_{route.route_key}",
                continue_loop=route.rewrite_action.kind in {"call_tool", "call_sub_agent"},
            )

        rewritten.routing_metadata = {
            **metadata,
            "route_result": "rewrite",
            "rewritten_action_kind": rewritten.action.kind,
        }
        return rewritten

    def _register_default_prompts(self) -> None:
        for layer, name, version, text in _DEFAULT_PROMPTS:
            try:
                self._prompt_registry.register(layer, name, version, text)
            except PromptAlreadyRegistered:
                continue

    def _infer_current_node(self, context: TurnRuntimeContext) -> str:
        if context.context_packet.unanswered_clarifications:
            return "clarification"

        if context.loop_state.observations:
            if context.context_packet.comparison_dimensions:
                return "comparison"
            return "advice"

        return "need_expression"

    @staticmethod
    def _fallback_decision(
        *,
        reason: str,
        user_message: str,
        rationale: str,
    ) -> AgentDecision:
        return AgentDecision(
            action=FallbackAction(
                reason=reason,
                user_message=user_message,
            ),
            rationale=rationale,
            next_task_label="fallback",
            continue_loop=False,
        )


__all__ = ["MainAgent"]
