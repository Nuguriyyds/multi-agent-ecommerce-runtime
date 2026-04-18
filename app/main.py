from __future__ import annotations

from fastapi import FastAPI

from app.v3.api import install_v3_api
from app.v3.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    application = FastAPI(
        title="Multi-Agent Ecommerce System V3 Workspace",
        version="0.0.0",
        debug=resolved_settings.app_debug,
    )
    application.state.settings = resolved_settings
    install_v3_api(application, resolved_settings)

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "workspace": "v3",
        }

    return application


app = create_app()
