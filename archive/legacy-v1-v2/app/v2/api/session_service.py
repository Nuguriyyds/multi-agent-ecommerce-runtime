from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.v2.api.schemas import (
    CreateSessionResponse,
    FeedbackEventRequest,
    FeedbackEventResponse,
    RecommendationReadRequest,
    RecommendationReadResponse,
    SessionMessageRequest,
    SessionMessageResponse,
    ShoppingManagerTurnResult,
    TurnTraceProjection,
    TurnTraceResponse,
    TurnTraceTask,
)
from app.v2.background.refresh import BackgroundRefreshProcessor
from app.v2.core.hooks import HookBus
from app.v2.core.models import ChatTurn, Event, ManagerTurnContext, SessionState, TaskRecord, ToolSpec, TurnPlan
from app.v2.core.persistence import (
    EventStore,
    RecommendationSnapshotStore,
    SQLiteDatabase,
    SessionStore,
    SessionTurnStore,
    TaskRecordStore,
    UserProfileStore,
)
from app.v2.core.policy import PolicyGate
from app.v2.core.prompts import build_default_prompt_registry
from app.v2.core.runtime import ManagerRegistry, ToolRegistry
from app.v2.core.tools import (
    build_feedback_record_handler,
    build_recommendation_request_refresh_handler,
)
from app.v2.managers.shopping import ShoppingManager
from app.v2.reads.recommendation import RecommendationReadService


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class V2SessionService:
    def __init__(self, database: SQLiteDatabase | str | Path | None = None) -> None:
        self.database = database if isinstance(database, SQLiteDatabase) else SQLiteDatabase(
            database or Path(".tmp") / "v2_runtime" / "v2.sqlite3",
        )
        self.database.initialize()

        self.hook_bus = HookBus()
        self.policy_gate = PolicyGate()
        self.prompt_registry = build_default_prompt_registry()

        self.sessions = SessionStore(self.database)
        self.turns = SessionTurnStore(self.database)
        self.tasks = TaskRecordStore(self.database)
        self.user_profiles = UserProfileStore(self.database)
        self.snapshots = RecommendationSnapshotStore(self.database)
        self.events = EventStore(self.database)
        self._service_tools = ToolRegistry(hook_bus=self.hook_bus)
        self._service_tools.register(
            ToolSpec(
                name="feedback.record_event",
                description="Persist a completed user feedback event",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                side_effect_level="persistent",
            ),
            build_feedback_record_handler(self.events),
        )
        self._service_tools.register(
            ToolSpec(
                name="recommendation.request_refresh",
                description="Enqueue a recommendation refresh event",
                input_schema={"type": "object"},
                output_schema={"type": "object"},
                side_effect_level="persistent",
            ),
            build_recommendation_request_refresh_handler(self.events),
        )

        self.managers = ManagerRegistry()
        self.shopping_manager = self.managers.register(
            ShoppingManager(
                policy_gate=self.policy_gate,
                prompt_registry=self.prompt_registry,
                hook_bus=self.hook_bus,
                snapshot_store=self.snapshots,
                session_store=self.sessions,
                user_profiles=self.user_profiles,
                events=self.events,
                tasks=self.tasks,
            ),
        )
        self.background_processor = BackgroundRefreshProcessor(
            events=self.events,
            user_profiles=self.user_profiles,
            snapshots=self.snapshots,
            tasks=self.tasks,
            hook_bus=self.hook_bus,
            prompt_registry=self.prompt_registry,
        )
        self.recommendation_reader = RecommendationReadService(
            snapshots=self.snapshots,
            user_profiles=self.user_profiles,
            events=self.events,
            hook_bus=self.hook_bus,
            prompt_registry=self.prompt_registry,
        )

    def create_session(self, user_id: str) -> CreateSessionResponse:
        now = utc_now()
        session = SessionState(
            session_id=f"sess_{uuid4().hex[:12]}",
            user_id=user_id,
            created_at=now,
            updated_at=now,
        )
        self.sessions.save(session)
        return CreateSessionResponse(
            session_id=session.session_id,
            manager_type=self.shopping_manager.name,
            created_at=session.created_at,
        )

    async def handle_message(
        self,
        session_id: str,
        request: SessionMessageRequest,
    ) -> SessionMessageResponse:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)

        user_profile = self.user_profiles.get(session.user_id)
        existing_turns = self.turns.list_records_for_session(session_id)
        raw_turn_number = len(existing_turns) + 1
        user_turn_number = sum(1 for turn in existing_turns if turn.role == "user") + 1
        user_turn_id = f"turn_{uuid4().hex[:12]}"
        user_turn = ChatTurn(
            role="user",
            content=request.message,
            turn_number=raw_turn_number,
            timestamp=utc_now(),
        )
        self.turns.save(turn_id=user_turn_id, session_id=session_id, turn=user_turn)

        context_session = session.model_copy(
            update={
                "memory": {
                    **session.memory,
                    "_next_user_turn": user_turn_number,
                },
            },
        )
        context = ManagerTurnContext(
            session_id=session_id,
            turn_id=user_turn_id,
            user_id=session.user_id,
            scene=request.scene,
            scene_context=request.scene_context,
            session_state=context_session,
            user_profile=user_profile,
        )

        task_id = f"conv_{user_turn_id}"
        created_at = utc_now()
        task_template = TaskRecord(
            task_id=task_id,
            task_scope="conversation",
            session_id=session_id,
            turn_id=user_turn_id,
            manager_name=self.shopping_manager.name,
            status="completed",
            input={
                "message": request.message,
                "scene": request.scene,
                "scene_context": request.scene_context,
            },
        )

        try:
            manager_result = ShoppingManagerTurnResult.model_validate(
                await self.shopping_manager.handle_turn(context, request.message),
            )
        except Exception as exc:
            failed = task_template.model_copy(update={"status": "failed", "error": str(exc)})
            self.tasks.save(failed, created_at=created_at, updated_at=utc_now())
            raise

        assistant_turn = ChatTurn(
            role="assistant",
            content=manager_result.clarification or manager_result.reply,
            turn_number=raw_turn_number + 1,
            timestamp=utc_now(),
        )
        self.turns.save(
            turn_id=f"turn_{uuid4().hex[:12]}",
            session_id=session_id,
            turn=assistant_turn,
        )

        updated_memory = self._merge_memory(
            manager_result.session_memory,
            request=request,
            manager_result=manager_result,
            user_turn_number=user_turn_number,
        )
        saved_session = session.model_copy(
            update={
                "memory": updated_memory,
                "updated_at": utc_now(),
            },
        )
        self.sessions.save(saved_session)

        completed = task_template.model_copy(
            update={
                "status": "completed",
                "input": {
                    "message": request.message,
                    "scene": request.scene,
                    "scene_context": request.scene_context,
                    "plan": (
                        manager_result.plan.model_dump(mode="json")
                        if manager_result.plan is not None
                        else None
                    ),
                },
                "output": {
                    "executed_steps": list(manager_result.executed_steps),
                    "skipped_steps": list(manager_result.skipped_steps),
                    "terminal_state": manager_result.agent_details.terminal_state,
                    "projection_event_id": manager_result.projection_event_id,
                    "projection_event_type": manager_result.projection_event_type,
                    "projection_trigger": manager_result.projection_trigger,
                    "reply_preview": (manager_result.clarification or manager_result.reply)[:160],
                },
                "latency_ms": manager_result.agent_details.latency_ms,
            },
        )
        self.tasks.save(completed, created_at=created_at, updated_at=utc_now())

        return SessionMessageResponse(
            session_id=session_id,
            reply=manager_result.reply,
            products=manager_result.products,
            comparisons=manager_result.comparisons,
            copies=manager_result.copies,
            clarification=manager_result.clarification,
            preferences_extracted=manager_result.preferences_extracted,
            recommendation_refresh_triggered=manager_result.recommendation_refresh_triggered,
            agent_details=manager_result.agent_details,
        )

    @staticmethod
    def _merge_memory(
        current_memory: dict[str, object],
        *,
        request: SessionMessageRequest,
        manager_result: ShoppingManagerTurnResult,
        user_turn_number: int,
    ) -> dict[str, object]:
        memory = dict(current_memory)

        memory["_user_turn_count"] = user_turn_number
        memory["last_scene"] = request.scene
        memory["last_scene_context"] = request.scene_context
        memory["last_user_message"] = request.message
        memory["last_terminal_state"] = manager_result.agent_details.terminal_state
        memory["last_assistant_message"] = manager_result.clarification or manager_result.reply
        memory.pop("_next_user_turn", None)
        return memory

    async def process_background_events(self, *, limit: int = 100) -> tuple[Event, ...]:
        return await self.background_processor.process_pending_events(limit=limit)

    async def read_recommendations(
        self,
        user_id: str,
        request: RecommendationReadRequest,
    ) -> RecommendationReadResponse:
        return await self.recommendation_reader.read_recommendations(user_id, request)

    async def record_feedback_event(
        self,
        user_id: str,
        request: FeedbackEventRequest,
    ) -> FeedbackEventResponse:
        feedback_result = await self._service_tools.invoke(
            "feedback.record_event",
            {
                "user_id": user_id,
                "event_type": request.event_type,
                "scene": request.scene,
                "product_id": request.product_id,
                "product_ids": list(request.product_ids),
                "metadata": dict(request.metadata),
            },
        )
        await self._service_tools.invoke(
            "recommendation.request_refresh",
            {
                "user_id": user_id,
                "scene": "homepage",
                "trigger": "feedback_event",
                "preferences": {},
                "changed_categories": [],
                "stable_categories": [],
                "conflict_categories": [],
            },
        )
        return FeedbackEventResponse(event_id=str(feedback_result["event_id"]))

    def get_turn_trace(self, session_id: str, user_turn_number: int) -> TurnTraceResponse:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)

        turn_id = self._resolve_user_turn_id(session_id, user_turn_number)
        tasks = self.tasks.list_for_turn(turn_id)
        parent_task = next(
            (
                task
                for task in tasks
                if task.worker_name is None and task.tool_name is None
            ),
            None,
        )
        if parent_task is None:
            raise KeyError(f"trace_not_found:{turn_id}")

        plan = TurnPlan.model_validate(parent_task.input.get("plan") or {"terminal_state": "fallback_used"})
        projection_event_id = None
        projection_event_type = None
        terminal_state = "fallback_used"
        projection_trigger = None
        if parent_task.output is not None:
            projection_event_id = parent_task.output.get("projection_event_id")
            projection_event_type = parent_task.output.get("projection_event_type")
            projection_trigger = parent_task.output.get("projection_trigger")
            terminal_state = str(parent_task.output.get("terminal_state", terminal_state))

        for task in tasks:
            if task.tool_name not in {"profile.request_projection", "recommendation.request_refresh"}:
                continue
            if projection_trigger is None and task.output is not None:
                projection_trigger = task.output.get("trigger")
            if projection_trigger is None:
                projection_trigger = task.input.get("trigger")
            if projection_event_type is None and task.output is not None:
                projection_event_type = task.output.get("event_type")
            if projection_event_type is None and task.tool_name == "profile.request_projection":
                projection_event_type = "profile_projection"
            if projection_event_type is None and task.tool_name == "recommendation.request_refresh":
                projection_event_type = "recommendation_refresh"
            if projection_event_id is None and task.output is not None:
                projection_event_id = task.output.get("event_id")

        return TurnTraceResponse(
            session_id=session_id,
            turn_id=turn_id,
            user_turn_number=user_turn_number,
            terminal_state=terminal_state,
            plan=plan,
            tasks=[
                TurnTraceTask(
                    task_id=task.task_id,
                    record_type=self._task_record_type(task.worker_name, task.tool_name),
                    step=task.step,
                    worker_name=task.worker_name,
                    tool_name=task.tool_name,
                    status=task.status,
                    input=dict(task.input),
                    output=task.output,
                    error=task.error,
                    latency_ms=task.latency_ms,
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                )
                for task in tasks
            ],
            projection=TurnTraceProjection(
                requested=projection_event_id is not None or projection_trigger is not None,
                event_type=projection_event_type,
                event_id=projection_event_id,
                trigger=projection_trigger,
            ),
        )

    def _resolve_user_turn_id(self, session_id: str, user_turn_number: int) -> str:
        if user_turn_number < 1:
            raise KeyError(f"invalid_turn:{user_turn_number}")
        current_user_turn = 0
        for turn in self.turns.list_records_for_session(session_id):
            if turn.role != "user":
                continue
            current_user_turn += 1
            if current_user_turn == user_turn_number:
                return turn.turn_id
        raise KeyError(f"unknown_turn:{user_turn_number}")

    @staticmethod
    def _task_record_type(worker_name: str | None, tool_name: str | None) -> str:
        if tool_name is not None:
            return "tool"
        if worker_name is not None:
            return "worker"
        return "conversation"


__all__ = [
    "V2SessionService",
    "utc_now",
]
