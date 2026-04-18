from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import Field

from .base import V3Model
from .decision import Observation


class ErrorCategory(str, Enum):
    provider = "provider"
    llm = "llm"
    system = "system"


class RetryPolicy(V3Model):
    max_retries: int = 0
    backoff_seconds: float = 0.0
    retryable_categories: list[ErrorCategory] = Field(default_factory=list)


class LLMErrorObservation(Observation):
    source: Literal["llm"] = "llm"
    status: Literal["error"] = "error"
    error_category: ErrorCategory = ErrorCategory.llm
    retryable: bool = False
    raw_output: str | None = None
