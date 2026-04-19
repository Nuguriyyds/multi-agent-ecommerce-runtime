from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.v3.models import (
    Action,
    AskClarificationAction,
    CallSubAgentAction,
    CallToolAction,
    FallbackAction,
    ReplyToUserAction,
    TurnRuntimeContext,
)
from app.v3.models.base import V3Model

ActionKind = Literal[
    "reply_to_user",
    "ask_clarification",
    "call_tool",
    "call_sub_agent",
    "fallback",
]

_CHECKOUT_KEYWORDS = ("下单", "支付", "付款", "账户", "账号", "售后", "退款", "退货", "buy it for me", "pay")
_SHOPPING_KEYWORDS = ("耳机", "降噪", "推荐", "商品", "礼物", "预算", "通勤", "买", "挑")
_SPECIALIST_KEYWORDS = ("完整演示", "最终推荐", "完整推荐", "候选分析", "商品对比", "推荐理由")
_V31_KEYWORDS = ("v3.1 演示", "召回商品", "查库存", "首页推荐文案")
_EXPLORATORY_KEYWORDS = ("看看", "看下", "推荐", "挑", "选", "帮我看", "帮我挑")
_PURCHASE_INTENT_KEYWORDS = ("想买", "买", "购买", "入手")

_SPECIALIST_CHAIN = (
    "shopping_brief_specialist",
    "candidate_analysis_specialist",
    "comparison_specialist",
    "recommendation_rationale_specialist",
)
_V31_TOOL_CHAIN = (
    "catalog_search",
    "inventory_check",
    "rag_product_knowledge",
    "preference_profile_update",
    "marketing_copy_generate",
)


class CollaborationRoute(V3Model):
    route_key: str
    required_action_kind: ActionKind
    reason: str
    rewrite_action: Action | None = None


class CollaborationRouter:
    def route(self, context: TurnRuntimeContext) -> CollaborationRoute:
        message = context.context_packet.latest_user_message.strip()
        normalized = message.lower()

        if _contains_any(normalized, _CHECKOUT_KEYWORDS):
            return self._fallback_route("business_boundary", "request crosses shopping guidance boundary")

        if self._is_v31_lite_request(normalized):
            return self._route_v31_lite(context)

        if self._is_specialist_request(normalized):
            return self._route_specialist_chain(context)

        if self._has_observations(context):
            return self._reply_route("observations_ready", "existing observations are enough for a grounded reply", context)

        if self._budget_missing(context) and self._is_shopping_request(normalized):
            return self._clarification_route("missing_constraints", "shopping request is missing required constraints")

        if self._scene_missing(context) and self._needs_scene_clarification(normalized):
            return self._clarification_route(
                "missing_scene",
                "exploratory shopping request is missing the primary usage scene",
            )

        if self._is_shopping_request(normalized):
            return self._tool_route(
                "shopping_data_lookup",
                "shopping request can start with deterministic catalog lookup",
                _catalog_search_action(message),
            )

        return self._clarification_route("ambiguous_request", "request is not specific enough for tool or specialist use")

    def _route_v31_lite(self, context: TurnRuntimeContext) -> CollaborationRoute:
        sources = self._sources(context)
        message = context.context_packet.latest_user_message
        if "catalog_search" not in sources:
            return self._tool_route("v31_lite.catalog_search", "V3.1 Lite starts with product recall", _v31_catalog_search_action())
        if "inventory_check" not in sources:
            return self._tool_route("v31_lite.inventory_check", "V3.1 Lite checks primary candidate inventory", _v31_inventory_check_action())
        if "rag_product_knowledge" not in sources:
            return self._tool_route("v31_lite.rag_product_knowledge", "V3.1 Lite retrieves MCP-backed product knowledge", _v31_rag_action())
        if "preference_profile_update" not in sources:
            return self._tool_route("v31_lite.preference_profile_update", "V3.1 Lite proposes auditable preference-state updates", _v31_preference_action())
        if "marketing_copy_generate" not in sources:
            return self._tool_route("v31_lite.marketing_copy_generate", "V3.1 Lite generates homepage recommendation copy", _v31_copy_action())
        return self._reply_route("v31_lite.reply", "all V3.1 Lite tool observations are ready", context)

    def _route_specialist_chain(self, context: TurnRuntimeContext) -> CollaborationRoute:
        sources = self._sources(context)
        if "shopping_brief_specialist" not in sources:
            return self._sub_agent_route(
                "specialist_chain.shopping_brief",
                "structured recommendation starts with need extraction",
                _shopping_brief_action(context),
            )
        if "candidate_analysis_specialist" not in sources:
            return self._sub_agent_route(
                "specialist_chain.candidate_analysis",
                "candidate analysis follows the shopping brief",
                _candidate_analysis_action(context),
            )
        if "comparison_specialist" not in sources:
            return self._sub_agent_route(
                "specialist_chain.comparison",
                "comparison follows candidate analysis",
                _comparison_action(context),
            )
        if "recommendation_rationale_specialist" not in sources:
            return self._sub_agent_route(
                "specialist_chain.recommendation_rationale",
                "rationale specialist adds traceable recommendation evidence",
                _rationale_action(context),
            )
        return self._reply_route("specialist_chain.reply", "all specialist observations are ready", context)

    @staticmethod
    def _sources(context: TurnRuntimeContext) -> set[str]:
        return {observation.source for observation in context.loop_state.observations}

    @staticmethod
    def _has_observations(context: TurnRuntimeContext) -> bool:
        return bool(context.loop_state.observations)

    @staticmethod
    def _is_v31_lite_request(normalized: str) -> bool:
        return _contains_any(normalized, _V31_KEYWORDS)

    @staticmethod
    def _is_specialist_request(normalized: str) -> bool:
        return _contains_any(normalized, _SPECIALIST_KEYWORDS)

    @staticmethod
    def _is_shopping_request(normalized: str) -> bool:
        return _contains_any(normalized, _SHOPPING_KEYWORDS)

    @staticmethod
    def _budget_missing(context: TurnRuntimeContext) -> bool:
        candidate_sections = (
            context.context_packet.active_constraints,
            context.context_packet.session_working_memory,
            context.context_packet.durable_user_memory,
            context.context_packet.confirmed_preferences,
        )
        budget_keys = {"budget", "budget_max", "budget_min", "max_budget", "price_max", "price_min"}
        for section in candidate_sections:
            if any(key in section for key in budget_keys):
                return False
        latest = context.context_packet.latest_user_message
        return not any(token in latest for token in ("元", "预算", "以内", "左右", "内"))

    @staticmethod
    def _scene_missing(context: TurnRuntimeContext) -> bool:
        candidate_sections = (
            context.context_packet.active_constraints,
            context.context_packet.session_working_memory,
            context.context_packet.durable_user_memory,
            context.context_packet.confirmed_preferences,
        )
        return not any("scene" in section for section in candidate_sections)

    @staticmethod
    def _needs_scene_clarification(normalized: str) -> bool:
        return _contains_any(normalized, _EXPLORATORY_KEYWORDS) and not _contains_any(
            normalized,
            _PURCHASE_INTENT_KEYWORDS,
        )

    @staticmethod
    def _fallback_route(route_key: str, reason: str) -> CollaborationRoute:
        return CollaborationRoute(
            route_key=route_key,
            required_action_kind="fallback",
            reason=reason,
            rewrite_action=FallbackAction(
                reason="business_scope_violation",
                user_message="目前我只能帮你做导购咨询，不能代下单、支付、处理账户或售后事项。",
            ),
        )

    @staticmethod
    def _clarification_route(route_key: str, reason: str) -> CollaborationRoute:
        return CollaborationRoute(
            route_key=route_key,
            required_action_kind="ask_clarification",
            reason=reason,
            rewrite_action=AskClarificationAction(
                question="为了继续推荐，我需要先确认预算、品类和使用场景。你希望控制在多少预算内，主要用在什么场景？",
                missing_slots=["budget", "category", "scene"],
            ),
        )

    @staticmethod
    def _tool_route(route_key: str, reason: str, action: CallToolAction) -> CollaborationRoute:
        return CollaborationRoute(
            route_key=route_key,
            required_action_kind="call_tool",
            reason=reason,
            rewrite_action=action,
        )

    @staticmethod
    def _sub_agent_route(route_key: str, reason: str, action: CallSubAgentAction) -> CollaborationRoute:
        return CollaborationRoute(
            route_key=route_key,
            required_action_kind="call_sub_agent",
            reason=reason,
            rewrite_action=action,
        )

    @staticmethod
    def _reply_route(route_key: str, reason: str, context: TurnRuntimeContext) -> CollaborationRoute:
        observation_ids = [observation.observation_id for observation in context.loop_state.observations]
        if not observation_ids:
            return CollaborationRouter._clarification_route(
                "reply_without_evidence_guard",
                "reply requires at least one observation",
            )
        return CollaborationRoute(
            route_key=route_key,
            required_action_kind="reply_to_user",
            reason=reason,
            rewrite_action=ReplyToUserAction(
                message="我已经根据当前可审计的工具或专家结果完成了这一轮分析，可以在 Trace 中查看完整调用链路。",
                observation_ids=[observation_ids[-1]],
            ),
        )


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle.lower() in text for needle in needles)


def _catalog_search_action(message: str) -> CallToolAction:
    return CallToolAction(
        capability_name="catalog_search",
        arguments={"query": message},
    )


def _v31_catalog_search_action() -> CallToolAction:
    return CallToolAction(
        capability_name="catalog_search",
        arguments={
            "query": "通勤 降噪耳机",
            "filters": {
                "category": "earphones",
                "scene": "commute",
                "price_max": 3000,
                "limit": 3,
            },
        },
    )


def _v31_inventory_check_action() -> CallToolAction:
    return CallToolAction(capability_name="inventory_check", arguments={"sku": "EAR-SON-WH1000XM5"})


def _v31_rag_action() -> CallToolAction:
    return CallToolAction(
        capability_name="rag_product_knowledge",
        arguments={"query": "Sony WH-1000XM5 通勤 降噪 卖点", "limit": 3},
    )


def _v31_preference_action() -> CallToolAction:
    return CallToolAction(
        capability_name="preference_profile_update",
        arguments={
            "preferences": {
                "scene": "commute",
                "category": "earphones",
                "budget": {"max": 3000, "currency": "CNY"},
            },
            "feedback_signal": "explicit_confirmed",
            "context": {"entry": "home_recommendation_card"},
        },
    )


def _v31_copy_action() -> CallToolAction:
    return CallToolAction(
        capability_name="marketing_copy_generate",
        arguments={
            "product": {
                "sku": "EAR-SON-WH1000XM5",
                "name": "Sony WH-1000XM5",
                "brand": "Sony",
                "price": 2899,
            },
            "preferences": {
                "scene": "commute",
                "category": "earphones",
                "budget": {"max": 3000, "currency": "CNY"},
            },
            "placement": "home_recommendation_card",
        },
    )


def _shopping_brief_action(context: TurnRuntimeContext) -> CallSubAgentAction:
    return CallSubAgentAction(
        capability_name="shopping_brief_specialist",
        brief={
            "brief_id": _brief_id(context, "shopping-brief"),
            "task_id": _task_id(context, "shopping-brief"),
            "role": "shopping_brief",
            "goal": "Extract a complete shopping brief from the user's request.",
            "constraints": {"raw_user_need": context.context_packet.latest_user_message},
            "allowed_capabilities": [],
        },
    )


def _candidate_analysis_action(context: TurnRuntimeContext) -> CallSubAgentAction:
    return CallSubAgentAction(
        capability_name="candidate_analysis_specialist",
        brief={
            "brief_id": _brief_id(context, "candidate-analysis"),
            "task_id": _task_id(context, "candidate-analysis"),
            "role": "candidate_analysis",
            "goal": "Search and analyze catalog-backed candidates for the structured shopping need.",
            "constraints": {
                "query": "3000 左右 通勤 降噪耳机",
                "category": "earphones",
                "scene": "commute",
                "budget_max": 3000,
                "exclude_brands": ["Beats"],
                "limit": 4,
            },
            "allowed_capabilities": ["catalog_search"],
        },
    )


def _comparison_action(context: TurnRuntimeContext) -> CallSubAgentAction:
    return CallSubAgentAction(
        capability_name="comparison_specialist",
        brief={
            "brief_id": _brief_id(context, "comparison"),
            "task_id": _task_id(context, "comparison"),
            "role": "comparison",
            "goal": "Compare the strongest candidates on commuter-relevant dimensions.",
            "constraints": {
                "skus": ["EAR-SON-WH1000XM5", "EAR-BOS-QCUH"],
                "dimensions": ["price", "battery", "noise_cancel", "weight"],
            },
            "allowed_capabilities": ["product_compare", "inventory_check"],
        },
    )


def _rationale_action(context: TurnRuntimeContext) -> CallSubAgentAction:
    return CallSubAgentAction(
        capability_name="recommendation_rationale_specialist",
        brief={
            "brief_id": _brief_id(context, "recommendation-rationale"),
            "task_id": _task_id(context, "recommendation-rationale"),
            "role": "recommendation_rationale",
            "goal": "Build evidence-cited rationales for the final pick.",
            "constraints": {
                "pick_sku": "EAR-SON-WH1000XM5",
                "query": "Sony WH-1000XM5 通勤 降噪",
            },
            "allowed_capabilities": ["rag_product_knowledge"],
        },
    )


def _brief_id(context: TurnRuntimeContext, name: str) -> str:
    return f"brief-{context.session.session_id}-{context.session.turn_count + 1}-{name}"


def _task_id(context: TurnRuntimeContext, name: str) -> str:
    return f"task-{context.session.session_id}-{context.session.turn_count + 1}-{name}"


__all__ = [
    "ActionKind",
    "CollaborationRoute",
    "CollaborationRouter",
]
