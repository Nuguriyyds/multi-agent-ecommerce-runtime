from __future__ import annotations

import logging

import pytest

from app.v3.hooks import HookBus
from app.v3.models import HookEvent, HookPoint, HookResult


def make_event() -> HookEvent:
    return HookEvent(
        hook_point=HookPoint.decision,
        session_id="session-1",
        trace_id="trace-1",
        turn_number=2,
        payload={"action": "call_tool"},
    )


@pytest.mark.asyncio
async def test_hook_bus_emits_handlers_in_registration_order() -> None:
    bus = HookBus()
    calls: list[str] = []

    async def first(event: HookEvent) -> HookResult:
        calls.append("first")
        return HookResult(handler_name="first", metadata={"point": event.hook_point.value})

    async def second(event: HookEvent) -> HookResult:
        calls.append("second")
        return HookResult(handler_name="second", metadata={"trace_id": event.trace_id})

    async def third(event: HookEvent) -> HookResult:
        calls.append("third")
        return HookResult(handler_name="third", metadata={"turn_number": event.turn_number})

    bus.register(HookPoint.decision, first)
    bus.register(HookPoint.decision, second)
    bus.register(HookPoint.decision, third)

    results = await bus.emit(HookPoint.decision, make_event())

    assert calls == ["first", "second", "third"]
    assert [result.handler_name for result in results] == ["first", "second", "third"]
    assert all(result.accepted for result in results)


@pytest.mark.asyncio
async def test_hook_bus_isolates_handler_failures(caplog: pytest.LogCaptureFixture) -> None:
    bus = HookBus()
    calls: list[str] = []

    async def before(_: HookEvent) -> HookResult:
        calls.append("before")
        return HookResult(handler_name="before")

    async def broken(_: HookEvent) -> HookResult:
        calls.append("broken")
        raise RuntimeError("hook exploded")

    async def after(_: HookEvent) -> HookResult:
        calls.append("after")
        return HookResult(handler_name="after")

    bus.register(HookPoint.decision, before)
    bus.register(HookPoint.decision, broken)
    bus.register(HookPoint.decision, after)

    with caplog.at_level(logging.ERROR):
        results = await bus.emit(HookPoint.decision, make_event())

    assert calls == ["before", "broken", "after"]
    assert [result.handler_name for result in results] == ["before", "broken", "after"]
    assert results[1].accepted is False
    assert results[1].note == "hook exploded"
    assert any("Hook handler broken failed at decision" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_hook_bus_warns_and_discards_event_mutations(caplog: pytest.LogCaptureFixture) -> None:
    bus = HookBus()
    observed_actions: list[str] = []
    observed_sessions: list[str | None] = []

    async def mutator(event: HookEvent) -> HookResult:
        event.payload["action"] = "fallback"
        event.session_id = "rewritten-session"
        return HookResult(handler_name="mutator", metadata={"mutated": True})

    async def observer(event: HookEvent) -> HookResult:
        observed_actions.append(event.payload["action"])
        observed_sessions.append(event.session_id)
        return HookResult(handler_name="observer")

    bus.register(HookPoint.decision, mutator)
    bus.register(HookPoint.decision, observer)

    original_event = make_event()
    with caplog.at_level(logging.WARNING):
        await bus.emit(HookPoint.decision, original_event)

    assert original_event.payload["action"] == "call_tool"
    assert original_event.session_id == "session-1"
    assert observed_actions == ["call_tool"]
    assert observed_sessions == ["session-1"]
    assert any("mutated HookEvent for decision" in record.message for record in caplog.records)
