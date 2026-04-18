from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.shared.models.domain import MarketingCopy, Product
from app.v2.core.models import (
    ChatTurn,
    Event,
    FeedbackEvent,
    ManagerTurnContext,
    PreferenceSignal,
    RecommendationSnapshot,
    SessionState,
    TaskRecord,
    ToolSpec,
    UserProfile,
    WorkerResult,
    WorkerTask,
)
from app.v2.core.runtime import Manager, ManagerRegistry, ToolRegistry, Worker, WorkerExecutionContext, WorkerRegistry


class DummyManager(Manager):
    async def handle_turn(self, context: ManagerTurnContext, message: str) -> dict[str, str]:
        return {"reply": f"{context.user_id}:{message}"}


class EchoWorker(Worker):
    async def execute(self, task: WorkerTask, context: WorkerExecutionContext) -> WorkerResult:
        payload = await context.call_tool(
            "session.read_memory",
            {"session_id": context.session_id},
        )
        return WorkerResult(
            worker_name=self.name,
            payload={
                "tool_payload": payload,
                "allowed_tools": list(context.allowed_tools),
            },
        )


class IsolationWorker(Worker):
    async def execute(self, task: WorkerTask, context: WorkerExecutionContext) -> WorkerResult:
        with pytest.raises(AttributeError):
            _ = context.worker_registry

        with pytest.raises(PermissionError):
            await context.call_tool("profile.read", {"user_id": "u_1"})

        payload = await context.call_tool(
            "session.read_memory",
            {"session_id": context.session_id},
        )
        return WorkerResult(
            worker_name=self.name,
            payload={"isolated": True, "tool_payload": payload},
        )


def test_v2_core_models_cover_runtime_contract():
    now = datetime.now(timezone.utc)
    session_state = SessionState(
        session_id="sess_1",
        user_id="u_1",
        memory={"budget": "3000"},
        created_at=now,
        updated_at=now,
    )
    profile = UserProfile(
        user_id="u_1",
        preferred_categories=["phone"],
        preferred_brands=["Acme"],
        use_cases=["gaming"],
        excluded_terms=["refurbished"],
        price_range=(2000.0, 3000.0),
        segments=["price_sensitive"],
        tags=["chat_collected"],
    )
    signal = PreferenceSignal(
        category="budget",
        value="3000",
        confidence=0.95,
        source_turn=2,
    )
    task = WorkerTask(
        task_id="task_1",
        worker_name="preference_worker",
        step=1,
        intent="extract_preferences",
        input={"message": "预算 3000 买手机"},
    )
    result = WorkerResult(
        worker_name="preference_worker",
        payload={"budget": "3000"},
        signals=[signal],
    )
    record = TaskRecord(
        task_id="task_1",
        task_scope="conversation",
        session_id="sess_1",
        manager_name="shopping",
        worker_name="preference_worker",
        step=1,
        status="completed",
        input=task.model_dump(mode="json"),
        output=result.model_dump(mode="json"),
    )
    snapshot = RecommendationSnapshot(
        snapshot_id="snap_1",
        user_id="u_1",
        scene="homepage",
        products=[
            Product(
                product_id="p_1",
                name="Phone X",
                category="phone",
                price=2999,
            )
        ],
        copies=[MarketingCopy(product_id="p_1", copy_text="适合预算 3000 的游戏手机")],
        generated_at=now,
    )
    event = Event(
        event_id="evt_1",
        event_type="recommendation_refresh",
        user_id="u_1",
        payload={"scene": "homepage"},
        created_at=now,
    )
    feedback = FeedbackEvent(
        event_id="fb_1",
        user_id="u_1",
        event_type="click",
        scene="homepage",
        product_id="p_1",
        created_at=now,
    )
    turn = ChatTurn(role="user", content="预算 3000 买手机", turn_number=1, timestamp=now)
    context = ManagerTurnContext(
        session_id="sess_1",
        user_id="u_1",
        session_state=session_state,
        user_profile=profile,
    )
    tool = ToolSpec(
        name="session.read_memory",
        description="read session memory",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effect_level="none",
    )

    assert context.user_profile == profile
    assert record.output is not None
    assert snapshot.products[0].product_id == "p_1"
    assert event.status == "pending"
    assert feedback.event_type == "click"
    assert turn.role == "user"
    assert tool.name == "session.read_memory"


def test_manager_and_worker_registries_support_registration_and_lookup():
    manager_registry = ManagerRegistry()
    worker_registry = WorkerRegistry()

    manager = DummyManager("shopping")
    worker = EchoWorker("preference_worker", allowed_tools={"session.read_memory"})

    assert manager_registry.register(manager) is manager
    assert worker_registry.register(worker) is worker
    assert manager_registry.get("shopping") is manager
    assert worker_registry.get("preference_worker") is worker
    assert manager_registry.list_names() == ("shopping",)
    assert worker_registry.list_names() == ("preference_worker",)

    with pytest.raises(ValueError, match="already registered"):
        manager_registry.register(DummyManager("shopping"))

    with pytest.raises(ValueError, match="already registered"):
        worker_registry.register(EchoWorker("preference_worker"))


@pytest.mark.asyncio
async def test_tool_registry_supports_sync_and_async_handlers():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="session.read_memory",
            description="read session memory",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level="none",
        ),
        lambda payload: {"memory": payload["session_id"]},
    )

    async def request_refresh(payload: dict[str, str]) -> dict[str, bool]:
        return {"accepted": payload["user_id"] == "u_1"}

    registry.register(
        ToolSpec(
            name="recommendation.request_refresh",
            description="enqueue refresh",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level="persistent",
        ),
        request_refresh,
    )

    assert await registry.invoke("session.read_memory", {"session_id": "sess_1"}) == {
        "memory": "sess_1"
    }
    assert await registry.invoke("recommendation.request_refresh", {"user_id": "u_1"}) == {
        "accepted": True
    }

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            ToolSpec(
                name="session.read_memory",
                description="duplicate",
                input_schema={},
                output_schema={},
                side_effect_level="none",
            ),
            lambda payload: payload,
        )


@pytest.mark.asyncio
async def test_worker_runtime_exposes_only_tools_and_blocks_direct_worker_communication():
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="session.read_memory",
            description="read session memory",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level="none",
        ),
        lambda payload: {"budget": "3000", "session_id": payload["session_id"]},
    )
    registry.register(
        ToolSpec(
            name="profile.read",
            description="read long-term profile",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect_level="persistent",
        ),
        lambda payload: payload,
    )

    worker = IsolationWorker("preference_worker", allowed_tools={"session.read_memory"})
    result = await worker.run(
        WorkerTask(
            task_id="task_1",
            worker_name="preference_worker",
            step=1,
            intent="probe_boundary",
        ),
        registry,
        manager_name="shopping",
        session_id="sess_1",
        turn_id="turn_1",
    )

    assert result.payload["isolated"] is True
    assert result.payload["tool_payload"]["budget"] == "3000"


@pytest.mark.asyncio
async def test_worker_rejects_task_assigned_to_another_worker():
    worker = EchoWorker("catalog_worker", allowed_tools={"session.read_memory"})

    with pytest.raises(ValueError, match="assigned to worker 'copy_worker'"):
        await worker.run(
            WorkerTask(
                task_id="task_1",
                worker_name="copy_worker",
                step=1,
                intent="mismatch",
            ),
            ToolRegistry(),
            manager_name="shopping",
        )
