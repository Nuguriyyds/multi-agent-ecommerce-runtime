from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from .base import V3Model


class CapabilityKind(str, Enum):
    tool = "tool"
    sub_agent = "sub_agent"
    mcp_tool = "mcp_tool"


class CapabilityDescriptor(V3Model):
    name: str
    kind: CapabilityKind
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout: float | None = None
    permission_tag: str | None = None
    description: str | None = None


class PluginCapability(V3Model):
    name: str
    kind: CapabilityKind
    permission_tag: str | None = None
    description: str | None = None
