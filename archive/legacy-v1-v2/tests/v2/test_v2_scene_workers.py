from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.v2.api.schemas import SessionMessageRequest
from app.v2.api.session_service import V2SessionService
from app.v2.core.models import UserProfile


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_scene_workers"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir()
    return path


@pytest.fixture
def v2_service() -> V2SessionService:
    tempdir = _workspace_tempdir()
    return V2SessionService(tempdir / "v2.sqlite3")


def _create_session(service: V2SessionService, user_id: str = "u_scene") -> str:
    return service.create_session(user_id).session_id


@pytest.mark.asyncio
async def test_v2_scene_default_advisory_turn_returns_no_product_artifacts(v2_service: V2SessionService):
    session_id = _create_session(v2_service)

    response = await v2_service.handle_message(session_id, SessionMessageRequest(message="budget 3000 phone gaming"))

    assert response.reply
    assert response.products == []
    assert response.comparisons == []
    assert response.copies == []
    assert response.clarification is None
    assert response.agent_details.terminal_state == "reply_ready"
    assert response.agent_details.steps_executed == 1
    assert response.agent_details.workers_called == ["preference_worker"]


@pytest.mark.asyncio
async def test_v2_scene_product_page_scene_clarifies_without_context(v2_service: V2SessionService):
    session_id = _create_session(v2_service)

    response = await v2_service.handle_message(session_id, SessionMessageRequest(message="compare this", scene="product_page"))

    assert response.reply == ""
    assert response.products == []
    assert response.clarification is not None
    assert "product_id" in response.clarification
    assert response.agent_details.terminal_state == "needs_clarification"
    assert response.agent_details.workers_called == []


@pytest.mark.asyncio
async def test_v2_scene_cart_scene_clarifies_without_context(v2_service: V2SessionService):
    session_id = _create_session(v2_service)

    response = await v2_service.handle_message(session_id, SessionMessageRequest(message="compare cart items", scene="cart"))

    assert response.reply == ""
    assert response.products == []
    assert response.clarification is not None
    assert "product_ids" in response.clarification
    assert response.agent_details.terminal_state == "needs_clarification"
    assert response.agent_details.workers_called == []


@pytest.mark.asyncio
async def test_v2_scene_explicit_recommendation_returns_structured_products(v2_service: V2SessionService):
    session_id = _create_session(v2_service, user_id="u_profile_scene")
    v2_service.user_profiles.save(UserProfile(user_id="u_profile_scene", preferred_categories=["手机"], preferred_brands=["Apple"], price_range=(0.0, 4000.0), cold_start=False))

    response = await v2_service.handle_message(session_id, SessionMessageRequest(message="recommend a phone"))

    assert response.products
    assert response.comparisons == []
    assert response.copies
    assert response.agent_details.workers_called == [
        "preference_worker",
        "catalog_worker",
        "inventory_worker",
        "copy_worker",
    ]


@pytest.mark.asyncio
async def test_v2_scene_product_page_comparison_returns_comparisons_without_copies(v2_service: V2SessionService):
    session_id = _create_session(v2_service)

    response = await v2_service.handle_message(
        session_id,
        SessionMessageRequest(message="compare this", scene="product_page", scene_context={"product_id": "sku-iphone-16-pro"}),
    )

    assert response.clarification is None
    assert response.products
    assert response.comparisons
    assert response.copies == []
    assert all(product.product_id != "sku-iphone-16-pro" for product in response.products)
    assert response.agent_details.workers_called == [
        "preference_worker",
        "catalog_worker",
        "inventory_worker",
        "comparison_worker",
    ]
