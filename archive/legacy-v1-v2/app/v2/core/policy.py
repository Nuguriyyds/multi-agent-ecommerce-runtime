from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_SCENE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "default": (),
    "homepage": (),
    "product_page": ("product_id",),
    "cart": ("product_ids",),
}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


class PolicyInput(BaseModel):
    scene: str = "default"
    scene_context: dict[str, Any] = Field(default_factory=dict)
    required_fields: tuple[str, ...] = ()
    available_fields: dict[str, Any] = Field(default_factory=dict)
    requested_tool: str | None = None
    allowed_tools: tuple[str, ...] = ()
    capability: str | None = None
    capability_supported: bool = True


class PolicyDecision(BaseModel):
    decision: Literal["allow", "reject", "clarify"]
    code: str
    reason: str
    missing_fields: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyGate:
    def __init__(
        self,
        *,
        scene_requirements: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        source = scene_requirements or DEFAULT_SCENE_REQUIREMENTS
        self._scene_requirements = {
            scene: tuple(requirements)
            for scene, requirements in source.items()
        }

    def list_scenes(self) -> tuple[str, ...]:
        return tuple(sorted(self._scene_requirements))

    def evaluate(self, policy_input: PolicyInput) -> PolicyDecision:
        scene = policy_input.scene or "default"
        if scene not in self._scene_requirements:
            return PolicyDecision(
                decision="reject",
                code="unsupported_scene",
                reason=f"scene '{scene}' is not supported",
                metadata={"supported_scenes": self.list_scenes()},
            )

        if (
            policy_input.requested_tool
            and policy_input.allowed_tools
            and policy_input.requested_tool not in policy_input.allowed_tools
        ):
            return PolicyDecision(
                decision="reject",
                code="illegal_tool",
                reason=f"tool '{policy_input.requested_tool}' is not allowed in this context",
                metadata={"allowed_tools": policy_input.allowed_tools},
            )

        if not policy_input.capability_supported:
            capability = policy_input.capability or "requested action"
            return PolicyDecision(
                decision="reject",
                code="unsupported_capability",
                reason=f"{capability} is not supported",
            )

        missing_scene_fields = tuple(
            field
            for field in self._scene_requirements[scene]
            if not _has_value(policy_input.scene_context.get(field))
        )
        if missing_scene_fields:
            return PolicyDecision(
                decision="clarify",
                code="missing_scene_context",
                reason="scene context is incomplete",
                missing_fields=missing_scene_fields,
                metadata={"scene": scene},
            )

        missing_required_fields = tuple(
            field
            for field in policy_input.required_fields
            if not _has_value(policy_input.available_fields.get(field))
        )
        if missing_required_fields:
            return PolicyDecision(
                decision="clarify",
                code="missing_required_fields",
                reason="required inputs are incomplete",
                missing_fields=missing_required_fields,
            )

        return PolicyDecision(
            decision="allow",
            code="allowed",
            reason="request satisfies the current V2 policy checks",
        )


__all__ = [
    "DEFAULT_SCENE_REQUIREMENTS",
    "PolicyDecision",
    "PolicyGate",
    "PolicyInput",
]
