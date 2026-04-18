from __future__ import annotations

import pytest

from app.v1.agents.user_profile import UserProfileAgent
from app.v1.services.feature_store import FeatureStore
from app.v1.services.llm_service import LLMService


class RecordingLLMService(LLMService):
    def __init__(self, response: str | None = None) -> None:
        self.calls = 0
        self.response = response

    async def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        if self.response is not None:
            return self.response
        return self._mock_response(user_prompt)


@pytest.mark.asyncio
async def test_user_profile_agent_returns_structured_profile_for_history_user():
    llm_service = RecordingLLMService()
    agent = UserProfileAgent(
        feature_store=FeatureStore(),
        llm_service=llm_service,
    )

    result = await agent.run({"user_id": "u_high_value"})

    assert result.success is True
    assert result.degraded is False
    assert result.data["cold_start"] is False
    assert result.data["segment"] == "high_value"
    assert result.data["price_range"][0] < result.data["price_range"][1]
    assert "高客单价" in result.data["tags"]

    profile = result.data["profile"]
    assert profile["user_id"] == "u_high_value"
    assert profile["cold_start"] is False
    assert "high_value" in profile["segments"]
    assert "手机" in profile["preferred_categories"]
    assert profile["rfm_score"]["monetary"] > 0.8
    assert llm_service.calls == 1


@pytest.mark.asyncio
async def test_user_profile_agent_returns_cold_start_for_unknown_user():
    llm_service = RecordingLLMService()
    agent = UserProfileAgent(
        feature_store=FeatureStore(),
        llm_service=llm_service,
    )

    result = await agent.run({"user_id": "u_unknown"})

    assert result.success is True
    assert result.degraded is False
    assert result.data["cold_start"] is True
    assert result.data["segment"] == "new_user"
    assert result.data["tags"] == ["手机", "耳机", "配件"]

    profile = result.data["profile"]
    assert profile["cold_start"] is True
    assert profile["segments"] == ["new_user"]
    assert profile["preferred_categories"] == ["手机", "耳机", "配件"]
    assert llm_service.calls == 0


@pytest.mark.asyncio
async def test_user_profile_agent_degrades_to_cold_start_when_llm_output_is_invalid():
    llm_service = RecordingLLMService(response="not-json")
    agent = UserProfileAgent(
        feature_store=FeatureStore(),
        llm_service=llm_service,
    )

    result = await agent.run({"user_id": "u_price_sensitive"})

    assert result.success is False
    assert result.degraded is True
    assert result.data["cold_start"] is True
    assert result.data["segment"] == "new_user"
    assert llm_service.calls == 3
