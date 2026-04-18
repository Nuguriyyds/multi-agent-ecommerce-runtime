from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import V3Model
from .capability import CapabilityDescriptor


class PermissionPolicy(V3Model):
    actor: str
    allowed_capabilities: list[str] = Field(default_factory=list)
    denied_capabilities: list[str] = Field(default_factory=list)
    notes: str | None = None

    def check(self, actor: str, capability: CapabilityDescriptor | str) -> "PermissionDecision":
        capability_name = capability.name if isinstance(capability, CapabilityDescriptor) else capability

        if self.actor not in {"*", actor}:
            return PermissionDecision(
                decision="deny",
                actor=actor,
                capability_name=capability_name,
                reason=f"policy actor mismatch: expected {self.actor}, got {actor}",
            )

        if capability_name in self.denied_capabilities:
            return PermissionDecision(
                decision="deny",
                actor=actor,
                capability_name=capability_name,
                reason=f"capability {capability_name} is explicitly denied",
            )

        if self.allowed_capabilities and capability_name not in self.allowed_capabilities:
            return PermissionDecision(
                decision="deny",
                actor=actor,
                capability_name=capability_name,
                reason=f"capability {capability_name} is outside allowed_capabilities",
            )

        return PermissionDecision(
            decision="allow",
            actor=actor,
            capability_name=capability_name,
        )


class PermissionDecision(V3Model):
    decision: Literal["allow", "deny"]
    actor: str
    capability_name: str
    reason: str | None = None
