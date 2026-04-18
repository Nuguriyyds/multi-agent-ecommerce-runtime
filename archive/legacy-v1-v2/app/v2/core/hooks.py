from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

CORE_HOOK_NAMES: tuple[str, ...] = (
    "turn.started",
    "turn.finished",
    "worker.started",
    "worker.finished",
    "worker.failed",
    "tool.before",
    "tool.after",
    "tool.error",
    "profile.updated",
    "snapshot.refreshed",
    "background_task.started",
    "background_task.finished",
    "background_task.failed",
)

HookHandler = Callable[
    [dict[str, Any]],
    BaseModel | dict[str, Any] | None | Awaitable[BaseModel | dict[str, Any] | None],
]


class HookResult(BaseModel):
    hook_name: str
    listener_name: str
    updates: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RegisteredHook:
    hook_name: str
    listener_name: str
    handler: HookHandler


class HookBus:
    def __init__(self, *, hook_names: tuple[str, ...] = CORE_HOOK_NAMES) -> None:
        self._hook_names = tuple(hook_names)
        self._listeners: dict[str, list[RegisteredHook]] = {
            hook_name: []
            for hook_name in self._hook_names
        }

    def list_hooks(self) -> tuple[str, ...]:
        return self._hook_names

    def list_listeners(self, hook_name: str) -> tuple[str, ...]:
        self._require_hook_name(hook_name)
        return tuple(listener.listener_name for listener in self._listeners[hook_name])

    def register(
        self,
        hook_name: str,
        listener_name: str,
        handler: HookHandler,
    ) -> RegisteredHook:
        self._require_hook_name(hook_name)
        if not listener_name:
            raise ValueError("hook listener_name must be non-empty")
        if any(
            registered.listener_name == listener_name
            for registered in self._listeners[hook_name]
        ):
            raise ValueError(
                f"hook listener '{listener_name}' is already registered for '{hook_name}'",
            )

        registration = RegisteredHook(
            hook_name=hook_name,
            listener_name=listener_name,
            handler=handler,
        )
        self._listeners[hook_name].append(registration)
        return registration

    async def emit(
        self,
        hook_name: str,
        snapshot: Mapping[str, Any] | BaseModel | None = None,
    ) -> tuple[HookResult, ...]:
        self._require_hook_name(hook_name)
        source_snapshot = self._normalize_snapshot(snapshot)
        results: list[HookResult] = []

        for listener in self._listeners[hook_name]:
            listener_snapshot = deepcopy(source_snapshot)
            outcome = listener.handler(listener_snapshot)
            if inspect.isawaitable(outcome):
                outcome = await outcome
            results.append(
                self._coerce_result(
                    hook_name=hook_name,
                    listener_name=listener.listener_name,
                    outcome=outcome,
                ),
            )

        return tuple(results)

    def _require_hook_name(self, hook_name: str) -> None:
        if hook_name not in self._listeners:
            raise ValueError(f"unknown hook '{hook_name}'")

    @staticmethod
    def _normalize_snapshot(
        snapshot: Mapping[str, Any] | BaseModel | None,
    ) -> dict[str, Any]:
        if snapshot is None:
            return {}
        if isinstance(snapshot, BaseModel):
            return snapshot.model_dump(mode="json")
        if isinstance(snapshot, Mapping):
            return dict(snapshot)
        raise TypeError("hook snapshot must be a mapping, BaseModel, or None")

    @staticmethod
    def _coerce_result(
        *,
        hook_name: str,
        listener_name: str,
        outcome: BaseModel | dict[str, Any] | None,
    ) -> HookResult:
        if outcome is None:
            payload: dict[str, Any] = {}
        elif isinstance(outcome, HookResult):
            return outcome.model_copy(
                update={
                    "hook_name": hook_name,
                    "listener_name": listener_name,
                },
            )
        elif isinstance(outcome, BaseModel):
            payload = outcome.model_dump(mode="json")
        elif isinstance(outcome, dict):
            payload = outcome
        else:
            raise TypeError("hook handlers must return HookResult, BaseModel, dict, or None")

        return HookResult.model_validate(
            {
                "hook_name": hook_name,
                "listener_name": listener_name,
                **payload,
            },
        )


__all__ = [
    "CORE_HOOK_NAMES",
    "HookBus",
    "HookResult",
    "RegisteredHook",
]
