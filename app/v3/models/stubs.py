from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from .base import V3Model
from .capability import PluginCapability


class BackgroundTaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class BackgroundTask(V3Model):
    task_id: str
    task_type: str
    status: BackgroundTaskStatus
    payload: dict[str, Any] = Field(default_factory=dict)


class SchedulePolicy(V3Model):
    name: str
    cadence: str
    enabled: bool = True


class SkillDefinition(V3Model):
    name: str
    steps: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    applicability_notes: list[str] = Field(default_factory=list)


class SkillExecutionContext(V3Model):
    skill_name: str
    session_id: str
    step_index: int = 0
    state: dict[str, Any] = Field(default_factory=dict)


class PluginManifest(V3Model):
    name: str
    version: str
    capabilities: list[PluginCapability] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
