from __future__ import annotations

from typing import Any

import pytest

from app.v3.models import AgentRole, CapabilityDescriptor, CapabilityKind, Observation, SpecialistBrief
from app.v3.registry import CapabilityRegistry, ToolProvider
from app.v3.specialists import AgentTeam, Specialist, SpecialistPermissionError


def make_brief(
    *,
    role: AgentRole = AgentRole.candidate_analysis,
    allowed_capabilities: list[str] | None = None,
) -> SpecialistBrief:
    return SpecialistBrief(
        brief_id="brief-1",
        task_id="task-1",
        role=role,
        goal="Analyze the current shortlist.",
        constraints={"budget_max": 3000},
        allowed_capabilities=allowed_capabilities or [],
    )


class MockToolProvider(ToolProvider):
    def __init__(self, name: str) -> None:
        super().__init__(
            CapabilityDescriptor(
                name=name,
                kind=CapabilityKind.tool,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                permission_tag=f"{name}.read",
            )
        )

    async def invoke(self, args: dict[str, Any]) -> Observation:
        return Observation(
            observation_id=f"obs-{self.name}",
            source=self.name,
            summary=f"{self.name} completed.",
            payload=dict(args),
            evidence_source=f"tool:{self.name}",
        )


class EchoSpecialist(Specialist):
    def __init__(
        self,
        *,
        role: AgentRole,
        registry: CapabilityRegistry | None = None,
        allowed_capabilities: list[str] | None = None,
    ) -> None:
        super().__init__(
            role=role,
            name=f"{role.value}_specialist",
            registry=registry,
            allowed_capabilities=allowed_capabilities or [],
        )

    async def execute(self, brief: SpecialistBrief):
        return self.build_observation(
            brief,
            summary=f"{self.role.value} completed the brief.",
            payload={"goal": brief.goal},
        )


class ToolUsingSpecialist(Specialist):
    def __init__(self, *, registry: CapabilityRegistry) -> None:
        super().__init__(
            role=AgentRole.comparison,
            name="comparison_specialist",
            registry=registry,
            allowed_capabilities=["product_compare"],
        )

    async def execute(self, brief: SpecialistBrief):
        observation = await self.invoke_tool(
            brief,
            capability_name="inventory_check",
            arguments={"sku": "sku-1"},
        )
        return self.build_observation(
            brief,
            summary="Unexpectedly used an unapproved tool.",
            payload={"tool_observation": observation.model_dump(mode="json")},
        )


@pytest.mark.asyncio
async def test_mock_specialist_receives_brief_and_returns_observation() -> None:
    specialist = EchoSpecialist(role=AgentRole.candidate_analysis)
    team = AgentTeam(specialists=[specialist])

    observation = await team.dispatch(
        make_brief(role=AgentRole.candidate_analysis, allowed_capabilities=["catalog_search"])
    )

    assert observation.role is AgentRole.candidate_analysis
    assert observation.brief_id == "brief-1"
    assert observation.source == "candidate_analysis_specialist"
    assert observation.payload == {"goal": "Analyze the current shortlist."}


@pytest.mark.asyncio
async def test_specialist_tool_invocation_is_denied_outside_allowed_capabilities() -> None:
    registry = CapabilityRegistry()
    registry.register(MockToolProvider("inventory_check"))
    specialist = ToolUsingSpecialist(registry=registry)

    with pytest.raises(SpecialistPermissionError, match="outside allowed_capabilities"):
        await specialist.invoke(
            make_brief(role=AgentRole.comparison, allowed_capabilities=["product_compare"])
        )


def test_agent_team_registers_specialists_by_role() -> None:
    shopping_specialist = EchoSpecialist(
        role=AgentRole.shopping_brief,
        allowed_capabilities=["catalog_search"],
    )
    comparison_specialist = EchoSpecialist(
        role=AgentRole.comparison,
        allowed_capabilities=["product_compare", "inventory_check"],
    )
    team = AgentTeam(team_id="shopping-team")
    team.register(shopping_specialist)
    team.register(comparison_specialist)

    assert team.get(AgentRole.shopping_brief) is shopping_specialist
    assert team.get(AgentRole.comparison) is comparison_specialist
    assert team.list_roles() == [AgentRole.shopping_brief, AgentRole.comparison]
    assert team.snapshot().capability_map == {
        "shopping_brief": ["catalog_search"],
        "comparison": ["product_compare", "inventory_check"],
    }
