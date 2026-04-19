from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from app.v3.models import TraceRecord
from app.v3.models.base import V3Model

FeedbackSignal = Literal["interested", "not_interested", "clicked", "ignored"]

_POSITIVE_SIGNALS: set[str] = {"interested", "clicked"}
_NEGATIVE_SIGNALS: set[str] = {"not_interested", "ignored"}
_RECENT_TURN_LIMIT = 5


class RuntimeTurnMetric(V3Model):
    trace_id: str
    turn_number: int
    status: str | None = None
    latency_ms: int = 0
    decision_count: int = 0
    invocation_count: int = 0
    observation_count: int = 0
    guardrail_hit_count: int = 0
    fallback_reason: str | None = None
    capability_counts: dict[str, int] = Field(default_factory=dict)


class RecommendationFeedbackEvent(V3Model):
    sku: str
    signal: FeedbackSignal
    source: str = "api"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuntimeMetricsSnapshot(V3Model):
    turn_count: int = 0
    avg_turn_latency_ms: int = 0
    total_decisions: int = 0
    total_invocations: int = 0
    total_observations: int = 0
    fallback_count: int = 0
    guardrail_hit_count: int = 0
    capability_counts: dict[str, int] = Field(default_factory=dict)


class FeedbackMetricsSnapshot(V3Model):
    total_events: int = 0
    positive_events: int = 0
    negative_events: int = 0
    interest_rate: float = 0.0
    sku_scores: dict[str, int] = Field(default_factory=dict)


class ObservabilitySnapshot(V3Model):
    session_id: str
    runtime: RuntimeMetricsSnapshot = Field(default_factory=RuntimeMetricsSnapshot)
    feedback: FeedbackMetricsSnapshot = Field(default_factory=FeedbackMetricsSnapshot)
    recent_turns: list[RuntimeTurnMetric] = Field(default_factory=list)


class ObservabilityStore:
    def __init__(self) -> None:
        self._turns: dict[str, list[RuntimeTurnMetric]] = {}
        self._feedback_events: dict[str, list[RecommendationFeedbackEvent]] = {}

    def record_turn(
        self,
        session_id: str,
        trace: TraceRecord,
        *,
        latency_ms: int,
    ) -> RuntimeTurnMetric:
        metric = RuntimeTurnMetric(
            trace_id=trace.trace_id,
            turn_number=trace.turn_number,
            status=trace.terminal_state,
            latency_ms=max(0, latency_ms),
            decision_count=len(trace.decisions),
            invocation_count=len(trace.invocations),
            observation_count=len(trace.observations),
            guardrail_hit_count=len(trace.guardrail_hits),
            fallback_reason=trace.fallback_reason,
            capability_counts=dict(Counter(invocation.capability_name for invocation in trace.invocations)),
        )

        existing = [
            item
            for item in self._turns.get(session_id, [])
            if item.turn_number != metric.turn_number
        ]
        existing.append(metric)
        existing.sort(key=lambda item: item.turn_number)
        self._turns[session_id] = existing
        return metric.model_copy(deep=True)

    def record_feedback(
        self,
        session_id: str,
        *,
        sku: str,
        signal: FeedbackSignal,
        source: str = "api",
    ) -> RecommendationFeedbackEvent:
        event = RecommendationFeedbackEvent(
            sku=sku.strip(),
            signal=signal,
            source=source.strip() or "api",
        )
        self._feedback_events.setdefault(session_id, []).append(event)
        return event.model_copy(deep=True)

    def snapshot(self, session_id: str) -> ObservabilitySnapshot:
        turns = [turn.model_copy(deep=True) for turn in self._turns.get(session_id, [])]
        feedback_events = [
            event.model_copy(deep=True)
            for event in self._feedback_events.get(session_id, [])
        ]
        return ObservabilitySnapshot(
            session_id=session_id,
            runtime=self._build_runtime_snapshot(turns),
            feedback=self._build_feedback_snapshot(feedback_events),
            recent_turns=turns[-_RECENT_TURN_LIMIT:],
        )

    @staticmethod
    def _build_runtime_snapshot(turns: list[RuntimeTurnMetric]) -> RuntimeMetricsSnapshot:
        capability_counts: Counter[str] = Counter()
        for turn in turns:
            capability_counts.update(turn.capability_counts)

        latency_values = [turn.latency_ms for turn in turns]
        avg_latency = (
            int(round(sum(latency_values) / len(latency_values)))
            if latency_values
            else 0
        )
        return RuntimeMetricsSnapshot(
            turn_count=len(turns),
            avg_turn_latency_ms=avg_latency,
            total_decisions=sum(turn.decision_count for turn in turns),
            total_invocations=sum(turn.invocation_count for turn in turns),
            total_observations=sum(turn.observation_count for turn in turns),
            fallback_count=sum(
                1
                for turn in turns
                if turn.status == "fallback" or bool(turn.fallback_reason)
            ),
            guardrail_hit_count=sum(turn.guardrail_hit_count for turn in turns),
            capability_counts=dict(sorted(capability_counts.items())),
        )

    @staticmethod
    def _build_feedback_snapshot(
        feedback_events: list[RecommendationFeedbackEvent],
    ) -> FeedbackMetricsSnapshot:
        sku_scores: Counter[str] = Counter()
        positive_events = 0
        negative_events = 0
        for event in feedback_events:
            if event.signal in _POSITIVE_SIGNALS:
                positive_events += 1
                sku_scores[event.sku] += 1
            elif event.signal in _NEGATIVE_SIGNALS:
                negative_events += 1
                sku_scores[event.sku] -= 1

        total_events = len(feedback_events)
        interest_rate = round(positive_events / total_events, 3) if total_events else 0.0
        return FeedbackMetricsSnapshot(
            total_events=total_events,
            positive_events=positive_events,
            negative_events=negative_events,
            interest_rate=interest_rate,
            sku_scores=dict(sorted(sku_scores.items())),
        )


__all__ = [
    "FeedbackMetricsSnapshot",
    "FeedbackSignal",
    "ObservabilitySnapshot",
    "ObservabilityStore",
    "RecommendationFeedbackEvent",
    "RuntimeMetricsSnapshot",
    "RuntimeTurnMetric",
]
