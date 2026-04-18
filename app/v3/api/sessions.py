from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Request, status
from pydantic import Field

from app.v3.models import SessionState
from app.v3.models.base import V3Model
from app.v3.observability import log_event

router = APIRouter(tags=["v3"])
_LOGGER = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CreateSessionRequest(V3Model):
    user_id: str | None = None


class CreateSessionResponse(V3Model):
    session_id: str
    user_id: str | None = None


@dataclass
class SessionRecord:
    state: SessionState
    created_at: datetime
    last_activity_at: datetime
    expired_reason: str | None = None
    expired_at: datetime | None = None

    def check_expiry(
        self,
        *,
        now: datetime,
        max_turns: int,
        idle_minutes: int,
    ) -> str | None:
        if self.expired_reason is not None:
            return self.expired_reason
        if self.state.turn_count >= max_turns:
            return "max_turns_reached"
        if now - self.last_activity_at > timedelta(minutes=idle_minutes):
            return "idle_timeout"
        return None


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    def create(self, *, user_id: str | None = None, now: datetime | None = None) -> SessionRecord:
        created_at = now or utc_now()
        session_id = f"session-{uuid4().hex[:12]}"
        record = SessionRecord(
            state=SessionState(session_id=session_id, user_id=user_id),
            created_at=created_at,
            last_activity_at=created_at,
        )
        self._sessions[session_id] = record
        _LOGGER.info("Created V3 session session_id=%s user_id=%s", session_id, user_id)
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def touch(self, session_id: str, *, now: datetime | None = None) -> SessionRecord:
        record = self._sessions[session_id]
        record.last_activity_at = now or utc_now()
        return record

    def mark_expired(
        self,
        session_id: str,
        *,
        reason: str,
        now: datetime | None = None,
    ) -> SessionRecord:
        record = self._sessions[session_id]
        if record.expired_reason is None:
            record.expired_reason = reason
            record.expired_at = now or utc_now()
            record.state.session_working_memory = {}
            _LOGGER.info("Expired V3 session session_id=%s reason=%s", session_id, reason)
        return record


@router.post("/api/v3/sessions", status_code=status.HTTP_201_CREATED)
async def create_session(
    request: Request,
    payload: CreateSessionRequest | None = None,
) -> dict[str, object]:
    session_store: SessionStore = request.app.state.v3_session_store
    body = payload or CreateSessionRequest()
    record = session_store.create(user_id=body.user_id)
    log_event(
        _LOGGER,
        "api.session.created",
        trace_id=getattr(request.state, "trace_id", None),
        session_id=record.state.session_id,
        payload={"user_id": record.state.user_id},
    )
    return CreateSessionResponse(
        session_id=record.state.session_id,
        user_id=record.state.user_id,
    ).model_dump(mode="json")


__all__ = [
    "CreateSessionRequest",
    "CreateSessionResponse",
    "SessionRecord",
    "SessionStore",
    "router",
    "utc_now",
]
