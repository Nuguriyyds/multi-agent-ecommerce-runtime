from __future__ import annotations

from typing import Iterable

from app.v2.core.models import TurnPlan, TurnPlanStep, TurnPlanStepName
from app.v2.core.policy import PolicyDecision

ALLOWED_TURN_PLAN_STEPS: tuple[TurnPlanStepName, ...] = (
    "clarify",
    "preference_worker",
    "catalog_worker",
    "inventory_worker",
    "comparison_worker",
    "copy_worker",
    "profile.request_projection",
)
MAX_TURN_PLAN_STEPS = 8
_COMPARE_KEYWORDS: tuple[str, ...] = (
    "compare",
    "comparison",
    "vs",
    "区别",
    "比较",
    "对比",
    "值不值得买",
)
_RECOMMENDATION_KEYWORDS: tuple[str, ...] = (
    "recommend",
    "suggest",
    "which one",
    "what should i buy",
    "推荐",
    "适合",
    "买什么",
)


class ShoppingTurnPlanner:
    def build_plan(
        self,
        *,
        scene: str,
        message: str,
        decision: PolicyDecision,
    ) -> TurnPlan:
        if decision.decision == "clarify":
            return TurnPlan(
                intent="clarify",
                terminal_state="needs_clarification",
                steps=[TurnPlanStep(name="clarify", step=1)],
            )

        if decision.decision == "reject":
            return TurnPlan(
                intent="fallback",
                terminal_state="fallback_used",
                steps=[],
                fallback_reason=decision.code,
            )

        normalized_message = message.casefold()
        if self._needs_comparison(scene=scene, message=normalized_message):
            plan = TurnPlan(
                intent="comparison",
                terminal_state="reply_ready",
                steps=[
                    TurnPlanStep(name="preference_worker", step=1),
                    TurnPlanStep(name="catalog_worker", step=2),
                    TurnPlanStep(name="inventory_worker", step=3),
                    TurnPlanStep(name="comparison_worker", step=4),
                    TurnPlanStep(
                        name="profile.request_projection",
                        step=5,
                        conditional=True,
                        skip_reason="no_projection_needed",
                    ),
                ],
            )
            return self.validate(plan)

        if self._needs_recommendation(message=normalized_message):
            plan = TurnPlan(
                intent="recommendation",
                terminal_state="reply_ready",
                steps=[
                    TurnPlanStep(name="preference_worker", step=1),
                    TurnPlanStep(name="catalog_worker", step=2),
                    TurnPlanStep(name="inventory_worker", step=3),
                    TurnPlanStep(name="copy_worker", step=4),
                    TurnPlanStep(
                        name="profile.request_projection",
                        step=5,
                        conditional=True,
                        skip_reason="no_projection_needed",
                    ),
                ],
            )
            return self.validate(plan)

        plan = TurnPlan(
            intent="advisory",
            terminal_state="reply_ready",
            steps=[
                TurnPlanStep(name="preference_worker", step=1),
                TurnPlanStep(
                    name="profile.request_projection",
                    step=2,
                    conditional=True,
                    skip_reason="no_projection_needed",
                ),
            ],
        )
        return self.validate(plan)

    def validate(self, plan: TurnPlan) -> TurnPlan:
        if len(plan.steps) > MAX_TURN_PLAN_STEPS:
            return TurnPlan(
                intent="fallback",
                terminal_state="fallback_used",
                steps=[],
                fallback_reason="step_cap_exceeded",
            )
        for step in plan.steps:
            if step.name not in ALLOWED_TURN_PLAN_STEPS:
                return TurnPlan(
                    intent="fallback",
                    terminal_state="fallback_used",
                    steps=[],
                    fallback_reason=f"illegal_step:{step.name}",
                )
        return plan

    @staticmethod
    def _needs_comparison(*, scene: str, message: str) -> bool:
        if scene in {"product_page", "cart"}:
            return True
        return any(keyword in message for keyword in _COMPARE_KEYWORDS)

    @staticmethod
    def _needs_recommendation(*, message: str) -> bool:
        return any(keyword in message for keyword in _RECOMMENDATION_KEYWORDS)


def normalize_executed_steps(steps: Iterable[TurnPlanStepName]) -> list[str]:
    return [str(step) for step in steps]


__all__ = [
    "ALLOWED_TURN_PLAN_STEPS",
    "MAX_TURN_PLAN_STEPS",
    "ShoppingTurnPlanner",
    "normalize_executed_steps",
]
