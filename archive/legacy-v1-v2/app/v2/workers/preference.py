from __future__ import annotations

import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal

from app.v2.core.models import Event, PreferenceSignal, UserProfile, WorkerResult, WorkerTask
from app.v2.core.prompts import PromptRegistry, build_default_prompt_registry
from app.v2.core.runtime import Worker, WorkerExecutionContext

PreferenceRefreshTrigger = Literal["preference_stable", "preference_corrected"]

_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("手机", ("手机", "phone", "iphone")),
    ("笔记本", ("笔记本", "laptop", "电脑")),
    ("平板", ("平板", "tablet", "ipad")),
    ("耳机", ("耳机", "headphone")),
    ("相机", ("相机", "camera")),
)

_BRAND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Apple", ("apple", "iphone", "ipad")),
    ("Huawei", ("huawei", "华为")),
    ("Xiaomi", ("xiaomi", "小米", "redmi")),
    ("Acme", ("acme",)),
    ("Nova", ("nova",)),
)

_USE_CASE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("游戏", ("游戏", "gaming", "手游")),
    ("拍照", ("拍照", "摄影", "camera")),
    ("办公", ("办公", "office", "工作")),
    ("通勤", ("通勤", "便携", "轻薄")),
)


@dataclass(frozen=True, slots=True)
class PreferenceUpdateOutcome:
    memory: dict[str, Any]
    refresh_trigger: PreferenceRefreshTrigger | None
    changed_categories: tuple[str, ...]
    stable_categories: tuple[str, ...]
    conflict_categories: tuple[str, ...]


class PreferenceWorker(Worker):
    def __init__(self, *, prompt_registry: PromptRegistry | None = None) -> None:
        super().__init__(
            "preference_worker",
            allowed_tools={"session.read_memory", "profile.extract_preferences"},
        )
        self._prompt_registry = prompt_registry or build_default_prompt_registry()

    async def execute(
        self,
        task: WorkerTask,
        context: WorkerExecutionContext,
    ) -> WorkerResult:
        started = perf_counter()
        message = str(task.input.get("message", ""))
        source_turn = int(task.input.get("source_turn", 1))
        session_id = str(task.input.get("session_id") or context.session_id or "")
        memory_payload = await context.call_tool(
            "session.read_memory",
            {"session_id": session_id},
        )
        session_memory = dict(memory_payload.get("memory") or {})

        self._prompt_registry.render(
            "preference.extract",
            variables={
                "message": message,
                "session_memory": json.dumps(
                    session_memory,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
        )

        extract_payload = await context.call_tool(
            "profile.extract_preferences",
            {
                "message": message,
                "source_turn": source_turn,
                "session_memory": session_memory,
            },
        )
        signals = [
            PreferenceSignal.model_validate(signal)
            for signal in list(extract_payload.get("signals") or [])
        ]
        return WorkerResult(
            worker_name=self.name,
            payload={
                "categories": [signal.category for signal in signals],
            },
            signals=signals,
            latency_ms=(perf_counter() - started) * 1000,
        )


def extract_preference_signals(message: str, *, source_turn: int) -> list[PreferenceSignal]:
    lowered = message.lower()
    signals: list[PreferenceSignal] = []

    budget_match = re.search(r"(?:预算|价位|¥|￥)?\s*(\d{3,6})(?:元|块|预算)?", message)
    if budget_match is not None:
        signals.append(
            PreferenceSignal(
                category="budget",
                value=budget_match.group(1),
                confidence=0.92,
                source_turn=source_turn,
            ),
        )

    for value, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in lowered or keyword in message for keyword in keywords):
            signals.append(
                PreferenceSignal(
                    category="product_category",
                    value=value,
                    confidence=0.85,
                    source_turn=source_turn,
                ),
            )
            break

    for value, keywords in _BRAND_KEYWORDS:
        if any(keyword in lowered or keyword in message for keyword in keywords):
            signals.append(
                PreferenceSignal(
                    category="brand",
                    value=value,
                    confidence=0.8,
                    source_turn=source_turn,
                ),
            )
            break

    for value, keywords in _USE_CASE_KEYWORDS:
        if any(keyword in lowered or keyword in message for keyword in keywords):
            signals.append(
                PreferenceSignal(
                    category="use_case",
                    value=value,
                    confidence=0.78,
                    source_turn=source_turn,
                ),
            )
            break

    exclusion_match = re.search(r"(?:不要|不想要|排除|别要)\s*([^，。,；; ]{1,12})", message)
    if exclusion_match is not None:
        signals.append(
            PreferenceSignal(
                category="exclusion",
                value=exclusion_match.group(1).strip(),
                confidence=0.75,
                source_turn=source_turn,
            ),
        )

    deduped: list[PreferenceSignal] = []
    seen: set[tuple[str, str]] = set()
    for signal in signals:
        marker = (signal.category, signal.value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(signal)
    return deduped


def apply_preference_signals(
    current_memory: dict[str, Any],
    *,
    signals: list[PreferenceSignal],
    user_turn_number: int,
) -> PreferenceUpdateOutcome:
    memory = dict(current_memory)
    previous_preferences = {
        str(category): str(value)
        for category, value in dict(memory.get("preferences", {})).items()
    }
    current_preferences = dict(previous_preferences)
    history = [_coerce_history_entry(entry) for entry in list(memory.get("preference_history", []))]

    for signal in signals:
        current_preferences[signal.category] = signal.value
        history.append(signal.model_dump(mode="json"))

    recent_turns = sorted({int(entry["source_turn"]) for entry in history})[-3:]
    recent_history = [
        entry
        for entry in history
        if int(entry["source_turn"]) in recent_turns
    ]
    latest_signals = _latest_signals_by_category(history)

    stable_categories = tuple(
        sorted(
            category
            for category, signal in latest_signals.items()
            if float(signal["confidence"]) >= 0.7
        ),
    )
    conflict_categories = tuple(
        sorted(
            category
            for category in {str(entry["category"]) for entry in recent_history}
            if len(
                {
                    str(entry["value"])
                    for entry in recent_history
                    if str(entry["category"]) == category
                },
            )
            > 1
        ),
    )
    changed_categories = tuple(
        sorted(
            {
                signal.category
                for signal in signals
                if signal.confidence >= 0.7
                and previous_preferences.get(signal.category) not in (None, signal.value)
            },
        ),
    )
    is_stable = len(stable_categories) >= 2 and not conflict_categories

    signature = _preference_signature(current_preferences)
    refresh_trigger: PreferenceRefreshTrigger | None = None
    if changed_categories and signature != memory.get("last_writeback_signature"):
        refresh_trigger = "preference_corrected"
    elif is_stable and signature != memory.get("last_writeback_signature"):
        refresh_trigger = "preference_stable"

    if current_preferences:
        memory["preferences"] = current_preferences
    memory["preference_history"] = history[-50:]
    memory["preference_status"] = {
        "stable": is_stable,
        "stable_categories": list(stable_categories),
        "changed_categories": list(changed_categories),
        "conflict_categories": list(conflict_categories),
        "last_evaluated_turn": user_turn_number,
    }
    if refresh_trigger is not None:
        memory["last_writeback_signature"] = signature
        memory["last_writeback_turn"] = user_turn_number
        memory["last_refresh_trigger"] = refresh_trigger

    return PreferenceUpdateOutcome(
        memory=memory,
        refresh_trigger=refresh_trigger,
        changed_categories=changed_categories,
        stable_categories=stable_categories,
        conflict_categories=conflict_categories,
    )


def build_user_profile(
    *,
    user_id: str,
    preferences: dict[str, Any],
    existing_profile: UserProfile | None,
) -> UserProfile:
    base = existing_profile.model_copy(deep=True) if existing_profile is not None else UserProfile(user_id=user_id)

    category = _as_non_empty_string(preferences.get("product_category"))
    if category is not None:
        base.preferred_categories = _merge_unique(base.preferred_categories, [category])

    brand = _as_non_empty_string(preferences.get("brand"))
    if brand is not None:
        base.preferred_brands = _merge_unique(base.preferred_brands, [brand])

    use_case = _as_non_empty_string(preferences.get("use_case"))
    if use_case is not None:
        base.use_cases = _merge_unique(base.use_cases, [use_case])

    exclusion = _as_non_empty_string(preferences.get("exclusion"))
    if exclusion is not None:
        base.excluded_terms = _merge_unique(base.excluded_terms, [exclusion])

    budget = _as_non_empty_string(preferences.get("budget"))
    if budget is not None:
        max_price = float(budget)
        base.price_range = (0.0, max_price)

    base.cold_start = False
    return base


def build_refresh_event_payload(
    *,
    session_id: str,
    user_turn_number: int,
    preferences: dict[str, Any],
    update: PreferenceUpdateOutcome,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "turn_number": user_turn_number,
        "trigger": update.refresh_trigger,
        "preferences": {
            str(category): str(value)
            for category, value in preferences.items()
        },
        "changed_categories": list(update.changed_categories),
        "stable_categories": list(update.stable_categories),
        "conflict_categories": list(update.conflict_categories),
    }


def attach_refresh_event(memory: dict[str, Any], event: Event) -> dict[str, Any]:
    updated = dict(memory)
    updated["last_refresh_event_id"] = event.event_id
    updated["last_refresh_trigger"] = event.payload.get("trigger")
    return updated


def _coerce_history_entry(entry: Any) -> dict[str, Any]:
    if isinstance(entry, PreferenceSignal):
        return entry.model_dump(mode="json")

    payload = dict(entry or {})
    return {
        "category": str(payload.get("category", "")),
        "value": str(payload.get("value", "")),
        "confidence": float(payload.get("confidence", 0.0)),
        "source_turn": int(payload.get("source_turn", 0)),
    }


def _latest_signals_by_category(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for entry in history:
        category = str(entry["category"])
        latest[category] = entry
    return latest


def _preference_signature(preferences: dict[str, str]) -> str:
    return json.dumps(
        {
            str(category): str(preferences[category])
            for category in sorted(preferences)
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _merge_unique(existing: list[str], additions: list[str]) -> list[str]:
    merged = list(existing)
    seen = set(existing)
    for value in additions:
        if value in seen:
            continue
        merged.append(value)
        seen.add(value)
    return merged


def _as_non_empty_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text


__all__ = [
    "PreferenceRefreshTrigger",
    "PreferenceUpdateOutcome",
    "PreferenceWorker",
    "apply_preference_signals",
    "attach_refresh_event",
    "build_refresh_event_payload",
    "build_user_profile",
    "extract_preference_signals",
]
