from __future__ import annotations

from fastapi import FastAPI

from app.v3.agents import MainAgent
from app.v3.config import Settings
from app.v3.observability import install_observability
from app.v3.prompts import PromptRegistry
from app.v3.registry import CapabilityRegistry
from app.v3.specialists import (
    AgentTeam,
    CandidateAnalysisSpecialist,
    ComparisonSpecialist,
    RecommendationRationaleSpecialist,
    ShoppingBriefSpecialist,
)
from app.v3.tools import register_mock_mcp_tool_providers, register_mock_tool_providers

from .messages import router as messages_router
from .middleware import install_trace_middleware
from .sessions import SessionStore, router as sessions_router
from .trace import router as trace_router


def install_v3_api(application: FastAPI, settings: Settings) -> None:
    hook_bus = install_observability(application, emit_to_stderr=settings.app_debug)
    registry = CapabilityRegistry()
    prompt_registry = PromptRegistry()
    session_store = SessionStore()
    team = AgentTeam()

    register_mock_tool_providers(registry)
    if settings.mcp_mock_enabled:
        register_mock_mcp_tool_providers(registry, settings=settings)

    specialists = (
        ShoppingBriefSpecialist(prompt_registry=prompt_registry),
        CandidateAnalysisSpecialist(registry=registry, prompt_registry=prompt_registry),
        ComparisonSpecialist(registry=registry, prompt_registry=prompt_registry),
        RecommendationRationaleSpecialist(registry=registry, prompt_registry=prompt_registry),
    )
    for specialist in specialists:
        registry.register(specialist)
        team.register(specialist)

    main_agent = MainAgent(
        registry=registry,
        prompt_registry=prompt_registry,
        hook_bus=hook_bus,
        settings=settings,
    )

    application.state.v3_registry = registry
    application.state.v3_prompt_registry = prompt_registry
    application.state.v3_session_store = session_store
    application.state.v3_team = team
    application.state.v3_hook_bus = hook_bus
    application.state.v3_main_agent = main_agent

    install_trace_middleware(application)
    application.include_router(sessions_router)
    application.include_router(messages_router)
    application.include_router(trace_router)

    async def close_v3_resources() -> None:
        await application.state.v3_main_agent.llm_client.aclose()

    application.router.add_event_handler("shutdown", close_v3_resources)


__all__ = ["install_v3_api", "SessionStore"]
