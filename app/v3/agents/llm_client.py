from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.v3.config import get_settings
from app.v3.models import AgentDecision, TurnRuntimeContext

MockDecisionPayload = str | Mapping[str, Any] | AgentDecision


class LLMClientError(RuntimeError):
    """Base error for LLM client failures."""


class LLMTransportError(LLMClientError):
    """Raised when the OpenAI-compatible transport fails."""


class LLMResponseFormatError(LLMClientError):
    """Raised when the LLM response does not contain usable JSON content."""


class LLMClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 15.0,
        mock_responses: Mapping[str, Sequence[MockDecisionPayload] | MockDecisionPayload] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = settings.openai_api_key if api_key is None else api_key
        self._base_url = (settings.openai_base_url if base_url is None else base_url).rstrip("/")
        self._model = settings.openai_model if model is None else model
        self._logger = logging.getLogger(__name__)
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout)
        self._mock_responses = {
            key: self._normalize_sequence(value)
            for key, value in (mock_responses or {}).items()
        }
        self._mock_cursors: dict[str, int] = {}
        self.prompt_history: list[str] = []
        self.scenario_history: list[str] = []

    async def complete_decision_json(
        self,
        *,
        prompt: str,
        context: TurnRuntimeContext,
    ) -> str:
        self.prompt_history.append(prompt)

        if not self._api_key:
            scenario_key = self._select_mock_key(context)
            self.scenario_history.append(scenario_key)
            self._logger.info("LLM mock completion selected scenario=%s", scenario_key)
            return self._next_mock_payload(scenario_key)

        self._logger.info("LLM completion start model=%s", self._model)
        return await self._complete_remote(prompt)

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http_client.aclose()

    def install_mock_responses(
        self,
        mock_responses: Mapping[str, Sequence[MockDecisionPayload] | MockDecisionPayload],
    ) -> None:
        self._mock_responses = {
            key: self._normalize_sequence(value)
            for key, value in mock_responses.items()
        }
        self._mock_cursors.clear()
        self.prompt_history.clear()
        self.scenario_history.clear()

    async def _complete_remote(self, prompt: str) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": prompt,
                }
            ],
        }

        try:
            response = await self._http_client.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            self._logger.warning("LLM transport failure: %s", exc)
            raise LLMTransportError(str(exc)) from exc

        try:
            body = response.json()
        except ValueError as exc:
            self._logger.warning("LLM returned non-JSON transport body")
            raise LLMResponseFormatError("transport response body is not valid JSON") from exc

        content = self._extract_message_content(body)
        if not content:
            self._logger.warning("LLM returned empty content payload")
            raise LLMResponseFormatError("chat completion content is empty")

        self._logger.info("LLM completion success model=%s", self._model)
        return content

    def normalize_decision_payload(self, payload: str) -> str:
        parsed = self._parse_json_like_payload(payload)
        decision = self._extract_decision_mapping(parsed)
        return json.dumps(decision, ensure_ascii=False)

    def _select_mock_key(self, context: TurnRuntimeContext) -> str:
        if not self._mock_responses:
            raise LLMClientError("mock_responses must be configured when OPENAI_API_KEY is empty")

        latest_message = context.context_packet.latest_user_message.strip().lower()

        keyword_matches = [
            key
            for key in self._mock_responses
            if key not in {"default", "__default__", "happy_path", "missing_budget"}
            and key.strip().lower() in latest_message
        ]
        if keyword_matches:
            return max(keyword_matches, key=len)

        if self._budget_missing(context) and "missing_budget" in self._mock_responses:
            return "missing_budget"

        if "happy_path" in self._mock_responses:
            return "happy_path"

        if "default" in self._mock_responses:
            return "default"

        if "__default__" in self._mock_responses:
            return "__default__"

        return next(iter(self._mock_responses))

    def _next_mock_payload(self, scenario_key: str) -> str:
        sequence = self._mock_responses.get(scenario_key)
        if sequence is None:
            fallback_key = "default" if "default" in self._mock_responses else "__default__"
            sequence = self._mock_responses.get(fallback_key)
            scenario_key = fallback_key

        if not sequence:
            raise LLMClientError(f"mock response sequence for {scenario_key!r} is empty")

        index = self._mock_cursors.get(scenario_key, 0)
        self._mock_cursors[scenario_key] = index + 1
        selected = sequence[index] if index < len(sequence) else sequence[-1]
        return self._coerce_payload(selected)

    @staticmethod
    def _normalize_sequence(
        value: Sequence[MockDecisionPayload] | MockDecisionPayload,
    ) -> list[MockDecisionPayload]:
        if isinstance(value, (str, AgentDecision)) or isinstance(value, Mapping):
            return [value]
        return list(value)

    @staticmethod
    def _coerce_payload(payload: MockDecisionPayload) -> str:
        if isinstance(payload, str):
            return payload
        if isinstance(payload, AgentDecision):
            return payload.model_dump_json()
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _extract_message_content(body: Mapping[str, Any]) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMResponseFormatError("chat completion choices are missing")

        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise LLMResponseFormatError("chat completion message is missing")

        content = message.get("content")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts).strip()

        raise LLMResponseFormatError("chat completion content has an unsupported shape")

    @classmethod
    def _parse_json_like_payload(cls, payload: str) -> Any:
        candidate = payload.strip()
        if not candidate:
            raise ValueError("decision payload is empty")

        for raw_candidate in (
            candidate,
            cls._strip_code_fence(candidate),
            cls._extract_first_json_object(candidate),
        ):
            if not raw_candidate:
                continue
            try:
                return cls._decode_nested_json(json.loads(raw_candidate))
            except ValueError:
                continue

        stripped = cls._strip_code_fence(candidate)
        embedded = cls._extract_first_json_object(stripped)
        if embedded:
            try:
                return cls._decode_nested_json(json.loads(embedded))
            except ValueError:
                pass

        raise ValueError("decision payload does not contain a valid JSON object")

    @classmethod
    def _extract_decision_mapping(cls, payload: Any) -> dict[str, Any]:
        if isinstance(payload, Mapping):
            match = cls._find_decision_mapping(payload)
            if match is not None:
                return cls._sanitize_decision_mapping(match)
        raise ValueError("decision payload does not match the AgentDecision shape")

    @classmethod
    def _find_decision_mapping(cls, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if cls._looks_like_agent_decision(payload):
            return payload

        for key in ("decision", "agent_decision", "result", "response", "output", "data"):
            candidate = payload.get(key)
            parsed = cls._parse_nested_candidate(candidate)
            if isinstance(parsed, Mapping):
                match = cls._find_decision_mapping(parsed)
                if match is not None:
                    return match

        if len(payload) == 1:
            parsed = cls._parse_nested_candidate(next(iter(payload.values())))
            if isinstance(parsed, Mapping):
                match = cls._find_decision_mapping(parsed)
                if match is not None:
                    return match

        return None

    @staticmethod
    def _looks_like_agent_decision(payload: Mapping[str, Any]) -> bool:
        return "action" in payload and "rationale" in payload

    @classmethod
    def _parse_nested_candidate(cls, candidate: Any) -> Any:
        if isinstance(candidate, str):
            stripped = candidate.strip()
            if stripped:
                try:
                    return cls._parse_json_like_payload(stripped)
                except ValueError:
                    return candidate
        return candidate

    @staticmethod
    def _sanitize_decision_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
        action = payload.get("action")
        if isinstance(action, str):
            action = {"kind": action}
        if not isinstance(action, Mapping):
            raise ValueError("decision action must be an object")

        kind = action.get("kind")
        allowed_action_fields = {
            "reply_to_user": {"kind", "message", "observation_ids"},
            "ask_clarification": {"kind", "question", "missing_slots"},
            "call_tool": {"kind", "capability_name", "arguments"},
            "call_sub_agent": {"kind", "capability_name", "brief"},
            "fallback": {"kind", "reason", "user_message"},
        }
        if kind not in allowed_action_fields:
            raise ValueError(f"unsupported action kind: {kind!r}")

        decision: dict[str, Any] = {
            "action": {
                key: value
                for key, value in action.items()
                if key in allowed_action_fields[kind]
            },
            "rationale": payload.get("rationale"),
        }
        for field in ("next_task_label", "continue_loop", "routing_metadata"):
            if field in payload:
                decision[field] = payload[field]
        return decision

    @staticmethod
    def _strip_code_fence(payload: str) -> str:
        candidate = payload.strip()
        if not candidate.startswith("```"):
            return candidate

        lines = candidate.splitlines()
        if not lines:
            return candidate
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _extract_first_json_object(payload: str) -> str | None:
        start_index: int | None = None
        depth = 0
        in_string = False
        escaping = False

        for index, char in enumerate(payload):
            if start_index is None:
                if char == "{":
                    start_index = index
                    depth = 1
                continue

            if in_string:
                if escaping:
                    escaping = False
                elif char == "\\":
                    escaping = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return payload[start_index : index + 1]

        return None

    @staticmethod
    def _decode_nested_json(payload: Any) -> Any:
        candidate = payload
        for _ in range(2):
            if not isinstance(candidate, str):
                return candidate
            stripped = candidate.strip()
            if not stripped.startswith("{"):
                return candidate
            try:
                candidate = json.loads(stripped)
            except ValueError:
                return candidate
        return candidate

    @staticmethod
    def _budget_missing(context: TurnRuntimeContext) -> bool:
        candidate_sections = (
            context.context_packet.active_constraints,
            context.context_packet.session_working_memory,
            context.context_packet.durable_user_memory,
            context.context_packet.confirmed_preferences,
        )
        budget_keys = {"budget", "budget_max", "budget_min", "max_budget", "price_max", "price_min"}
        for section in candidate_sections:
            if any(key in section for key in budget_keys):
                return False
        return True


__all__ = [
    "LLMClient",
    "LLMClientError",
    "LLMResponseFormatError",
    "LLMTransportError",
]
