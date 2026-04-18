from __future__ import annotations

from enum import Enum


class PromptLayer(str, Enum):
    platform = "platform"
    scenario = "scenario"
    role = "role"
    task_brief = "task_brief"
