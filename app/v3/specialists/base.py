from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from app.v3.models import (
    AgentRole,
    CapabilityDescriptor,
    CapabilityKind,
    Observation,
    PermissionPolicy,
    SpecialistBrief,
    SpecialistObservation,
)
from app.v3.registry import CapabilityRegistry, SubAgentProvider, ToolProvider

_SPECIALIST_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "brief_id": {"type": "string"},
        "task_id": {"type": "string"},
        "role": {"type": "string"},
        "goal": {"type": "string"},
        "constraints": {"type": "object"},
        "allowed_capabilities": {"type": "array"},
        "context_packet": {"type": "object"},
    },
    "required": ["brief_id", "task_id", "role", "goal", "allowed_capabilities"],
}

_SPECIALIST_OBSERVATION_SCHEMA = {
    "type": "object",
    "properties": {
        "observation_id": {"type": "string"},
        "source": {"type": "string"},
        "status": {"type": "string"},
        "summary": {"type": "string"},
        "payload": {"type": "object"},
        "evidence_source": {"type": "string"},
        "role": {"type": "string"},
        "brief_id": {"type": "string"},
    },
    "required": ["observation_id", "source", "status", "summary", "payload", "role"],
}


class SpecialistRoleMismatch(ValueError):
    """Raised when a brief targets a different role than the specialist."""


class SpecialistPermissionError(PermissionError):
    """Raised when a specialist attempts to use a capability outside its scope."""


class SpecialistCapabilityTypeError(TypeError):
    """Raised when a specialist tries to invoke a non-tool capability as a tool."""


class Specialist(SubAgentProvider, ABC):
    def __init__(
        self,
        *,
        role: AgentRole,
        registry: CapabilityRegistry | None = None,
        name: str | None = None,
        description: str | None = None,
        allowed_capabilities: Sequence[str] = (),
    ) -> None:
        self._role = role
        self._registry = registry or CapabilityRegistry()
        self._allowed_capabilities = tuple(allowed_capabilities)
        descriptor = CapabilityDescriptor(
            name=name or f"{role.value}_specialist",
            kind=CapabilityKind.sub_agent,
            input_schema=_SPECIALIST_BRIEF_SCHEMA,
            output_schema=_SPECIALIST_OBSERVATION_SCHEMA,
            permission_tag=f"specialist.{role.value}.invoke",
            description=description,
        )
        super().__init__(descriptor)
        self._logger = logging.getLogger(f"{__name__}.{self.name}")

    @property
    def role(self) -> AgentRole:
        return self._role

    @property
    def registry(self) -> CapabilityRegistry:
        return self._registry

    @property
    def allowed_capabilities(self) -> list[str]:
        return list(self._allowed_capabilities)

    async def invoke(self, brief: SpecialistBrief) -> SpecialistObservation:
        self._ensure_matching_role(brief)
        self._logger.info(
            "Specialist start name=%s role=%s task_id=%s",
            self.name,
            self.role.value,
            brief.task_id,
        )
        try:
            observation = await self.execute(brief)
        except Exception:
            self._logger.exception(
                "Specialist failed name=%s role=%s task_id=%s",
                self.name,
                self.role.value,
                brief.task_id,
            )
            raise

        normalized = observation.model_copy(
            deep=True,
            update={
                "role": brief.role,
                "brief_id": brief.brief_id,
                "source": self.name,
                "evidence_source": observation.evidence_source or f"specialist:{self.name}",
            },
        )
        self._logger.info(
            "Specialist success name=%s role=%s task_id=%s observation_id=%s",
            self.name,
            self.role.value,
            brief.task_id,
            normalized.observation_id,
        )
        return normalized

    @abstractmethod
    async def execute(self, brief: SpecialistBrief) -> SpecialistObservation:
        """Run the specialist and return a structured observation to the main agent."""

    async def invoke_tool(
        self,
        brief: SpecialistBrief,
        *,
        capability_name: str,
        arguments: dict[str, Any],
    ) -> Observation:
        provider = self._registry.get(capability_name)
        if not isinstance(provider, ToolProvider):
            raise SpecialistCapabilityTypeError(
                f"Capability {capability_name!r} is not a tool provider."
            )

        self._check_permission(brief, provider.descriptor)
        self._logger.info(
            "Specialist tool start specialist=%s role=%s capability=%s task_id=%s",
            self.name,
            self.role.value,
            capability_name,
            brief.task_id,
        )
        try:
            observation = await provider.invoke(arguments)
        except Exception:
            self._logger.exception(
                "Specialist tool failed specialist=%s role=%s capability=%s task_id=%s",
                self.name,
                self.role.value,
                capability_name,
                brief.task_id,
            )
            raise

        self._logger.info(
            "Specialist tool success specialist=%s role=%s capability=%s observation_id=%s",
            self.name,
            self.role.value,
            capability_name,
            observation.observation_id,
        )
        return observation.model_copy(deep=True)

    def build_observation(
        self,
        brief: SpecialistBrief,
        *,
        summary: str,
        payload: dict[str, Any] | None = None,
        status: str = "ok",
        observation_id: str | None = None,
        evidence_source: str | None = None,
    ) -> SpecialistObservation:
        return SpecialistObservation(
            observation_id=observation_id or f"obs-{uuid4().hex[:12]}",
            source=self.name,
            status=status,
            summary=summary,
            payload=dict(payload or {}),
            evidence_source=evidence_source or f"specialist:{self.name}",
            role=brief.role,
            brief_id=brief.brief_id,
        )

    def _ensure_matching_role(self, brief: SpecialistBrief) -> None:
        if brief.role is self.role:
            return
        raise SpecialistRoleMismatch(
            f"Specialist {self.name} handles role {self.role.value}, got brief for {brief.role.value}"
        )

    def _check_permission(
        self,
        brief: SpecialistBrief,
        descriptor: CapabilityDescriptor,
    ) -> None:
        if descriptor.name not in brief.allowed_capabilities:
            raise SpecialistPermissionError(
                f"capability {descriptor.name} is outside allowed_capabilities"
            )

        policy = PermissionPolicy(
            actor=self.name,
            allowed_capabilities=list(brief.allowed_capabilities),
        )
        decision = policy.check(self.name, descriptor)
        if decision.decision == "allow":
            return
        raise SpecialistPermissionError(
            decision.reason or f"capability {descriptor.name} is outside allowed_capabilities"
        )


__all__ = [
    "Specialist",
    "SpecialistCapabilityTypeError",
    "SpecialistPermissionError",
    "SpecialistRoleMismatch",
]
