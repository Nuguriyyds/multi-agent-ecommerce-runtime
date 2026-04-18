from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from app.v3.models.base import V3Model
from app.v3.observability import log_event

from .sessions import SessionStore, utc_now

router = APIRouter(tags=["v3"])
_LOGGER = logging.getLogger(__name__)


class MessageRequest(V3Model):
    message: str = Field(min_length=1)


@router.post("/api/v3/sessions/{session_id}/messages")
async def post_message(
    session_id: str,
    payload: MessageRequest,
    request: Request,
) -> dict[str, object]:
    session_store: SessionStore = request.app.state.v3_session_store
    settings = request.app.state.settings
    agent = request.app.state.v3_main_agent

    record = session_store.get(session_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "session_not_found", "session_id": session_id},
        )

    now = utc_now()
    expired_reason = record.check_expiry(
        now=now,
        max_turns=settings.session_max_turns,
        idle_minutes=settings.session_idle_minutes,
    )
    if expired_reason is not None:
        session_store.mark_expired(session_id, reason=expired_reason, now=now)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "session_expired",
                "reason": expired_reason,
                "session_id": session_id,
            },
        )

    log_event(
        _LOGGER,
        "api.message.started",
        trace_id=getattr(request.state, "trace_id", None),
        session_id=session_id,
        turn_number=record.state.turn_count + 1,
        payload={"message_length": len(payload.message)},
    )
    turn_result = await agent.run_turn(record.state, payload.message)
    session_store.touch(session_id, now=utc_now())
    if record.state.turn_count >= settings.session_max_turns:
        session_store.mark_expired(
            session_id,
            reason="max_turns_reached",
            now=utc_now(),
        )

    request.state.response_trace_id = turn_result.trace_id
    log_event(
        _LOGGER,
        "api.message.finished",
        trace_id=turn_result.trace_id,
        session_id=session_id,
        turn_number=turn_result.turn_number,
        payload={
            "status": turn_result.status,
            "completed_steps": turn_result.completed_steps,
        },
    )
    return turn_result.model_dump(mode="json")


__all__ = ["MessageRequest", "router"]
