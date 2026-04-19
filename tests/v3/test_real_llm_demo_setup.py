from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.v3.config import Settings


def test_health_reports_mock_mode_when_api_key_is_empty() -> None:
    app = create_app(Settings(openai_api_key=""))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "workspace": "v3", "llm_mode": "mock"}


def test_health_reports_remote_mode_when_api_key_is_present() -> None:
    app = create_app(Settings(openai_api_key="demo-key"))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "workspace": "v3", "llm_mode": "remote"}


def test_real_llm_demo_template_exists() -> None:
    template = Path("demo/real_llm/.env.real.example")

    assert template.exists()
    content = template.read_text(encoding="utf-8")
    assert "ECOV3_OPENAI_API_KEY=" in content
    assert "ECOV3_OPENAI_MODEL=" in content
