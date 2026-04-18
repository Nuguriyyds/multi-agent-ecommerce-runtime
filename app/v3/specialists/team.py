from __future__ import annotations

import logging
from collections.abc import Iterable

from app.v3.models import AgentRole, AgentTeam as AgentTeamState, DelegationPolicy, SpecialistBrief, SpecialistObservation

from .base import Specialist


class SpecialistAlreadyRegistered(ValueError):
    """Raised when a role is registered more than once in the fixed team."""


class SpecialistNotFound(LookupError):
    """Raised when a requested specialist role is missing from the fixed team."""


class AgentTeam:
    def __init__(
        self,
        *,
        team_id: str = "fixed-specialist-team",
        delegation_policy: DelegationPolicy | None = None,
        specialists: Iterable[Specialist] = (),
    ) -> None:
        self._team_id = team_id
        self._delegation_policy = delegation_policy
        self._specialists: dict[AgentRole, Specialist] = {}
        self._logger = logging.getLogger(__name__)
        for specialist in specialists:
            self.register(specialist)

    @property
    def team_id(self) -> str:
        return self._team_id

    def register(self, specialist: Specialist) -> Specialist:
        if specialist.role in self._specialists:
            raise SpecialistAlreadyRegistered(
                f"specialist role {specialist.role.value} is already registered"
            )

        self._specialists[specialist.role] = specialist
        self._logger.info(
            "AgentTeam registered specialist team_id=%s role=%s capability=%s",
            self._team_id,
            specialist.role.value,
            specialist.name,
        )
        return specialist

    def get(self, role: AgentRole) -> Specialist:
        try:
            return self._specialists[role]
        except KeyError as exc:
            raise SpecialistNotFound(f"specialist role {role.value} is not registered") from exc

    def list_roles(self) -> list[AgentRole]:
        return list(self._specialists)

    async def dispatch(self, brief: SpecialistBrief) -> SpecialistObservation:
        specialist = self.get(brief.role)
        return await specialist.invoke(brief)

    def snapshot(self) -> AgentTeamState:
        return AgentTeamState(
            team_id=self._team_id,
            roles=self.list_roles(),
            delegation_policy=self._delegation_policy.model_copy(deep=True)
            if self._delegation_policy is not None
            else None,
            capability_map={
                role.value: specialist.allowed_capabilities
                for role, specialist in self._specialists.items()
            },
        )


__all__ = [
    "AgentTeam",
    "SpecialistAlreadyRegistered",
    "SpecialistNotFound",
]
