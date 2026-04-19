from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from app.v3.hooks import HookBus
from app.v3.models import MemoryEntry, MemoryLayer, MemorySource, MemoryStatus, MemoryWriteDecision

from .write_decision import emit_memory_write_hook

if TYPE_CHECKING:
    from app.v3.models import SessionState

_LOGGER = logging.getLogger(__name__)

# Keyword vocabulary is intentionally kept independent from
# app.v3.specialists.shopping_brief: the memory layer must not depend on
# specialists. The shopping_brief specialist (F10) remains the authoritative
# slot extractor for specialist observations. This module provides a lighter
# regex pass that runs at message intake, so ContextPacketBuilder sees the
# confirmed_preferences dict populated even before the MainAgent turn runs.
_BRAND_KEYWORDS: tuple[str, ...] = (
    "Apple",
    "Sony",
    "Bose",
    "Sennheiser",
    "Huawei",
    "Xiaomi",
    "Samsung",
    "Beats",
    "Nothing",
)

_SCENE_KEYWORDS: dict[str, str] = {
    "通勤": "commute",
    "commute": "commute",
    "办公室": "office",
    "office": "office",
    "旅行": "travel",
    "travel": "travel",
    "礼物": "gift",
    "送礼": "gift",
    "gift": "gift",
    "运动": "gym",
    "健身": "gym",
    "gym": "gym",
    "游戏": "gaming",
    "gaming": "gaming",
    "日常": "daily",
    "daily": "daily",
}

_CATEGORY_KEYWORDS: dict[str, str] = {
    "耳机": "earphones",
    "降噪": "earphones",
    "earphone": "earphones",
    "headphone": "earphones",
    "earbuds": "earphones",
    "手机": "phone",
    "phone": "phone",
}

_BUDGET_LANGUAGE_MARKERS: tuple[str, ...] = ("预算", "budget", "左右", "块", "元")
_BUDGET_CEILING_MARKERS: tuple[str, ...] = ("内", "以内", "不超过", "以下")
_EXCLUSION_MARKERS: tuple[str, ...] = ("不要", "排除", "不考虑", "exclude", "not ")


def extract_preferences(message: str) -> dict[str, Any]:
    text = message.strip()
    if not text:
        return {}

    result: dict[str, Any] = {}

    budget = _extract_budget(text)
    if budget is not None:
        result["budget"] = budget

    category = _extract_category(text)
    if category is not None:
        result["category"] = category

    scene = _extract_scene(text)
    if scene is not None:
        result["scene"] = scene

    exclusions = _extract_exclusions(text)
    if exclusions:
        result["exclude_brands"] = exclusions

    return result


def _extract_budget(text: str) -> dict[str, Any] | None:
    lowered = text.lower()
    has_budget_signal = any(marker in lowered for marker in _BUDGET_LANGUAGE_MARKERS) or any(
        marker in text for marker in _BUDGET_CEILING_MARKERS
    )
    if not has_budget_signal:
        return None

    numbers = re.findall(r"\d{3,6}", text.replace(",", ""))
    if not numbers:
        return None

    ceiling = int(numbers[-1])
    return {"max": ceiling, "currency": "CNY"}


def _extract_category(text: str) -> str | None:
    lowered = text.lower()
    for keyword, category in _CATEGORY_KEYWORDS.items():
        haystack = lowered if keyword.isascii() else text
        if keyword in haystack:
            return category
    return None


def _extract_scene(text: str) -> str | None:
    lowered = text.lower()
    for keyword, scene in _SCENE_KEYWORDS.items():
        haystack = lowered if keyword.isascii() else text
        if keyword in haystack:
            return scene
    return None


def _extract_exclusions(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for brand in _BRAND_KEYWORDS:
        index = lowered.find(brand.lower())
        if index < 0:
            continue
        prefix = text[max(0, index - 8) : index]
        prefix_lower = prefix.lower()
        if any(marker in prefix or marker in prefix_lower for marker in _EXCLUSION_MARKERS):
            found.append(brand)
    return list(dict.fromkeys(found))


async def extract_and_store_preferences(
    session_state: "SessionState",
    message: str,
    *,
    hook_bus: HookBus | None = None,
    trace_id: str | None = None,
    turn_number: int | None = None,
) -> dict[str, Any]:
    extracted = extract_preferences(message)
    if not extracted:
        return {}

    working_memory = session_state.session_working_memory
    confirmed = working_memory.setdefault("confirmed_preferences", {})

    newly_written: dict[str, Any] = {}
    for key, value in extracted.items():
        if confirmed.get(key) == value:
            continue
        confirmed[key] = value
        newly_written[key] = value
        _LOGGER.info(
            "preference_extractor wrote key=%s value=%r session=%s turn=%s",
            key,
            value,
            session_state.session_id,
            turn_number,
        )

        entry = MemoryEntry(
            key=key,
            value=value,
            source=MemorySource.user_confirmed,
            layer=MemoryLayer.session_working,
        )
        decision = MemoryWriteDecision(
            decision="allow",
            target_layer=MemoryLayer.session_working,
            memory_key=key,
            reason="preference_extractor: user_confirmed extraction from user message",
        )
        await emit_memory_write_hook(
            hook_bus,
            entry=entry,
            decision=decision,
            session_id=session_state.session_id,
            user_id=session_state.user_id,
            trace_id=trace_id,
            turn_number=turn_number,
        )

    return newly_written


async def revoke_preference(
    session_state: "SessionState",
    key: str,
    *,
    reason: str = "user requested revoke",
    hook_bus: HookBus | None = None,
    trace_id: str | None = None,
    turn_number: int | None = None,
) -> bool:
    working_memory = session_state.session_working_memory
    durable_memory = session_state.durable_user_memory
    confirmed = working_memory.get("confirmed_preferences")
    if not isinstance(confirmed, dict):
        confirmed = {}

    revoked_value: Any | None = None
    layer: MemoryLayer | None = None

    if key in confirmed:
        revoked_value = confirmed.pop(key)
        layer = MemoryLayer.session_working
    elif key in durable_memory:
        revoked_value = durable_memory.pop(key)
        layer = MemoryLayer.durable_user

    if layer is None:
        _LOGGER.info(
            "preference_extractor revoke noop: key=%s not present session=%s",
            key,
            session_state.session_id,
        )
        return False

    entry = MemoryEntry(
        key=key,
        value=revoked_value,
        source=MemorySource.user_confirmed,
        layer=layer,
        status=MemoryStatus.revoked,
    )
    decision = MemoryWriteDecision(
        decision="revoke",
        target_layer=layer,
        memory_key=key,
        reason=reason,
    )
    await emit_memory_write_hook(
        hook_bus,
        entry=entry,
        decision=decision,
        session_id=session_state.session_id,
        user_id=session_state.user_id,
        trace_id=trace_id,
        turn_number=turn_number,
    )
    _LOGGER.info(
        "preference_extractor revoked key=%s layer=%s session=%s",
        key,
        layer.value,
        session_state.session_id,
    )
    return True


def get_preference_profile(session_state: "SessionState") -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    working_confirmed = session_state.session_working_memory.get("confirmed_preferences")
    if isinstance(working_confirmed, dict):
        for key, value in working_confirmed.items():
            entries.append(
                {
                    "key": key,
                    "value": value,
                    "layer": MemoryLayer.session_working.value,
                    "source": MemorySource.user_confirmed.value,
                    "status": MemoryStatus.active.value,
                }
            )
            seen_keys.add(key)

    for key, value in session_state.durable_user_memory.items():
        if key in seen_keys:
            continue
        entries.append(
            {
                "key": key,
                "value": value,
                "layer": MemoryLayer.durable_user.value,
                "source": MemorySource.user_confirmed.value,
                "status": MemoryStatus.active.value,
            }
        )

    return entries


__all__ = [
    "extract_and_store_preferences",
    "extract_preferences",
    "get_preference_profile",
    "revoke_preference",
]
