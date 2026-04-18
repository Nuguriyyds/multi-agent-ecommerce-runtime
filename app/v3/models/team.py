from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field

from .base import V3Model
from .decision import Observation
from .session import ContextPacket


class AgentRole(str, Enum):
    shopping_brief = "shopping_brief"
    candidate_analysis = "candidate_analysis"
    comparison = "comparison"
    recommendation_rationale = "recommendation_rationale"


class DelegationPolicy(V3Model):
    preferred_roles: list[AgentRole] = Field(default_factory=list)
    fallback_to_tool: bool = True
    rationale: str | None = None


class SpecialistBrief(V3Model):
    brief_id: str
    task_id: str
    role: AgentRole
    goal: str
    constraints: dict[str, Any] = Field(default_factory=dict)
    allowed_capabilities: list[str] = Field(default_factory=list)
    context_packet: ContextPacket | None = None


class SpecialistObservation(Observation):
    role: AgentRole
    brief_id: str | None = None
    source: str = "specialist"


class TeamTask(V3Model):
    team_task_id: str
    role: AgentRole
    description: str
    allowed_capabilities: list[str] = Field(default_factory=list)
    brief: SpecialistBrief | None = None


class TeamTaskResult(V3Model):
    team_task_id: str
    status: Literal["done", "failed", "blocked"]
    observation_summary: str
    observation: SpecialistObservation | None = None


class AgentTeam(V3Model):
    team_id: str
    roles: list[AgentRole] = Field(default_factory=list)
    delegation_policy: DelegationPolicy | None = None
    capability_map: dict[str, list[str]] = Field(default_factory=dict)
