from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ECOV3_",
        extra="ignore",
    )

    openai_api_key: str = Field(default="")
    openai_base_url: str = Field(default="https://api.openai.com/v1")
    openai_model: str = Field(default="gpt-4.1-mini")
    openai_timeout_seconds: float = Field(default=30.0, gt=0)
    app_host: str = Field(default="127.0.0.1")
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_debug: bool = Field(default=False)
    session_max_turns: int = Field(default=20, ge=1)
    session_idle_minutes: int = Field(default=30, ge=1)
    max_steps: int = Field(default=8, ge=1)
    mcp_mock_enabled: bool = Field(default=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
