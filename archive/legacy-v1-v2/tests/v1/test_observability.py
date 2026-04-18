from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.shared.data.inventory_store import InventoryStore
from app.shared.data.product_catalog import ProductCatalog
from app.shared.observability.logging_utils import JsonLogFormatter
from app.v1.agents.inventory import InventoryAgent
from app.v1.agents.marketing_copy import MarketingCopyAgent
from app.v1.agents.product_rec import ProductRecAgent
from app.v1.agents.user_profile import UserProfileAgent
from app.v1.orchestrator.supervisor import Supervisor
from app.v1.services.feature_store import FeatureStore
from app.v1.services.llm_service import LLMService
from app.v1.services.metrics import get_metrics_collector
from main import app, get_ab_test_engine, get_supervisor


class SlowLLMService(LLMService):
    def __init__(self, *, delay: float) -> None:
        self.delay = delay

    async def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        await asyncio.sleep(self.delay)
        return "{}"


@pytest.fixture(autouse=True)
def reset_state():
    get_ab_test_engine.cache_clear()
    get_supervisor.cache_clear()
    get_metrics_collector().reset()
    app.dependency_overrides.clear()
    yield
    get_ab_test_engine.cache_clear()
    get_supervisor.cache_clear()
    get_metrics_collector().reset()
    app.dependency_overrides.clear()


def test_metrics_endpoint_returns_agent_call_counts_avg_latency_and_error_rate():
    supervisor = Supervisor(
        user_profile_agent=UserProfileAgent(
            feature_store=FeatureStore(),
            llm_service=LLMService(),
        ),
        product_rec_agent=ProductRecAgent(product_catalog=ProductCatalog()),
        inventory_agent=InventoryAgent(inventory_store=InventoryStore()),
        marketing_copy_agent=MarketingCopyAgent(
            llm_service=SlowLLMService(delay=0.05),
            timeout=0.01,
            max_retries=0,
        ),
    )
    app.dependency_overrides[get_supervisor] = lambda: supervisor

    with TestClient(app) as client:
        recommend_response = client.post(
            "/recommend",
            json={"user_id": "u_high_value", "num_items": 3},
        )
        metrics_response = client.get("/metrics")
        prefixed_metrics_response = client.get("/api/v1/metrics")

    assert recommend_response.status_code == 200
    assert metrics_response.status_code == 200
    assert prefixed_metrics_response.status_code == 200
    assert metrics_response.json() == prefixed_metrics_response.json()

    agents = metrics_response.json()["agents"]
    assert agents["user_profile"]["calls"] == 1
    assert agents["product_rec"]["calls"] == 2
    assert agents["inventory"]["calls"] == 1
    assert agents["marketing_copy"]["calls"] == 1
    assert agents["marketing_copy"]["error_rate"] == 1.0
    assert agents["user_profile"]["error_rate"] == 0.0
    assert agents["inventory"]["error_rate"] == 0.0
    assert all(metric["avg_latency_ms"] >= 0 for metric in agents.values())


def test_recommend_logs_are_json_and_include_trace_id_and_agent_latencies():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())

    root_logger = logging.getLogger()
    original_level = root_logger.level
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    try:
        with TestClient(app) as client:
            response = client.post(
                "/recommend",
                headers={"X-Trace-ID": "trace-log-001"},
                json={"user_id": "u_high_value", "num_items": 2},
            )
    finally:
        root_logger.removeHandler(handler)
        root_logger.setLevel(original_level)

    assert response.status_code == 200

    records = [
        json.loads(line)
        for line in stream.getvalue().splitlines()
        if line.strip()
    ]
    assert records

    supervisor_completed = next(
        record
        for record in records
        if record.get("message") == "supervisor_completed"
    )
    assert supervisor_completed["trace_id"] == "trace-log-001"
    assert set(supervisor_completed["stage_latencies_ms"]) == {
        "user_profile",
        "product_rec_coarse",
        "product_rec_ranked",
        "inventory",
        "marketing_copy",
    }
    assert all(
        latency >= 0
        for latency in supervisor_completed["stage_latencies_ms"].values()
    )

    agent_completed = next(
        record
        for record in records
        if record.get("message") == "agent_completed"
    )
    assert agent_completed["trace_id"] == "trace-log-001"
    assert agent_completed["agent"] in {
        "user_profile",
        "product_rec",
        "inventory",
        "marketing_copy",
    }
    assert agent_completed["latency_ms"] >= 0
