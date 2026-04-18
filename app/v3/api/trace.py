from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from app.v3.observability import log_event

router = APIRouter(tags=["v3"])
_LOGGER = logging.getLogger(__name__)


@router.get("/api/v3/sessions/{session_id}/turns/{turn_number}/trace")
async def get_turn_trace(
    session_id: str,
    turn_number: int,
    request: Request,
) -> dict[str, object]:
    trace = request.app.state.v3_main_agent.trace_store.get(session_id, turn_number)
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "trace_not_found",
                "session_id": session_id,
                "turn_number": turn_number,
            },
        )

    request.state.response_trace_id = trace.trace_id
    log_event(
        _LOGGER,
        "api.trace.fetched",
        trace_id=trace.trace_id,
        session_id=session_id,
        turn_number=turn_number,
        payload={
            "terminal_state": trace.terminal_state,
            "decision_count": len(trace.decisions),
            "invocation_count": len(trace.invocations),
        },
    )
    return trace.model_dump(mode="json")


__all__ = ["router"]
