from __future__ import annotations

import logging
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field, field_validator

from app.v3.models.base import V3Model
from app.v3.observability import ObservabilityStore, log_event
from app.v3.registry import CapabilityNotFound, MCPProvider

from .sessions import SessionStore

router = APIRouter(tags=["v3"])
_LOGGER = logging.getLogger(__name__)

FeedbackSignal = Literal["interested", "not_interested", "clicked", "ignored"]


class RecommendationFeedbackRequest(V3Model):
    sku: str = Field(min_length=1)
    signal: FeedbackSignal
    source: str = "api"

    @field_validator("sku", "source")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized


@router.get("/api/v3/sessions/{session_id}/observability")
async def get_session_observability(session_id: str, request: Request) -> dict[str, object]:
    _get_session_or_404(session_id, request)
    try:
        provider = request.app.state.v3_registry.get("observability_metrics_query")
    except CapabilityNotFound:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "observability_mcp_unavailable"},
        ) from None
    if not isinstance(provider, MCPProvider):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "observability_mcp_unavailable"},
        )

    observation = await provider.invoke({"session_id": session_id})
    snippets = observation.payload.get("snippets")
    if not isinstance(snippets, list) or not snippets or not isinstance(snippets[0], dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "observability_mcp_invalid_payload"},
        )

    log_event(
        _LOGGER,
        "api.observability.fetched",
        trace_id=getattr(request.state, "trace_id", None),
        session_id=session_id,
        payload={"mcp_tool": "observability_metrics_query"},
    )
    return snippets[0]


@router.post("/api/v3/sessions/{session_id}/recommendation_feedback")
async def post_recommendation_feedback(
    session_id: str,
    payload: RecommendationFeedbackRequest,
    request: Request,
) -> dict[str, object]:
    _get_session_or_404(session_id, request)
    observability_store: ObservabilityStore = request.app.state.v3_observability_store
    started_at = perf_counter()
    event = observability_store.record_feedback(
        session_id,
        sku=payload.sku,
        signal=payload.signal,
        source=payload.source,
    )
    snapshot = observability_store.snapshot(session_id)
    elapsed_ms = max(0, int(round((perf_counter() - started_at) * 1000)))

    log_event(
        _LOGGER,
        "api.recommendation_feedback.recorded",
        trace_id=getattr(request.state, "trace_id", None),
        session_id=session_id,
        payload={
            "sku": event.sku,
            "signal": event.signal,
            "source": event.source,
            "elapsed_ms": elapsed_ms,
            "memory_policy": "session_metric_only",
        },
    )
    return {
        "session_id": session_id,
        "event": event.model_dump(mode="json"),
        "memory_policy": "session_metric_only",
        "observability": snapshot.model_dump(mode="json"),
    }


def _get_session_or_404(session_id: str, request: Request):
    session_store: SessionStore = request.app.state.v3_session_store
    record = session_store.get(session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found", "session_id": session_id},
        )
    return record


__all__ = [
    "RecommendationFeedbackRequest",
    "router",
]
