from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from threading import Lock

from app.shared.models.domain import AgentMetricSnapshot, MetricsResponse
from app.v1.models.agent_result import AgentResult


@dataclass
class _AgentMetricState:
    calls: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0


class MetricsCollector:
    def __init__(self) -> None:
        self._lock = Lock()
        self._agent_metrics: dict[str, _AgentMetricState] = {}

    def record_agent_result(self, result: AgentResult) -> None:
        if not result.agent_name:
            return

        with self._lock:
            state = self._agent_metrics.setdefault(result.agent_name, _AgentMetricState())
            state.calls += 1
            state.total_latency_ms += result.latency_ms
            if not result.success or result.degraded:
                state.failures += 1

    def snapshot(self) -> MetricsResponse:
        with self._lock:
            agents = {
                agent_name: AgentMetricSnapshot(
                    calls=state.calls,
                    avg_latency_ms=round(state.total_latency_ms / state.calls, 2)
                    if state.calls
                    else 0.0,
                    error_rate=round(state.failures / state.calls, 4)
                    if state.calls
                    else 0.0,
                )
                for agent_name, state in sorted(self._agent_metrics.items())
            }

        return MetricsResponse(agents=agents)

    def reset(self) -> None:
        with self._lock:
            self._agent_metrics.clear()


@lru_cache
def get_metrics_collector() -> MetricsCollector:
    return MetricsCollector()
