from app.main import app


def test_v3_workspace_placeholder_app_exists() -> None:
    assert app.title == "Multi-Agent Ecommerce System V3 Workspace"


def test_v3_workspace_health_route_registered() -> None:
    paths = {route.path for route in app.router.routes}
    assert "/health" in paths
