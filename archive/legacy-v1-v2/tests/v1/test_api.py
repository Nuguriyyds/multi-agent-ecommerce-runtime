from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app, get_ab_test_engine, get_supervisor
from app.v1.services.metrics import get_metrics_collector


@pytest.fixture(autouse=True)
def reset_singletons():
    get_ab_test_engine.cache_clear()
    get_supervisor.cache_clear()
    get_metrics_collector().reset()
    app.dependency_overrides.clear()
    yield
    get_ab_test_engine.cache_clear()
    get_supervisor.cache_clear()
    get_metrics_collector().reset()
    app.dependency_overrides.clear()


def test_post_recommend_returns_complete_json_response():
    with TestClient(app) as client:
        response = client.post(
            "/recommend",
            json={
                "user_id": "u_high_value",
                "num_items": 3,
            },
        )

    assert response.status_code == 200
    assert "X-Trace-ID" in response.headers

    payload = response.json()
    assert payload["request_id"] == response.headers["X-Trace-ID"]
    assert payload["user_id"] == "u_high_value"
    assert payload["experiment_group"] in {"control", "treatment"}
    assert payload["latency_ms"] >= 0
    assert {
        "request_id",
        "user_id",
        "profile",
        "recommendations",
        "copies",
        "inventory_status",
        "experiment_group",
        "agent_details",
        "latency_ms",
    }.issubset(payload)
    assert len(payload["recommendations"]) == 3
    assert len(payload["copies"]) == 3
    assert len(payload["inventory_status"]) >= 3
    assert {
        "user_profile",
        "product_rec_coarse",
        "product_rec_ranked",
        "inventory",
        "marketing_copy",
    } == set(payload["agent_details"])

    first_product = payload["recommendations"][0]
    assert {"id", "name", "score", "category"}.issubset(first_product)


def test_post_api_v1_recommend_returns_cold_start_response_for_unknown_user():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/recommend",
            json={
                "user_id": "u_missing",
                "num_items": 4,
            },
        )

    assert response.status_code == 200
    assert "X-Trace-ID" in response.headers

    payload = response.json()
    assert payload["profile"]["cold_start"] is True
    assert payload["profile"]["segments"] == ["new_user"]
    assert len(payload["recommendations"]) == 4
    assert len(payload["copies"]) == 4
    assert payload["latency_ms"] >= 0


def test_post_recommend_preserves_client_trace_id_header():
    with TestClient(app) as client:
        response = client.post(
            "/recommend",
            headers={"X-Trace-ID": "trace-test-001"},
            json={
                "user_id": "u_price_sensitive",
                "num_items": 2,
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == "trace-test-001"
    assert response.json()["request_id"] == "trace-test-001"
