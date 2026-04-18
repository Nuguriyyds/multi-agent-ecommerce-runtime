from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from app.shared.config.settings import get_settings
from app.shared.observability.logging_utils import configure_logging
from app.v1.models.agent_result import AgentResult
from app.v1.services.metrics import get_metrics_collector

configure_logging()
logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Agent 基类 — 提供执行循环、重试（指数退避）、超时控制、降级返回默认结果。"""

    def __init__(
        self,
        name: str,
        *,
        timeout: float | None = None,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
    ) -> None:
        self.name = name
        settings = get_settings()
        self.timeout = timeout if timeout is not None else settings.agent_timeout
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay

    # ------------------------------------------------------------------
    # 子类必须实现
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute(self, input_data: dict) -> dict:
        """执行具体业务逻辑，返回业务数据 dict。"""

    @abstractmethod
    def default_result(self, input_data: dict) -> dict:
        """降级时返回的默认结果。"""

    # ------------------------------------------------------------------
    # 执行入口
    # ------------------------------------------------------------------

    async def run(self, input_data: dict) -> AgentResult:
        """带重试、超时、降级的执行循环。

        流程:
        1. 尝试在 *timeout* 秒内执行 execute()
        2. 如果超时 → 立即降级，返回 default_result（不再重试）
        3. 如果抛出其他异常 → 指数退避后重试，直到 max_retries 耗尽
        4. 重试全部失败 → 降级返回 default_result
        """
        start = time.perf_counter()
        last_error = ""
        logger.info(
            "agent_started",
            extra={
                "agent": self.name,
                "timeout_s": self.timeout,
                "max_retries": self.max_retries,
            },
        )

        for attempt in range(1, self.max_retries + 2):  # 1-indexed, includes initial + retries
            try:
                data = await asyncio.wait_for(
                    self.execute(input_data),
                    timeout=self.timeout,
                )
                elapsed = (time.perf_counter() - start) * 1000
                result = AgentResult(
                    success=True,
                    data=data,
                    agent_name=self.name,
                    attempts=attempt,
                    latency_ms=round(elapsed, 2),
                )
                logger.info(
                    "agent_completed",
                    extra={
                        "agent": self.name,
                        "attempt": attempt,
                        "latency_ms": result.latency_ms,
                        "success": True,
                        "degraded": False,
                    },
                )
                return self._finalize_result(result)

            except asyncio.TimeoutError:
                elapsed = (time.perf_counter() - start) * 1000
                last_error = f"Timeout after {self.timeout}s"
                result = AgentResult(
                    success=False,
                    data=self.default_result(input_data),
                    degraded=True,
                    agent_name=self.name,
                    attempts=attempt,
                    error=last_error,
                    latency_ms=round(elapsed, 2),
                )
                logger.warning(
                    "agent_timed_out",
                    extra={
                        "agent": self.name,
                        "attempt": attempt,
                        "latency_ms": result.latency_ms,
                        "error": last_error,
                        "degraded": True,
                    },
                )
                # 超时直接降级，不再重试
                return self._finalize_result(result)

            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "agent_attempt_failed",
                    extra={
                        "agent": self.name,
                        "attempt": attempt,
                        "error": last_error,
                    },
                )
                # 还有重试机会 → 指数退避
                if attempt <= self.max_retries:
                    delay = self.retry_base_delay * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)

        # 重试全部耗尽 → 降级
        elapsed = (time.perf_counter() - start) * 1000
        result = AgentResult(
            success=False,
            data=self.default_result(input_data),
            degraded=True,
            agent_name=self.name,
            attempts=self.max_retries + 1,
            error=last_error,
            latency_ms=round(elapsed, 2),
        )
        logger.error(
            "agent_degraded",
            extra={
                "agent": self.name,
                "attempts": result.attempts,
                "latency_ms": result.latency_ms,
                "error": last_error,
                "degraded": True,
            },
        )
        return self._finalize_result(result)

    def _finalize_result(self, result: AgentResult) -> AgentResult:
        get_metrics_collector().record_agent_result(result)
        return result
