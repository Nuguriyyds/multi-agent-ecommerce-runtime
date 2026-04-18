from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel

from app.v2.core.hooks import HookBus
from app.v2.core.models import ManagerTurnContext, TaskRecord, ToolSpec, WorkerResult, WorkerTask
from app.v2.core.persistence import TaskRecordStore

ToolHandler = Callable[[dict[str, Any]], Any | Awaitable[Any]]


class NamedComponent(Protocol):
    name: str


T = TypeVar("T", bound=NamedComponent)


class BaseRegistry(Generic[T]):
    def __init__(self, component_label: str) -> None:
        self._component_label = component_label
        self._entries: dict[str, T] = {}

    def register(self, component: T) -> T:
        name = getattr(component, "name", "")
        if not name:
            raise ValueError(f"{self._component_label} must define a non-empty name")
        if name in self._entries:
            raise ValueError(f"{self._component_label} '{name}' is already registered")
        self._entries[name] = component
        return component

    def get(self, name: str) -> T:
        try:
            return self._entries[name]
        except KeyError as exc:
            raise KeyError(f"unknown {self._component_label}: {name}") from exc

    def has(self, name: str) -> bool:
        return name in self._entries

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))


class Manager(ABC):
    def __init__(self, name: str) -> None:
        if not name:
            raise ValueError("manager name must be non-empty")
        self.name = name

    @abstractmethod
    async def handle_turn(self, context: ManagerTurnContext, message: str) -> dict[str, Any]:
        """Handle one manager turn and return structured output."""


class ManagerRegistry(BaseRegistry[Manager]):
    def __init__(self) -> None:
        super().__init__("manager")


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler

    @property
    def name(self) -> str:
        return self.spec.name


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ToolTaskPersistenceContext:
    task_store: TaskRecordStore
    task_id: str
    task_scope: Literal["conversation", "background"] = "conversation"
    manager_name: str | None = None
    session_id: str | None = None
    turn_id: str | None = None
    event_id: str | None = None
    worker_name: str | None = None
    step: int | None = None


class ToolRegistry:
    def __init__(self, hook_bus: HookBus | None = None) -> None:
        self._entries: dict[str, RegisteredTool] = {}
        self._hook_bus = hook_bus

    def register(self, spec: ToolSpec, handler: ToolHandler) -> RegisteredTool:
        if spec.name in self._entries:
            raise ValueError(f"tool '{spec.name}' is already registered")
        registered = RegisteredTool(spec=spec, handler=handler)
        self._entries[spec.name] = registered
        return registered

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._entries[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def get_spec(self, name: str) -> ToolSpec:
        return self.get(name).spec

    def has(self, name: str) -> bool:
        return name in self._entries

    def list_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    async def invoke(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        hook_context: dict[str, Any] | None = None,
        task_persistence: ToolTaskPersistenceContext | None = None,
    ) -> Any:
        registered = self.get(name)
        request_payload = dict(payload or {})
        created_at = _utc_now() if task_persistence is not None else None
        started = perf_counter()
        await self._emit_tool_hook(
            "tool.before",
            name=name,
            payload=request_payload,
            extra=hook_context,
        )

        try:
            result = registered.handler(request_payload)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            if task_persistence is not None and created_at is not None:
                failed = TaskRecord(
                    task_id=task_persistence.task_id,
                    task_scope=task_persistence.task_scope,
                    session_id=task_persistence.session_id,
                    turn_id=task_persistence.turn_id,
                    event_id=task_persistence.event_id,
                    manager_name=task_persistence.manager_name,
                    worker_name=task_persistence.worker_name,
                    tool_name=name,
                    step=task_persistence.step,
                    status="failed",
                    input=_serialize_for_hook(request_payload),
                    error=str(exc),
                    latency_ms=(perf_counter() - started) * 1000,
                )
                task_persistence.task_store.save(
                    failed,
                    created_at=created_at,
                    updated_at=_utc_now(),
                )
            await self._emit_tool_hook(
                "tool.error",
                name=name,
                payload=request_payload,
                extra={
                    **(hook_context or {}),
                    "error": str(exc),
                },
            )
            raise

        if task_persistence is not None and created_at is not None:
            completed = TaskRecord(
                task_id=task_persistence.task_id,
                task_scope=task_persistence.task_scope,
                session_id=task_persistence.session_id,
                turn_id=task_persistence.turn_id,
                event_id=task_persistence.event_id,
                manager_name=task_persistence.manager_name,
                worker_name=task_persistence.worker_name,
                tool_name=name,
                step=task_persistence.step,
                status="completed",
                input=_serialize_for_hook(request_payload),
                output=_serialize_for_hook(result),
                latency_ms=(perf_counter() - started) * 1000,
            )
            task_persistence.task_store.save(
                completed,
                created_at=created_at,
                updated_at=_utc_now(),
            )
        await self._emit_tool_hook(
            "tool.after",
            name=name,
            payload=request_payload,
            extra={
                **(hook_context or {}),
                "result": _serialize_for_hook(result),
            },
        )
        return result

    async def _emit_tool_hook(
        self,
        hook_name: str,
        *,
        name: str,
        payload: dict[str, Any],
        extra: dict[str, Any] | None = None,
    ) -> None:
        if self._hook_bus is None:
            return
        await self._hook_bus.emit(
            hook_name,
            {
                "tool_name": name,
                "payload": _serialize_for_hook(payload),
                **(extra or {}),
            },
        )


class _AllowedToolRuntime:
    def __init__(self, registry: ToolRegistry, allowed_tools: frozenset[str]) -> None:
        self._registry = registry
        self._allowed_tools = allowed_tools

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return tuple(sorted(self._allowed_tools))

    async def call_tool(
        self,
        name: str,
        payload: dict[str, Any] | None = None,
        *,
        task_persistence: ToolTaskPersistenceContext | None = None,
    ) -> Any:
        if name not in self._allowed_tools:
            raise PermissionError(f"worker is not allowed to call tool '{name}'")
        return await self._registry.invoke(
            name,
            payload,
            task_persistence=task_persistence,
        )


class WorkerExecutionContext:
    __slots__ = (
        "event_id",
        "manager_name",
        "session_id",
        "turn_id",
        "_task_scope",
        "_tool_call_count",
        "_tool_runtime",
        "_tool_step",
        "_tool_step_key",
        "_tool_task_store",
    )

    def __init__(
        self,
        *,
        manager_name: str,
        tool_runtime: _AllowedToolRuntime,
        session_id: str | None = None,
        turn_id: str | None = None,
        event_id: str | None = None,
        task_store: TaskRecordStore | None = None,
        task_scope: Literal["conversation", "background"] = "conversation",
        tool_step: int | None = None,
        tool_step_key: str | None = None,
    ) -> None:
        self.manager_name = manager_name
        self.session_id = session_id
        self.turn_id = turn_id
        self.event_id = event_id
        self._tool_runtime = tool_runtime
        self._tool_task_store = task_store
        self._task_scope = task_scope
        self._tool_step = tool_step
        self._tool_step_key = tool_step_key or ""
        self._tool_call_count = 0

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return self._tool_runtime.allowed_tools

    async def call_tool(self, name: str, payload: dict[str, Any] | None = None) -> Any:
        task_persistence: ToolTaskPersistenceContext | None = None
        if (
            self._tool_task_store is not None
            and self.turn_id
            and self._tool_step_key
        ):
            self._tool_call_count += 1
            task_persistence = ToolTaskPersistenceContext(
                task_store=self._tool_task_store,
                task_id=f"tool_{self.turn_id}_{self._tool_step_key}_{self._tool_call_count}",
                task_scope=self._task_scope,
                manager_name=self.manager_name,
                session_id=self.session_id,
                turn_id=self.turn_id,
                event_id=self.event_id,
                step=self._tool_step,
            )
        return await self._tool_runtime.call_tool(
            name,
            payload,
            task_persistence=task_persistence,
        )


class Worker(ABC):
    def __init__(self, name: str, *, allowed_tools: set[str] | None = None) -> None:
        if not name:
            raise ValueError("worker name must be non-empty")
        self.name = name
        self.allowed_tools = frozenset(allowed_tools or set())

    async def run(
        self,
        task: WorkerTask,
        tool_registry: ToolRegistry,
        *,
        manager_name: str,
        session_id: str | None = None,
        turn_id: str | None = None,
        event_id: str | None = None,
        hook_bus: HookBus | None = None,
        task_store: TaskRecordStore | None = None,
        task_scope: Literal["conversation", "background"] = "conversation",
        tool_step_key: str | None = None,
    ) -> WorkerResult:
        if task.worker_name != self.name:
            raise ValueError(
                f"task is assigned to worker '{task.worker_name}', not '{self.name}'",
            )

        context = WorkerExecutionContext(
            manager_name=manager_name,
            session_id=session_id,
            turn_id=turn_id,
            event_id=event_id,
            task_store=task_store,
            task_scope=task_scope,
            tool_step=task.step,
            tool_step_key=tool_step_key or task.worker_name,
            tool_runtime=_AllowedToolRuntime(tool_registry, self.allowed_tools),
        )
        task_snapshot = {
            "manager_name": manager_name,
            "session_id": session_id,
            "turn_id": turn_id,
            "task": task.model_dump(mode="json"),
            "worker_name": self.name,
        }
        if hook_bus is not None:
            await hook_bus.emit("worker.started", task_snapshot)

        try:
            result = await self.execute(task, context)
        except Exception as exc:
            if hook_bus is not None:
                await hook_bus.emit(
                    "worker.failed",
                    {
                        **task_snapshot,
                        "error": str(exc),
                    },
                )
            raise

        if result.worker_name != self.name:
            result = result.model_copy(update={"worker_name": self.name})
        if hook_bus is not None:
            await hook_bus.emit(
                "worker.finished",
                {
                    **task_snapshot,
                    "result": _serialize_for_hook(result),
                },
            )
        return result

    @abstractmethod
    async def execute(
        self,
        task: WorkerTask,
        context: WorkerExecutionContext,
    ) -> WorkerResult:
        """Execute a single worker task using tool-only runtime access."""


class WorkerRegistry(BaseRegistry[Worker]):
    def __init__(self) -> None:
        super().__init__("worker")


def _serialize_for_hook(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            key: _serialize_for_hook(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_serialize_for_hook(item) for item in value]
    return value


__all__ = [
    "Manager",
    "ManagerRegistry",
    "RegisteredTool",
    "ToolTaskPersistenceContext",
    "ToolRegistry",
    "Worker",
    "WorkerExecutionContext",
    "WorkerRegistry",
]
