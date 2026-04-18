from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.v3.api import install_v3_api
from app.v3.config import Settings, get_settings

_WEB_DIR = Path(__file__).parent / "v3" / "api" / "web"


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

    if _WEB_DIR.is_dir():
        application.mount(
            "/ui",
            StaticFiles(directory=str(_WEB_DIR), html=True),
            name="v3-ui",
        )

    return application


app = create_app()
