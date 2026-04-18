from __future__ import annotations

import json
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from starlette.responses import JSONResponse

from app.v3.observability import log_event

_LOGGER = logging.getLogger(__name__)


def install_trace_middleware(application: FastAPI) -> None:
    @application.middleware("http")
    async def trace_id_middleware(request: Request, call_next):
        if not request.url.path.startswith("/api/v3/"):
            return await call_next(request)

        request.state.trace_id = f"http-{uuid4().hex[:12]}"
        started_at = perf_counter()
        response = await call_next(request)
        latency_ms = max(0, int(round((perf_counter() - started_at) * 1000)))
        final_response = await _inject_trace_and_latency(request, response, latency_ms=latency_ms)
        log_event(
            _LOGGER,
            "http.request.finished",
            trace_id=final_response.headers.get("X-Trace-ID", request.state.trace_id),
            payload={
                "path": request.url.path,
                "method": request.method,
                "status_code": final_response.status_code,
                "latency_ms": latency_ms,
            },
        )
        return final_response


async def _inject_trace_and_latency(
    request: Request,
    response: Response,
    *,
    latency_ms: int,
) -> Response:
    response_trace_id = getattr(request.state, "response_trace_id", None) or request.state.trace_id
    content_type = response.headers.get("content-type", "").lower()
    if "application/json" not in content_type:
        response.headers["X-Trace-ID"] = response_trace_id
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() != "content-length"
    }

    payload = _decode_json_object(body)
    if payload is None:
        headers["X-Trace-ID"] = response_trace_id
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
            background=response.background,
        )

    payload.setdefault("latency_ms", latency_ms)
    payload_trace_id = payload.get("trace_id")
    if isinstance(payload_trace_id, str) and payload_trace_id.strip():
        response_trace_id = payload_trace_id

    headers["X-Trace-ID"] = response_trace_id
    return JSONResponse(
        content=payload,
        status_code=response.status_code,
        headers=headers,
        background=response.background,
    )


def _decode_json_object(body: bytes) -> dict[str, object] | None:
    if not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        return payload
    return None


__all__ = ["install_trace_middleware"]
