from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.shared.data.product_catalog import ProductCatalog
from app.v2.api.schemas import RecommendationReadRequest
from app.v2.api.session_service import V2SessionService
from app.v2.core.models import RecommendationSnapshot
from main import app, get_v2_session_service


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_recommendation_api"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir()
    return path


def _catalog_by_id():
    return {product.product_id: product for product in ProductCatalog().get_fallback_products(limit=100)}


@pytest.fixture
def v2_service() -> V2SessionService:
    tempdir = _workspace_tempdir()
    service = V2SessionService(tempdir / "v2.sqlite3")
    get_v2_session_service.cache_clear()
    app.dependency_overrides[get_v2_session_service] = lambda: service
    yield service
    app.dependency_overrides.clear()
    get_v2_session_service.cache_clear()


def test_v2_recommendation_api_homepage_returns_existing_snapshot(v2_service: V2SessionService):
    catalog = _catalog_by_id()
    generated_at = datetime(2026, 4, 17, 10, 5, tzinfo=timezone.utc)
    v2_service.snapshots.save(
        RecommendationSnapshot(
            snapshot_id="snap_existing_homepage",
            user_id="u_home_scene",
            scene="homepage",
            products=[catalog["sku-gan-65w"].model_copy(deep=True), catalog["sku-watch-fit-4"].model_copy(deep=True)],
            copies=[],
            generated_at=generated_at,
            expires_at=generated_at + timedelta(hours=24),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/v2/users/u_home_scene/recommendations", params={"scene": "homepage"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["scene"] == "homepage"
    assert payload["products"][0]["product_id"] == "sku-gan-65w"
    assert payload["generated_at"] == generated_at.isoformat().replace("+00:00", "Z")
    assert payload["stale"] is False
    assert payload["pending_refresh"] is False


def test_v2_recommendation_api_default_miss_falls_back_to_homepage(v2_service: V2SessionService):
    catalog = _catalog_by_id()
    generated_at = datetime(2026, 4, 17, 9, 30, tzinfo=timezone.utc)
    v2_service.snapshots.save(
        RecommendationSnapshot(
            snapshot_id="snap_homepage_fallback",
            user_id="u_scene_fallback",
            scene="homepage",
            products=[catalog["sku-redmi-k80"].model_copy(deep=True)],
            copies=[],
            generated_at=generated_at,
            expires_at=generated_at + timedelta(hours=24),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/v2/users/u_scene_fallback/recommendations", params={"scene": "default"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["scene"] == "homepage"
    assert payload["products"][0]["product_id"] == "sku-redmi-k80"
    assert payload["pending_refresh"] is False


def test_v2_recommendation_api_contextual_miss_falls_back_to_homepage(v2_service: V2SessionService):
    catalog = _catalog_by_id()
    generated_at = datetime(2026, 4, 17, 9, 45, tzinfo=timezone.utc)
    v2_service.snapshots.save(
        RecommendationSnapshot(
            snapshot_id="snap_homepage_contextual_fallback",
            user_id="u_cart_fallback",
            scene="homepage",
            products=[catalog["sku-watch-fit-4"].model_copy(deep=True)],
            copies=[],
            generated_at=generated_at,
            expires_at=generated_at + timedelta(hours=24),
        ),
    )

    with TestClient(app) as client:
        product_page = client.get(
            "/api/v2/users/u_cart_fallback/recommendations",
            params={"scene": "product_page", "product_id": "sku-iphone-16-pro"},
        )
        cart = client.get(
            "/api/v2/users/u_cart_fallback/recommendations",
            params={"scene": "cart", "product_ids": "sku-ipad-air-6,sku-iphone-16-pro"},
        )

    assert product_page.status_code == 200
    assert cart.status_code == 200
    assert product_page.json()["scene"] == "homepage"
    assert cart.json()["scene"] == "homepage"


@pytest.mark.asyncio
async def test_v2_recommendation_api_miss_returns_empty_and_enqueues_refresh(v2_service: V2SessionService):
    response = await v2_service.read_recommendations("u_miss", RecommendationReadRequest(scene="homepage"))

    assert response.scene == "homepage"
    assert response.products == []
    assert response.copies == []
    assert response.generated_at is None
    assert response.stale is True
    assert response.pending_refresh is True

    pending = v2_service.events.list_by_status("pending")
    assert len(pending) == 1
    assert pending[0].event_type == "recommendation_refresh"
    assert pending[0].payload["trigger"] == "snapshot_miss"


def test_v2_recommendation_api_returns_expired_snapshot_and_enqueues_refresh(v2_service: V2SessionService):
    catalog = _catalog_by_id()
    expired_at = datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc)
    v2_service.snapshots.save(
        RecommendationSnapshot(
            snapshot_id="snap_expired_homepage",
            user_id="u_expired_scene",
            scene="homepage",
            products=[catalog["sku-redmi-k80"].model_copy(deep=True)],
            copies=[],
            generated_at=expired_at,
            expires_at=expired_at + timedelta(hours=1),
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/v2/users/u_expired_scene/recommendations", params={"scene": "homepage"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["scene"] == "homepage"
    assert payload["generated_at"] == expired_at.isoformat().replace("+00:00", "Z")
    assert payload["stale"] is True
    assert payload["pending_refresh"] is True

    pending = v2_service.events.list_by_status("pending")
    assert len(pending) == 1
    assert pending[0].payload["trigger"] == "snapshot_expired"
