from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app, create_app
from app.v3.config import Settings, get_settings


ENV_KEYS = (
    "ECOV3_OPENAI_API_KEY",
    "ECOV3_OPENAI_BASE_URL",
    "ECOV3_OPENAI_MODEL",
    "ECOV3_APP_HOST",
    "ECOV3_APP_PORT",
    "ECOV3_APP_DEBUG",
    "ECOV3_SESSION_MAX_TURNS",
    "ECOV3_SESSION_IDLE_MINUTES",
    "ECOV3_MAX_STEPS",
    "ECOV3_MCP_MOCK_ENABLED",
)


def _clear_v3_env(monkeypatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_settings_defaults(monkeypatch) -> None:
    _clear_v3_env(monkeypatch)
    get_settings.cache_clear()
    settings = Settings()

    assert settings.openai_api_key == ""
    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.openai_model == "gpt-4.1-mini"
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8000
    assert settings.app_debug is False
    assert settings.session_max_turns == 20
    assert settings.session_idle_minutes == 30
    assert settings.max_steps == 8
    assert settings.mcp_mock_enabled is True


def test_settings_read_ecov3_prefix_and_cache(monkeypatch) -> None:
    _clear_v3_env(monkeypatch)
    get_settings.cache_clear()
    monkeypatch.setenv("ECOV3_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ECOV3_OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("ECOV3_OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("ECOV3_APP_HOST", "0.0.0.0")
    monkeypatch.setenv("ECOV3_APP_PORT", "9001")
    monkeypatch.setenv("ECOV3_APP_DEBUG", "true")
    monkeypatch.setenv("ECOV3_SESSION_MAX_TURNS", "25")
    monkeypatch.setenv("ECOV3_SESSION_IDLE_MINUTES", "45")
    monkeypatch.setenv("ECOV3_MAX_STEPS", "9")
    monkeypatch.setenv("ECOV3_MCP_MOCK_ENABLED", "false")

    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.openai_api_key == "test-key"
    assert first.openai_base_url == "https://example.com/v1"
    assert first.openai_model == "gpt-test"
    assert first.app_host == "0.0.0.0"
    assert first.app_port == 9001
    assert first.app_debug is True
    assert first.session_max_turns == 25
    assert first.session_idle_minutes == 45
    assert first.max_steps == 9
    assert first.mcp_mock_enabled is False
    get_settings.cache_clear()


def test_create_app_exposes_health_and_settings() -> None:
    settings = Settings(app_debug=True)
    test_app = create_app(settings)
    client = TestClient(test_app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "workspace": "v3"}
    assert test_app.state.settings is settings
    assert test_app.debug is True


def test_module_app_health_contract() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "workspace": "v3"}
