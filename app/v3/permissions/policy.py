from __future__ import annotations

from app.v3.models.capability import CapabilityDescriptor
from app.v3.models.permissions import PermissionDecision, PermissionPolicy


def check_permission(
    policy: PermissionPolicy,
    actor: str,
    capability: CapabilityDescriptor | str,
) -> PermissionDecision:
    return policy.check(actor, capability)


__all__ = ["PermissionDecision", "PermissionPolicy", "check_permission"]
