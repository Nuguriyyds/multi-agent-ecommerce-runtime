from __future__ import annotations

from contextvars import ContextVar, Token
from uuid import uuid4

_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="")


def generate_trace_id() -> str:
    return uuid4().hex


def get_trace_id() -> str:
    return _TRACE_ID.get()


def set_trace_id(trace_id: str) -> Token[str]:
    return _TRACE_ID.set(trace_id)


def clear_trace_id(token: Token[str]) -> None:
    _TRACE_ID.reset(token)
