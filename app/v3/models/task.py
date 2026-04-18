from __future__ import annotations

from enum import Enum

from pydantic import Field

from .base import V3Model
from .trace import InvocationRecord


class TaskStatus(str, Enum):
    pending = "pending"
    ready = "ready"
    running = "running"
    done = "done"
    failed = "failed"
    blocked = "blocked"


class TurnTask(V3Model):
    task_id: str
    name: str
    status: TaskStatus = TaskStatus.pending
    depends_on: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    invocations: list[InvocationRecord] = Field(default_factory=list)
    description: str | None = None


class TurnTaskBoard(V3Model):
    tasks: list[TurnTask] = Field(default_factory=list)
    current_task_id: str | None = None
    ready_task_ids: list[str] = Field(default_factory=list)
    blocked_task_ids: list[str] = Field(default_factory=list)
    completed_task_ids: list[str] = Field(default_factory=list)

    @classmethod
    def create(cls, tasks: list[TurnTask] | None = None) -> "TurnTaskBoard":
        board = cls(tasks=[task.model_copy(deep=True) for task in tasks or []])
        board._recompute_indexes()
        return board

    def add_task(self, task: TurnTask) -> TurnTask:
        if self.get_task(task.task_id) is not None:
            raise ValueError(f"Task {task.task_id!r} is already present on this turn board.")

        stored_task = task.model_copy(deep=True)
        self.tasks.append(stored_task)
        self._recompute_indexes()
        return stored_task

    def get_task(self, task_id: str) -> TurnTask | None:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    def next_ready(self) -> TurnTask | None:
        self._recompute_indexes()
        for task in self.tasks:
            if task.status == TaskStatus.ready:
                task.status = TaskStatus.running
                task.blocked_reason = None
                self.current_task_id = task.task_id
                self._recompute_indexes()
                return task

        self.current_task_id = None
        return None

    def mark_done(self, task_id: str) -> TurnTask:
        task = self._require_task(task_id)
        task.status = TaskStatus.done
        task.blocked_reason = None
        if self.current_task_id == task_id:
            self.current_task_id = None
        self._recompute_indexes()
        return task

    def mark_failed(self, task_id: str, reason: str | None = None) -> TurnTask:
        task = self._require_task(task_id)
        task.status = TaskStatus.failed
        task.blocked_reason = reason
        if self.current_task_id == task_id:
            self.current_task_id = None
        self._recompute_indexes()
        return task

    def mark_blocked(self, task_id: str, reason: str | None = None) -> TurnTask:
        task = self._require_task(task_id)
        task.status = TaskStatus.blocked
        task.blocked_reason = reason or task.blocked_reason or "waiting_on_dependencies"
        if self.current_task_id == task_id:
            self.current_task_id = None
        self._recompute_indexes()
        return task

    def _require_task(self, task_id: str) -> TurnTask:
        task = self.get_task(task_id)
        if task is None:
            raise KeyError(f"Unknown task_id: {task_id}")
        return task

    def _recompute_indexes(self) -> None:
        completed_ids = {
            task.task_id
            for task in self.tasks
            if task.status == TaskStatus.done
        }

        for task in self.tasks:
            if task.status in {TaskStatus.done, TaskStatus.failed, TaskStatus.running}:
                continue

            if self._dependencies_satisfied(task, completed_ids):
                task.status = TaskStatus.ready
                task.blocked_reason = None
            elif task.depends_on:
                task.status = TaskStatus.blocked
                task.blocked_reason = task.blocked_reason or "waiting_on_dependencies"

        self.ready_task_ids = [task.task_id for task in self.tasks if task.status == TaskStatus.ready]
        self.blocked_task_ids = [task.task_id for task in self.tasks if task.status == TaskStatus.blocked]
        self.completed_task_ids = [task.task_id for task in self.tasks if task.status == TaskStatus.done]

        current_task = self.get_task(self.current_task_id) if self.current_task_id else None
        if current_task is None or current_task.status != TaskStatus.running:
            self.current_task_id = None

    @staticmethod
    def _dependencies_satisfied(task: TurnTask, completed_ids: set[str]) -> bool:
        return all(depends_on_id in completed_ids for depends_on_id in task.depends_on)
