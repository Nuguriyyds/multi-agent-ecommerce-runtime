from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from app.shared.data.inventory_store import InventoryStore
from app.shared.data.product_catalog import ProductCatalog
from app.shared.models.domain import MarketingCopy, Product
from app.v2.api.schemas import SessionProductComparison, SessionProductPreview, ShoppingAgentDetails, ShoppingManagerTurnResult
from app.v2.core.hooks import HookBus
from app.v2.core.models import ManagerTurnContext, PreferenceSignal, TaskRecord, ToolSpec, TurnPlan, TurnPlanStep, UserProfile, WorkerTask
from app.v2.core.persistence import EventStore, SessionStore, TaskRecordStore
from app.v2.core.policy import PolicyDecision, PolicyGate, PolicyInput
from app.v2.core.prompts import PromptRegistry, build_default_prompt_registry
from app.v2.core.runtime import Manager, ToolRegistry, ToolTaskPersistenceContext, WorkerRegistry
from app.v2.core.tools import build_feedback_summary_read_handler, build_profile_extract_preferences_handler, build_profile_request_projection_handler, build_session_read_memory_handler
from app.v2.managers.planning import ShoppingTurnPlanner
from app.v2.workers.catalog import CatalogWorker, build_catalog_search_handler
from app.v2.workers.comparison import ComparisonWorker, build_product_compare_handler
from app.v2.workers.copy import CopyWorker, build_copy_generate_handler
from app.v2.workers.inventory import InventoryWorker, build_inventory_check_handler
from app.v2.workers.preference import PreferenceUpdateOutcome, PreferenceWorker, apply_preference_signals, build_refresh_event_payload

_UNSUPPORTED_KEYWORDS: tuple[str, ...] = ("merchant", "admin", "refund", "fulfillment", "warehouse")


@dataclass(slots=True)
class TurnExecutionState:
    active_profile: UserProfile | None
    available_fields: dict[str, str]
    working_memory: dict[str, Any]
    extracted: list[PreferenceSignal] = field(default_factory=list)
    candidate_products: list[Product] = field(default_factory=list)
    available_products: list[Product] = field(default_factory=list)
    final_products: list[SessionProductPreview] = field(default_factory=list)
    comparisons: list[SessionProductComparison] = field(default_factory=list)
    copies: list[MarketingCopy] = field(default_factory=list)
    workers_called: list[str] = field(default_factory=list)
    executed_steps: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    tool_ordinals: dict[str, int] = field(default_factory=dict)
    preference_update: PreferenceUpdateOutcome | None = None
    projection_event_id: str | None = None
    projection_event_type: str | None = None
    projection_trigger: str | None = None


class ShoppingManager(Manager):
    def __init__(
        self,
        *,
        policy_gate: PolicyGate | None = None,
        prompt_registry: PromptRegistry | None = None,
        hook_bus: HookBus | None = None,
        product_catalog: ProductCatalog | None = None,
        inventory_store: InventoryStore | None = None,
        snapshot_store: object | None = None,
        session_store: SessionStore | None = None,
        user_profiles: object | None = None,
        events: EventStore | None = None,
        tasks: TaskRecordStore | None = None,
    ) -> None:
        super().__init__("shopping")
        self._policy_gate = policy_gate or PolicyGate()
        self._prompt_registry = prompt_registry or build_default_prompt_registry()
        self._hook_bus = hook_bus
        self._planner = ShoppingTurnPlanner()
        self._task_store = tasks
        self._workers = WorkerRegistry()
        self._preference_worker = self._workers.register(PreferenceWorker(prompt_registry=self._prompt_registry))
        self._catalog_worker = self._workers.register(CatalogWorker())
        self._inventory_worker = self._workers.register(InventoryWorker())
        self._comparison_worker = self._workers.register(ComparisonWorker(prompt_registry=self._prompt_registry))
        self._copy_worker = self._workers.register(CopyWorker())
        self._tool_registry = ToolRegistry(hook_bus=self._hook_bus)
        self._register_tools(
            product_catalog=product_catalog or ProductCatalog(),
            inventory_store=inventory_store or InventoryStore(),
            session_store=session_store,
            events=events,
        )

    async def handle_turn(self, context: ManagerTurnContext, message: str) -> dict[str, object]:
        started = perf_counter()
        if self._hook_bus is not None:
            await self._hook_bus.emit(
                "turn.started",
                {
                    "manager_name": self.name,
                    "session_id": context.session_id,
                    "user_id": context.user_id,
                    "turn": {"message": message, "scene": context.scene, "scene_context": context.scene_context},
                },
            )
        result = await self._handle_turn_impl(context, message, started)
        if self._hook_bus is not None:
            await self._hook_bus.emit(
                "turn.finished",
                {
                    "manager_name": self.name,
                    "session_id": context.session_id,
                    "user_id": context.user_id,
                    "result": result.model_dump(mode="json"),
                },
            )
        return result.model_dump(mode="json")

    async def _handle_turn_impl(self, context: ManagerTurnContext, message: str, started: float) -> ShoppingManagerTurnResult:
        scene = context.scene or "default"
        decision = self._build_policy_decision(scene=scene, scene_context=context.scene_context, message=message)
        plan = self._planner.build_plan(scene=scene, message=message, decision=decision)
        if plan.terminal_state == "needs_clarification":
            return self._build_clarification_result(decision=decision, scene=scene, started=started, plan=plan, session_memory=context.session_state.memory)
        if plan.terminal_state == "fallback_used":
            return self._build_fallback_result(decision=decision, started=started, plan=plan, session_memory=context.session_state.memory)

        signal_turn = int(context.session_state.memory.get("_next_user_turn", 1))
        state = TurnExecutionState(
            active_profile=context.user_profile,
            available_fields=dict(context.session_state.memory.get("preferences", {})),
            working_memory=dict(context.session_state.memory),
        )
        try:
            for step in plan.steps:
                await self._execute_plan_step(context=context, step=step, message=message, scene=scene, signal_turn=signal_turn, state=state)
        except Exception as exc:  # noqa: BLE001
            if self._is_runtime_boundary_error(exc):
                return self._build_runtime_fallback_result(started=started, plan=plan, session_memory=state.working_memory)
            raise

        reply = self._build_reply(plan=plan, scene=scene, signals=state.extracted, products=state.final_products, comparisons=state.comparisons)
        return ShoppingManagerTurnResult(
            reply=reply,
            products=state.final_products,
            comparisons=state.comparisons,
            copies=state.copies,
            clarification=None,
            preferences_extracted=state.extracted,
            recommendation_refresh_triggered=state.projection_event_id is not None,
            session_memory=state.working_memory,
            plan=plan,
            executed_steps=state.executed_steps,
            skipped_steps=state.skipped_steps,
            projection_event_id=state.projection_event_id,
            projection_event_type=state.projection_event_type,
            projection_trigger=state.projection_trigger,
            agent_details=ShoppingAgentDetails(
                steps_executed=len(state.workers_called),
                workers_called=state.workers_called,
                terminal_state="reply_ready",
                latency_ms=(perf_counter() - started) * 1000,
            ),
        )

    async def _execute_plan_step(self, *, context: ManagerTurnContext, step: TurnPlanStep, message: str, scene: str, signal_turn: int, state: TurnExecutionState) -> None:
        if step.name == "preference_worker":
            result = await self._run_worker_step(
                context=context,
                step=step,
                worker=self._preference_worker,
                intent="extract_preferences",
                payload={"message": message, "source_turn": signal_turn, "session_id": context.session_id},
            )
            state.workers_called.append(self._preference_worker.name)
            state.executed_steps.append(step.name)
            state.extracted = result.signals
            state.preference_update = apply_preference_signals(state.working_memory, signals=state.extracted, user_turn_number=signal_turn)
            state.working_memory = state.preference_update.memory
            state.available_fields = dict(state.working_memory.get("preferences", {}))
            return

        if step.name == "catalog_worker":
            result = await self._run_worker_step(
                context=context,
                step=step,
                worker=self._catalog_worker,
                intent="select_products",
                payload={
                    "scene": scene,
                    "scene_context": context.scene_context,
                    "user_id": context.user_id,
                    "preferences": state.available_fields,
                    "user_profile": state.active_profile.model_dump(mode="json") if state.active_profile is not None else None,
                    "allow_snapshot_read": False,
                    "limit": 3,
                },
            )
            state.workers_called.append(self._catalog_worker.name)
            state.executed_steps.append(step.name)
            state.candidate_products = self._coerce_products(result.payload.get("products"))
            return

        if step.name == "inventory_worker":
            result = await self._run_worker_step(
                context=context,
                step=step,
                worker=self._inventory_worker,
                intent="validate_inventory",
                payload={"products": [product.model_dump(mode="json") for product in state.candidate_products]},
            )
            state.workers_called.append(self._inventory_worker.name)
            state.executed_steps.append(step.name)
            state.available_products = self._coerce_products(result.payload.get("products"))
            state.final_products = self._build_product_previews(state.available_products)
            return

        if step.name == "comparison_worker":
            result = await self._run_worker_step(
                context=context,
                step=step,
                worker=self._comparison_worker,
                intent="compare_products",
                payload={
                    "message": message,
                    "scene": scene,
                    "preferences": state.available_fields,
                    "focus": self._build_comparison_focus(scene=scene, preferences=state.available_fields, message=message),
                    "products": [product.model_dump(mode="json") for product in state.available_products],
                },
            )
            state.workers_called.append(self._comparison_worker.name)
            state.executed_steps.append(step.name)
            state.comparisons = self._coerce_comparisons(result.payload.get("comparisons"))
            return

        if step.name == "copy_worker":
            if not state.available_products:
                state.skipped_steps.append(step.name)
                return
            result = await self._run_worker_step(
                context=context,
                step=step,
                worker=self._copy_worker,
                intent="generate_copy",
                payload={
                    "message": message,
                    "scene": scene,
                    "preferences": state.available_fields,
                    "user_profile": state.active_profile.model_dump(mode="json") if state.active_profile is not None else None,
                    "products": [product.model_dump(mode="json") for product in state.available_products],
                },
            )
            state.workers_called.append(self._copy_worker.name)
            state.executed_steps.append(step.name)
            state.copies = self._coerce_copies(result.payload.get("copies"))
            return

        if step.name == "profile.request_projection":
            if state.preference_update is None or state.preference_update.refresh_trigger is None:
                state.skipped_steps.append(step.name)
                return
            payload = await self._invoke_manager_tool(
                context=context,
                step=step,
                tool_name="profile.request_projection",
                payload={
                    "user_id": context.user_id,
                    "target_scene": "homepage",
                    **build_refresh_event_payload(
                        session_id=context.session_id,
                        user_turn_number=signal_turn,
                        preferences=state.available_fields,
                        update=state.preference_update,
                    ),
                },
                ordinals=state.tool_ordinals,
            )
            state.executed_steps.append(step.name)
            state.projection_event_id = _as_text(payload.get("event_id"))
            state.projection_event_type = _as_text(payload.get("event_type"))
            state.projection_trigger = _as_text(payload.get("trigger"))
            if state.projection_event_id is not None:
                state.working_memory["last_projection_event_id"] = state.projection_event_id
                state.working_memory["last_projection_trigger"] = state.projection_trigger
                state.working_memory["last_refresh_trigger"] = state.projection_trigger
            return

    def _build_policy_decision(self, *, scene: str, scene_context: dict[str, Any], message: str) -> PolicyDecision:
        if self._looks_unsupported(message):
            return self._policy_gate.evaluate(PolicyInput(scene=scene, scene_context=scene_context, capability="shopping conversation", capability_supported=False))
        return self._policy_gate.evaluate(PolicyInput(scene=scene, scene_context=scene_context))

    def _register_tools(self, *, product_catalog: ProductCatalog, inventory_store: InventoryStore, session_store: SessionStore | None, events: EventStore | None) -> None:
        self._tool_registry.register(ToolSpec(name="catalog.search_products", description="search", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"), build_catalog_search_handler(product_catalog))
        self._tool_registry.register(ToolSpec(name="inventory.check", description="inventory", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"), build_inventory_check_handler(inventory_store))
        self._tool_registry.register(ToolSpec(name="product.compare", description="compare", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"), build_product_compare_handler())
        self._tool_registry.register(ToolSpec(name="copy.generate", description="copy", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"), build_copy_generate_handler(prompt_registry=self._prompt_registry))
        self._tool_registry.register(ToolSpec(name="session.read_memory", description="memory", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"), build_session_read_memory_handler(session_store) if session_store is not None else lambda payload: {"found": False, "session_id": str(payload.get("session_id", "")), "memory": {}})
        self._tool_registry.register(ToolSpec(name="profile.extract_preferences", description="extract", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"), build_profile_extract_preferences_handler())
        self._tool_registry.register(ToolSpec(name="feedback.read_summary", description="feedback", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="none"), build_feedback_summary_read_handler(events, product_catalog) if events is not None else lambda payload: {"boosted_categories": [], "boosted_brands": [], "suppressed_product_ids": []})
        if events is not None:
            self._tool_registry.register(ToolSpec(name="profile.request_projection", description="projection", input_schema={"type": "object"}, output_schema={"type": "object"}, side_effect_level="persistent"), build_profile_request_projection_handler(events))

    async def _run_worker_step(self, *, context: ManagerTurnContext, step: TurnPlanStep, worker: Any, intent: str, payload: dict[str, Any]) -> Any:
        task = WorkerTask(task_id=f"worker_{context.turn_id}_{step.name}", worker_name=worker.name, step=step.step, intent=intent, input=payload)
        created_at = self._now()
        try:
            result = await worker.run(task, self._tool_registry, manager_name=self.name, session_id=context.session_id, turn_id=context.turn_id, hook_bus=self._hook_bus, task_store=self._task_store, task_scope="conversation", tool_step_key=step.name)
        except Exception as exc:
            if self._task_store is not None and context.turn_id:
                self._task_store.save(self._build_worker_task_record(context=context, task=task, status="failed", error=str(exc)), created_at=created_at, updated_at=self._now())
            raise
        if self._task_store is not None and context.turn_id:
            self._task_store.save(self._build_worker_task_record(context=context, task=task, status="completed", output=result.model_dump(mode="json"), latency_ms=result.latency_ms), created_at=created_at, updated_at=self._now())
        return result

    async def _invoke_manager_tool(self, *, context: ManagerTurnContext, step: TurnPlanStep, tool_name: str, payload: dict[str, Any], ordinals: dict[str, int]) -> Any:
        ordinals[step.name] = ordinals.get(step.name, 0) + 1
        task_persistence = None
        if self._task_store is not None and context.turn_id:
            task_persistence = ToolTaskPersistenceContext(task_store=self._task_store, task_id=f"tool_{context.turn_id}_{step.name}_{ordinals[step.name]}", task_scope="conversation", manager_name=self.name, session_id=context.session_id, turn_id=context.turn_id, step=step.step)
        return await self._tool_registry.invoke(tool_name, payload, hook_context={"manager_name": self.name, "session_id": context.session_id, "turn_id": context.turn_id, "step": step.step}, task_persistence=task_persistence)

    def _build_worker_task_record(self, *, context: ManagerTurnContext, task: WorkerTask, status: str, output: dict[str, Any] | None = None, error: str | None = None, latency_ms: float = 0.0) -> TaskRecord:
        return TaskRecord(task_id=task.task_id, task_scope="conversation", session_id=context.session_id, turn_id=context.turn_id, manager_name=self.name, worker_name=task.worker_name, step=task.step, status=status, input=dict(task.input), output=output, error=error, latency_ms=latency_ms)

    def _build_reply(self, *, plan: TurnPlan, scene: str, signals: list[PreferenceSignal], products: list[SessionProductPreview], comparisons: list[SessionProductComparison]) -> str:
        intent = plan.intent or "advisory"
        summary = ", ".join(f"{signal.category}={signal.value}" for signal in signals)
        if intent == "advisory":
            if summary:
                return f"I noted your preferences ({summary}). Ask for recommendation or comparison when you want concrete options."
            return "Tell me your budget, category, preferred brand, or use case, and I will narrow the choice down."
        if intent == "recommendation":
            if products:
                return f"Based on your preferences, here are a few options for {scene}: {self._product_names(products)}."
            return "I understood the request, but I do not have any in-stock options yet."
        if intent == "comparison":
            if comparisons and comparisons[0].summary:
                return comparisons[0].summary
            if products:
                return f"I compared the current options for {scene}: {self._product_names(products)}."
            return "I do not have enough candidate products to compare yet."
        return "I could not complete this turn safely."

    def _build_comparison_focus(self, *, scene: str, preferences: dict[str, str], message: str) -> str:
        use_case = str(preferences.get("use_case", "")).strip()
        if use_case:
            return use_case
        if scene == "product_page":
            return "product page"
        if scene == "cart":
            return "cart"
        if "worth" in message.casefold():
            return "worth buying"
        if "compare" in message.casefold():
            return "comparison"
        if str(preferences.get("budget", "")).strip():
            return "budget"
        return "general"

    def _build_product_previews(self, raw_products: object) -> list[SessionProductPreview]:
        previews: list[SessionProductPreview] = []
        for product in self._coerce_products(raw_products):
            previews.append(SessionProductPreview(product_id=product.product_id, name=product.name, price=product.price, category=product.category, brand=product.brand))
        return previews

    def _coerce_products(self, raw_products: object) -> list[Product]:
        if not isinstance(raw_products, list):
            return []
        products: list[Product] = []
        for item in raw_products:
            try:
                products.append(Product.model_validate(item))
            except ValidationError:
                continue
        return products

    def _coerce_comparisons(self, raw_comparisons: object) -> list[SessionProductComparison]:
        if not isinstance(raw_comparisons, list):
            return []
        comparisons: list[SessionProductComparison] = []
        for item in raw_comparisons:
            try:
                comparisons.append(SessionProductComparison.model_validate(item))
            except ValidationError:
                continue
        return comparisons

    def _coerce_copies(self, raw_copies: object) -> list[MarketingCopy]:
        if not isinstance(raw_copies, list):
            return []
        copies: list[MarketingCopy] = []
        for item in raw_copies:
            try:
                copies.append(MarketingCopy.model_validate(item))
            except ValidationError:
                continue
        return copies

    def _product_names(self, products: list[SessionProductPreview]) -> str:
        return ", ".join(product.name for product in products[:3]) if products else "[]"

    def _looks_unsupported(self, message: str) -> bool:
        lowered = message.casefold()
        return any(keyword in lowered for keyword in _UNSUPPORTED_KEYWORDS)

    def _build_clarification_result(self, *, decision: PolicyDecision, scene: str, started: float, plan: TurnPlan, session_memory: dict[str, Any]) -> ShoppingManagerTurnResult:
        clarification = f"Please provide {', '.join(decision.missing_fields)} for the {scene} scene."
        return ShoppingManagerTurnResult(reply="", products=[], comparisons=[], copies=[], clarification=clarification, preferences_extracted=[], recommendation_refresh_triggered=False, session_memory=dict(session_memory), plan=plan, executed_steps=["clarify"], skipped_steps=[], projection_event_id=None, projection_event_type=None, projection_trigger=None, agent_details=ShoppingAgentDetails(steps_executed=0, workers_called=[], terminal_state="needs_clarification", latency_ms=(perf_counter() - started) * 1000))

    def _build_fallback_result(self, *, decision: PolicyDecision, started: float, plan: TurnPlan, session_memory: dict[str, Any]) -> ShoppingManagerTurnResult:
        if decision.code == "unsupported_capability":
            reply = "This chat lane only supports shopping guidance, recommendation, and comparison."
        elif decision.code == "unsupported_scene":
            reply = "That scene is not supported in the current V2 runtime."
        else:
            reply = "I could not route this request safely."
        return ShoppingManagerTurnResult(reply=reply, products=[], comparisons=[], copies=[], clarification=None, preferences_extracted=[], recommendation_refresh_triggered=False, session_memory=dict(session_memory), plan=plan, executed_steps=[], skipped_steps=[], projection_event_id=None, projection_event_type=None, projection_trigger=None, agent_details=ShoppingAgentDetails(steps_executed=0, workers_called=[], terminal_state="fallback_used", latency_ms=(perf_counter() - started) * 1000))

    def _build_runtime_fallback_result(self, *, started: float, plan: TurnPlan, session_memory: dict[str, Any]) -> ShoppingManagerTurnResult:
        return ShoppingManagerTurnResult(reply="The runtime hit a boundary error and used the fallback path.", products=[], comparisons=[], copies=[], clarification=None, preferences_extracted=[], recommendation_refresh_triggered=False, session_memory=dict(session_memory), plan=plan, executed_steps=[], skipped_steps=[], projection_event_id=None, projection_event_type=None, projection_trigger=None, agent_details=ShoppingAgentDetails(steps_executed=0, workers_called=[], terminal_state="fallback_used", latency_ms=(perf_counter() - started) * 1000))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _is_runtime_boundary_error(exc: Exception) -> bool:
        return isinstance(exc, PermissionError) or "unknown tool" in str(exc)


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["SessionProductPreview", "ShoppingManager"]
