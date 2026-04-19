from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from app.v3.models import CapabilityDescriptor, CapabilityKind, Observation
from app.v3.registry import ToolProvider

_PREFERENCE_PROFILE_UPDATE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "preferences": {"type": "object"},
        "feedback_signal": {"type": "string"},
        "context": {"type": "object"},
    },
    "required": ["preferences"],
    "additionalProperties": False,
}

_PREFERENCE_PROFILE_UPDATE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "profile_updates": {"type": "array", "items": {"type": "object"}},
        "write_policy": {"type": "string"},
        "audit_notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["profile_updates", "write_policy", "audit_notes"],
    "additionalProperties": False,
}

_CONFIRMING_SIGNALS = {"explicit_confirmed", "confirmed", "user_confirmed"}


def preference_profile_update(
    *,
    preferences: dict[str, Any],
    feedback_signal: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_signal = (feedback_signal or "explicit_confirmed").strip().lower()
    write_policy = (
        "requires_user_confirmation"
        if normalized_signal not in _CONFIRMING_SIGNALS
        else "session_only"
    )

    updates = [
        {
            "key": key,
            "value": value,
            "source": "explicit_preference_state",
            "confidence": "confirmed" if normalized_signal in _CONFIRMING_SIGNALS else "low",
            "feedback_signal": normalized_signal,
        }
        for key, value in preferences.items()
    ]

    audit_notes = [
        "Generated an auditable preference-state proposal; this tool never writes durable memory directly.",
        f"write_policy={write_policy}",
    ]
    if context:
        audit_notes.append(f"context_keys={','.join(sorted(str(key) for key in context))}")

    return {
        "profile_updates": updates,
        "write_policy": write_policy,
        "audit_notes": audit_notes,
    }


class PreferenceProfileUpdateProvider(ToolProvider):
    def __init__(self) -> None:
        super().__init__(
            CapabilityDescriptor(
                name="preference_profile_update",
                kind=CapabilityKind.tool,
                input_schema=_PREFERENCE_PROFILE_UPDATE_INPUT_SCHEMA,
                output_schema=_PREFERENCE_PROFILE_UPDATE_OUTPUT_SCHEMA,
                timeout=2.0,
                permission_tag="preference.profile.propose",
                description=(
                    "Propose auditable preference-state updates without writing durable memory."
                ),
            )
        )
        self._logger = logging.getLogger(__name__)

    async def invoke(self, args: dict[str, Any]) -> Observation:
        self._logger.info("preference_profile_update start args=%s", args)
        preferences = args.get("preferences")
        if not isinstance(preferences, dict):
            raise ValueError("preferences must be an object")

        feedback_signal = args.get("feedback_signal")
        if feedback_signal is not None and not isinstance(feedback_signal, str):
            raise ValueError("feedback_signal must be a string")

        context = args.get("context")
        if context is not None and not isinstance(context, dict):
            raise ValueError("context must be an object")

        payload = preference_profile_update(
            preferences=preferences,
            feedback_signal=feedback_signal,
            context=context,
        )
        observation = Observation(
            observation_id=f"obs-{uuid4().hex[:12]}",
            source=self.name,
            status="ok" if payload["profile_updates"] else "partial",
            summary=(
                "Prepared an auditable preference-state proposal without writing durable memory."
            ),
            payload=payload,
            evidence_source=f"tool:{self.name}",
        )
        self._logger.info(
            "preference_profile_update success updates=%s observation_id=%s",
            len(payload["profile_updates"]),
            observation.observation_id,
        )
        return observation


__all__ = ["PreferenceProfileUpdateProvider", "preference_profile_update"]
