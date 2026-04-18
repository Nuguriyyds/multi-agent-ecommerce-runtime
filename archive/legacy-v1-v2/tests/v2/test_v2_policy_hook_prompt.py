from __future__ import annotations

from typing import Any

import pytest

from app.v2 import (
    HookBus,
    HookResult,
    PolicyGate,
    PolicyInput,
    PromptRegistry,
    PromptTemplate,
    ToolRegistry,
    ToolSpec,
    Worker,
    WorkerResult,
    WorkerTask,
    build_default_prompt_registry,
)


class HookProbeWorker(Worker):
    async def execute(self, task: WorkerTask, context: Any) -> WorkerResult:
        if task.intent == "explode":
            raise RuntimeError("worker boom")

        return WorkerResult(
            worker_name=self.name,
            payload={
                "task_id": task.task_id,
                "session_id": context.session_id,
            },
        )


def test_policy_gate_clarifies_when_scene_context_is_missing():
    gate = PolicyGate()

    decision = gate.evaluate(
        PolicyInput(
            scene="product_page",
            scene_context={},
        ),
    )

    assert decision.decision == "clarify"
    assert decision.code == "missing_scene_context"
    assert decision.missing_fields == ("product_id",)


def test_policy_gate_rejects_unsupported_actions():
    gate = PolicyGate()

    illegal_tool = gate.evaluate(
        PolicyInput(
            scene="default",
            requested_tool="inventory.write",
            allowed_tools=("catalog.search", "copy.generate"),
        ),
    )
    unsupported_scene = gate.evaluate(
        PolicyInput(
            scene="checkout",
            scene_context={},
        ),
    )

    assert illegal_tool.decision == "reject"
    assert illegal_tool.code == "illegal_tool"
    assert unsupported_scene.decision == "reject"
    assert unsupported_scene.code == "unsupported_scene"


def test_policy_gate_allows_supported_requests_with_required_inputs():
    gate = PolicyGate()

    decision = gate.evaluate(
        PolicyInput(
            scene="cart",
            scene_context={"product_ids": ["p_1", "p_2"]},
            required_fields=("budget", "product_category"),
            available_fields={
                "budget": "3000",
                "product_category": "phone",
            },
        ),
    )

    assert decision.decision == "allow"
    assert decision.code == "allowed"
    assert decision.missing_fields == ()


@pytest.mark.asyncio
async def test_hook_bus_emits_registered_listeners_with_snapshot_copies():
    bus = HookBus()

    def audit(snapshot: dict[str, Any]) -> dict[str, Any]:
        snapshot["turn"]["message"] = "mutated"
        return {
            "updates": {"audit": True},
            "metadata": {"phase": "start"},
        }

    async def metrics(snapshot: dict[str, Any]) -> HookResult:
        return HookResult(
            hook_name="turn.started",
            listener_name="metrics",
            updates={"message_seen": snapshot["turn"]["message"]},
        )

    bus.register("turn.started", "audit", audit)
    bus.register("turn.started", "metrics", metrics)

    source = {"turn": {"message": "预算 3000 买手机"}}
    results = await bus.emit("turn.started", source)

    assert source == {"turn": {"message": "预算 3000 买手机"}}
    assert "background_task.failed" in bus.list_hooks()
    assert bus.list_listeners("turn.started") == ("audit", "metrics")
    assert [result.listener_name for result in results] == ["audit", "metrics"]
    assert results[0].updates["audit"] is True
    assert results[1].updates["message_seen"] == "预算 3000 买手机"


@pytest.mark.asyncio
async def test_tool_registry_emits_tool_hooks_for_success_and_error_paths():
    bus = HookBus()
    observed: list[tuple[str, str]] = []

    def capture(label: str):
        def _capture(snapshot: dict[str, Any]) -> dict[str, Any]:
            observed.append((label, snapshot["tool_name"]))
            return {"metadata": {"label": label}}

        return _capture

    bus.register("tool.before", "capture.before", capture("tool.before"))
    bus.register("tool.after", "capture.after", capture("tool.after"))
    bus.register("tool.error", "capture.error", capture("tool.error"))

    registry = ToolRegistry(hook_bus=bus)
    registry.register(
        ToolSpec(
            name="session.read_memory",
            description="read session memory",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level="none",
        ),
        lambda payload: {"session_id": payload["session_id"]},
    )
    registry.register(
        ToolSpec(
            name="profile.write",
            description="persist profile",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level="persistent",
        ),
        lambda payload: (_ for _ in ()).throw(RuntimeError("tool boom")),
    )

    assert await registry.invoke(
        "session.read_memory",
        {"session_id": "sess_1"},
        hook_context={"manager_name": "shopping"},
    ) == {"session_id": "sess_1"}

    with pytest.raises(RuntimeError, match="tool boom"):
        await registry.invoke(
            "profile.write",
            {"user_id": "u_1"},
            hook_context={"manager_name": "shopping"},
        )

    assert observed == [
        ("tool.before", "session.read_memory"),
        ("tool.after", "session.read_memory"),
        ("tool.before", "profile.write"),
        ("tool.error", "profile.write"),
    ]


@pytest.mark.asyncio
async def test_worker_run_emits_worker_hooks_for_success_and_failure_paths():
    bus = HookBus()
    observed: list[tuple[str, str]] = []

    def capture(label: str):
        def _capture(snapshot: dict[str, Any]) -> dict[str, Any]:
            observed.append((label, snapshot["task"]["task_id"]))
            return {"updates": {"label": label}}

        return _capture

    bus.register("worker.started", "capture.started", capture("worker.started"))
    bus.register("worker.finished", "capture.finished", capture("worker.finished"))
    bus.register("worker.failed", "capture.failed", capture("worker.failed"))

    worker = HookProbeWorker("catalog_worker")
    registry = ToolRegistry()

    result = await worker.run(
        WorkerTask(
            task_id="task_ok",
            worker_name="catalog_worker",
            step=1,
            intent="lookup",
        ),
        registry,
        manager_name="shopping",
        session_id="sess_1",
        hook_bus=bus,
    )

    assert result.payload["task_id"] == "task_ok"

    with pytest.raises(RuntimeError, match="worker boom"):
        await worker.run(
            WorkerTask(
                task_id="task_fail",
                worker_name="catalog_worker",
                step=1,
                intent="explode",
            ),
            registry,
            manager_name="shopping",
            session_id="sess_1",
            hook_bus=bus,
        )

    assert observed == [
        ("worker.started", "task_ok"),
        ("worker.finished", "task_ok"),
        ("worker.started", "task_fail"),
        ("worker.failed", "task_fail"),
    ]


def test_prompt_registry_supports_versioned_lookup_and_rendering():
    registry = PromptRegistry()
    registry.register(
        PromptTemplate(
            name="copy.generate",
            version="v1",
            template="为 {product_name} 生成一句文案",
            variables_schema={
                "type": "object",
                "required": ["product_name"],
            },
        ),
    )
    registry.register(
        PromptTemplate(
            name="copy.generate",
            version="v2",
            template="面向 {audience}，为 {product_name} 生成一句文案",
            variables_schema={
                "type": "object",
                "required": ["product_name", "audience"],
            },
        ),
    )

    assert registry.get("copy.generate", version="v1").template == "为 {product_name} 生成一句文案"
    assert registry.get("copy.generate").version == "v2"
    assert registry.render(
        "copy.generate",
        version="v2",
        variables={"product_name": "Phone X", "audience": "手游用户"},
    ) == "面向 手游用户，为 Phone X 生成一句文案"

    with pytest.raises(ValueError, match="missing required prompt variables"):
        registry.render(
            "copy.generate",
            version="v1",
            variables={},
        )


def test_default_prompt_registry_seeds_required_v2_prompts():
    registry = build_default_prompt_registry()

    assert registry.list_names() == (
        "comparison.summarize",
        "copy.generate",
        "preference.extract",
        "shopping.manager.clarify",
        "shopping.manager.plan",
        "shopping.manager.respond",
    )
    assert registry.get("shopping.manager.plan", version="v1").template
    assert "{missing_fields}" in registry.get("shopping.manager.clarify", version="v1").template
