from __future__ import annotations

import copy
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeAlias

from app.v3.models import HookEvent, HookPoint, HookResult

HookHandlerResult: TypeAlias = HookResult | Mapping[str, Any] | None
HookHandler: TypeAlias = Callable[[HookEvent], HookHandlerResult | Awaitable[HookHandlerResult]]


class HookBus:
    """Observe-only hook registry with ordered dispatch and failure isolation."""

    def __init__(self) -> None:
        self._handlers: dict[HookPoint, list[HookHandler]] = {point: [] for point in HookPoint}
        self._logger = logging.getLogger(__name__)

    def register(self, point: HookPoint | str, handler: HookHandler) -> HookHandler:
        normalized_point = self._normalize_point(point)
        self._handlers[normalized_point].append(handler)
        self._logger.info(
            "Registered hook handler %s for %s",
            self._handler_name(handler),
            normalized_point.value,
        )
        return handler

    async def emit(self, point: HookPoint | str, event: HookEvent) -> list[HookResult]:
        normalized_point = self._normalize_point(point)
        if event.hook_point != normalized_point:
            raise ValueError(
                f"HookEvent hook_point {event.hook_point.value!r} does not match emit point {normalized_point.value!r}"
            )

        results: list[HookResult] = []
        for handler in self._handlers[normalized_point]:
            result = await self._invoke_handler(normalized_point, event, handler)
            results.append(result)
        return results

    async def _invoke_handler(
        self,
        point: HookPoint,
        event: HookEvent,
        handler: HookHandler,
    ) -> HookResult:
        handler_name = self._handler_name(handler)
        handler_event = event.model_copy(deep=True)
        before = copy.deepcopy(handler_event.model_dump(mode="python"))

        try:
            result = handler(handler_event)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            self._logger.error(
                "Hook handler %s failed at %s: %s",
                handler_name,
                point.value,
                exc,
                exc_info=True,
            )
            return HookResult(handler_name=handler_name, accepted=False, note=str(exc))

        after = handler_event.model_dump(mode="python")
        if after != before:
            self._logger.warning(
                "Hook handler %s mutated HookEvent for %s; mutations were discarded",
                handler_name,
                point.value,
            )

        try:
            return self._normalize_result(handler_name, result)
        except Exception as exc:
            self._logger.error(
                "Hook handler %s returned invalid result at %s: %s",
                handler_name,
                point.value,
                exc,
                exc_info=True,
            )
            return HookResult(handler_name=handler_name, accepted=False, note="invalid_hook_result")

    @staticmethod
    def _normalize_point(point: HookPoint | str) -> HookPoint:
        if isinstance(point, HookPoint):
            return point
        return HookPoint(point)

    @staticmethod
    def _handler_name(handler: HookHandler) -> str:
        return getattr(handler, "__name__", handler.__class__.__name__)

    @staticmethod
    def _normalize_result(handler_name: str, result: HookHandlerResult) -> HookResult:
        if result is None:
            return HookResult(handler_name=handler_name)

        if isinstance(result, HookResult):
            return result.model_copy(update={"handler_name": handler_name}, deep=True)

        if isinstance(result, Mapping):
            payload = dict(result)
            payload.setdefault("handler_name", handler_name)
            return HookResult.model_validate(payload)

        raise TypeError(f"Unsupported hook result type: {type(result).__name__}")
