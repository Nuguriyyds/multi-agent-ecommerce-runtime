from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.shared.config.settings import get_settings
from app.shared.models.domain import MetricsResponse, RecommendationRequest, RecommendationResponse
from app.shared.observability.logging_utils import configure_logging
from app.shared.observability.trace import clear_trace_id, generate_trace_id, get_trace_id, set_trace_id
from app.v1.orchestrator.supervisor import Supervisor
from app.v1.services.ab_test import ABTestEngine
from app.v1.services.metrics import MetricsCollector, get_metrics_collector
from app.v2.api.schemas import (
    CreateSessionRequest,
    CreateSessionResponse,
    FeedbackEventRequest,
    FeedbackEventResponse,
    RecommendationReadRequest,
    RecommendationReadResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    TurnTraceResponse,
)
from app.v2.api.session_service import V2SessionService

configure_logging()
logger = logging.getLogger(__name__)

settings = get_settings()

app = FastAPI(
    title="Multi-Agent Ecommerce System",
    version="0.1.0",
)


@lru_cache
def get_supervisor() -> Supervisor:
    return Supervisor()


@lru_cache
def get_ab_test_engine() -> ABTestEngine:
    return ABTestEngine()


@lru_cache
def get_v2_session_service() -> V2SessionService:
    return V2SessionService(Path(".tmp") / "v2_runtime" / "v2.sqlite3")


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID") or generate_trace_id()
    token = set_trace_id(trace_id)
    request.state.trace_id = trace_id

    logger.info(
        "api_request_started",
        extra={
            "method": request.method,
            "path": request.url.path,
        },
    )

    try:
        response = await call_next(request)
    except Exception:  # noqa: BLE001
        logger.exception(
            "api_request_failed",
            extra={
                "method": request.method,
                "path": request.url.path,
            },
        )
        response = JSONResponse(
            status_code=500,
            content={
                "detail": "internal_server_error",
                "trace_id": trace_id,
            },
        )

    logger.info(
        "api_request_completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
        },
    )
    clear_trace_id(token)

    response.headers["X-Trace-ID"] = trace_id
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/v2/sessions", response_model=CreateSessionResponse)
async def create_v2_session(
    payload: CreateSessionRequest,
    service: V2SessionService = Depends(get_v2_session_service),
) -> CreateSessionResponse:
    result = service.create_session(payload.user_id)
    logger.info(
        "v2_session_created",
        extra={
            "user_id": payload.user_id,
            "session_id": result.session_id,
            "manager_type": result.manager_type,
        },
    )
    return result


@app.post("/api/v2/sessions/{session_id}/messages", response_model=SessionMessageResponse)
async def post_v2_message(
    session_id: str,
    payload: SessionMessageRequest,
    service: V2SessionService = Depends(get_v2_session_service),
) -> SessionMessageResponse:
    try:
        result = await service.handle_message(session_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown session: {exc.args[0]}") from exc
    logger.info(
        "v2_message_handled",
        extra={
            "session_id": session_id,
            "scene": payload.scene,
            "scene_context": payload.scene_context,
            "terminal_state": result.agent_details.terminal_state,
            "workers_called": result.agent_details.workers_called,
            "preference_count": len(result.preferences_extracted),
            "product_count": len(result.products),
            "comparison_count": len(result.comparisons),
            "copy_count": len(result.copies),
            "refresh_triggered": result.recommendation_refresh_triggered,
            "latency_ms": result.agent_details.latency_ms,
        },
    )
    return result


@app.get("/api/v2/users/{user_id}/recommendations", response_model=RecommendationReadResponse)
async def get_v2_recommendations(
    user_id: str,
    scene: str = "default",
    product_id: str | None = None,
    product_ids: list[str] = Query(default_factory=list),
    service: V2SessionService = Depends(get_v2_session_service),
) -> RecommendationReadResponse:
    result = await service.read_recommendations(
        user_id,
        RecommendationReadRequest(
            scene=scene,
            product_id=product_id,
            product_ids=product_ids,
        ),
    )
    logger.info(
        "v2_recommendation_read",
        extra={
            "user_id": user_id,
            "scene_requested": scene,
            "scene_served": result.scene,
            "product_id": product_id,
            "product_ids": product_ids,
            "product_count": len(result.products),
            "copy_count": len(result.copies),
        },
    )
    return result


@app.post("/api/v2/users/{user_id}/feedback-events", response_model=FeedbackEventResponse)
async def post_v2_feedback_event(
    user_id: str,
    payload: FeedbackEventRequest,
    service: V2SessionService = Depends(get_v2_session_service),
) -> FeedbackEventResponse:
    result = await service.record_feedback_event(user_id, payload)
    logger.info(
        "v2_feedback_recorded",
        extra={
            "user_id": user_id,
            "event_id": result.event_id,
            "event_type": payload.event_type,
            "scene": payload.scene,
            "product_id": payload.product_id,
            "product_ids": payload.product_ids,
        },
    )
    return result


@app.get(
    "/api/v2/sessions/{session_id}/turns/{user_turn_number}/trace",
    response_model=TurnTraceResponse,
)
async def get_v2_turn_trace(
    session_id: str,
    user_turn_number: int,
    service: V2SessionService = Depends(get_v2_session_service),
) -> TurnTraceResponse:
    try:
        return service.get_turn_trace(session_id, user_turn_number)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"unknown turn trace: {exc.args[0]}") from exc


@app.post("/recommend", response_model=RecommendationResponse)
@app.post(f"{settings.api_prefix}/recommend", response_model=RecommendationResponse)
async def recommend(
    payload: RecommendationRequest,
    supervisor: Supervisor = Depends(get_supervisor),
    ab_test_engine: ABTestEngine = Depends(get_ab_test_engine),
) -> RecommendationResponse:
    trace_id = get_trace_id() or generate_trace_id()
    assignment = ab_test_engine.assign_user(
        user_id=payload.user_id,
        experiment_name=settings.recommend_experiment_name,
    )

    logger.info(
        "recommend_started",
        extra={
            "request_id": trace_id,
            "user_id": payload.user_id,
            "experiment": assignment.experiment_name,
            "variant": assignment.variant_name,
        },
    )

    response = await supervisor.recommend(
        {
            **payload.model_dump(mode="json"),
            "request_id": trace_id,
            "experiment_name": assignment.experiment_name,
            "experiment_parameters": assignment.parameters,
        },
    )
    response = response.model_copy(
        update={"experiment_group": assignment.variant_name},
        deep=True,
    )

    logger.info(
        "recommend_completed",
        extra={
            "request_id": trace_id,
            "user_id": payload.user_id,
            "experiment": assignment.experiment_name,
            "variant": assignment.variant_name,
            "recommendations": len(response.recommendations),
            "latency_ms": response.latency_ms,
            "stage_latencies_ms": {
                stage: detail.latency_ms
                for stage, detail in response.agent_details.items()
            },
        },
    )
    return response


@app.get("/metrics", response_model=MetricsResponse)
@app.get(f"{settings.api_prefix}/metrics", response_model=MetricsResponse)
async def metrics(
    metrics_collector: MetricsCollector = Depends(get_metrics_collector),
) -> MetricsResponse:
    snapshot = metrics_collector.snapshot()
    logger.info(
        "metrics_requested",
        extra={
            "agent_metrics": snapshot.model_dump(mode="json")["agents"],
        },
    )
    return snapshot


__all__ = [
    "app",
    "get_ab_test_engine",
    "get_supervisor",
    "get_v2_session_service",
]
