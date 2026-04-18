from __future__ import annotations

import json
import logging

from app.shared.models.domain import RFMScore, UserBehaviorSummary, UserProfile, UserSegment
from app.v1.agents.base import BaseAgent
from app.v1.models.agent_io import UserProfileInput, UserProfileOutput
from app.v1.services.feature_store import FeatureStore
from app.v1.services.llm_service import LLMService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an ecommerce user profiling assistant.
Return JSON only with keys:
- segments
- preferred_categories
- price_range
- rfm_score
- tags
"""


class UserProfileAgent(BaseAgent):
    def __init__(
        self,
        *,
        feature_store: FeatureStore | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        super().__init__(name="user_profile")
        self.feature_store = feature_store or FeatureStore()
        self.llm_service = llm_service or LLMService()

    async def execute(self, input_data: dict) -> dict:
        request = UserProfileInput(**input_data)
        logger.info("user_profile started user_id=%s", request.user_id)

        summary = await self.feature_store.get_user_behavior(request.user_id)
        if not summary.has_history:
            logger.info("user_profile cold start user_id=%s", request.user_id)
            return self._build_cold_start_output(request.user_id)

        prompt = self._build_prompt(request.user_id, summary)
        raw_profile = await self.llm_service.complete_json(SYSTEM_PROMPT, prompt)
        profile = self._parse_profile(request.user_id, raw_profile, summary)

        output = UserProfileOutput.from_profile(profile)
        logger.info(
            "user_profile produced segment=%s user_id=%s",
            output.segment.value,
            request.user_id,
        )
        return output.model_dump(mode="json")

    def default_result(self, input_data: dict) -> dict:
        user_id = str(input_data.get("user_id", "unknown"))
        logger.warning("user_profile degrading to cold start user_id=%s", user_id)
        return self._build_cold_start_output(user_id)

    def _build_prompt(self, user_id: str, summary: UserBehaviorSummary) -> str:
        payload = summary.model_dump(mode="json")
        return f"用户ID: {user_id}\n行为摘要:\n{json.dumps(payload, ensure_ascii=False)}"

    def _parse_profile(
        self,
        user_id: str,
        raw_profile: str,
        summary: UserBehaviorSummary,
    ) -> UserProfile:
        cleaned = raw_profile.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM returned invalid JSON") from exc

        preferred_categories = payload.get("preferred_categories") or summary.top_categories
        segments = self._parse_segments(payload.get("segments"))
        if not segments:
            segments = [self._fallback_segment(summary)]

        price_range = self._parse_price_range(
            payload.get("price_range"),
            summary,
        )
        rfm_score = RFMScore(**(payload.get("rfm_score") or summary.rfm_score.model_dump()))
        tags = payload.get("tags") or self._fallback_tags(summary, preferred_categories, segments)

        return UserProfile(
            user_id=user_id,
            segments=segments,
            preferred_categories=preferred_categories,
            price_range=price_range,
            rfm_score=rfm_score,
            tags=tags,
            cold_start=False,
        )

    def _parse_segments(self, raw_segments: list[str] | None) -> list[UserSegment]:
        segments: list[UserSegment] = []
        for raw_segment in raw_segments or []:
            try:
                segments.append(UserSegment(raw_segment))
            except ValueError:
                continue
        return segments

    def _parse_price_range(
        self,
        raw_price_range: list[float] | tuple[float, float] | None,
        summary: UserBehaviorSummary,
    ) -> tuple[float, float]:
        if isinstance(raw_price_range, (list, tuple)) and len(raw_price_range) >= 2:
            min_price = float(raw_price_range[0])
            max_price = float(raw_price_range[1])
        else:
            reference_price = max(
                summary.average_purchase_price,
                summary.average_view_price,
                299.0,
            )
            min_price = round(reference_price * 0.5, 2)
            max_price = round(reference_price * 1.3, 2)

        if min_price > max_price:
            min_price, max_price = max_price, min_price
        return (min_price, max_price)

    def _fallback_segment(self, summary: UserBehaviorSummary) -> UserSegment:
        if summary.rfm_score.monetary >= 0.8:
            return UserSegment.HIGH_VALUE
        if 0 < summary.rfm_score.recency <= 0.2:
            return UserSegment.CHURN_RISK
        if summary.average_purchase_price and summary.average_purchase_price <= 500:
            return UserSegment.PRICE_SENSITIVE
        return UserSegment.ACTIVE

    def _fallback_tags(
        self,
        summary: UserBehaviorSummary,
        preferred_categories: list[str],
        segments: list[UserSegment],
    ) -> list[str]:
        tags = []
        if UserSegment.HIGH_VALUE in segments:
            tags.append("高客单价")
        if UserSegment.PRICE_SENSITIVE in segments:
            tags.append("价格敏感")
        if len(summary.purchases) >= 2:
            tags.append("复购倾向")
        tags.extend(preferred_categories[:2])
        return list(dict.fromkeys(tags))

    def _build_cold_start_output(self, user_id: str) -> dict:
        hot_categories = self.feature_store.get_hot_categories()
        profile = UserProfile(
            user_id=user_id,
            segments=[UserSegment.NEW_USER],
            preferred_categories=hot_categories,
            price_range=(0.0, 999.0),
            rfm_score=RFMScore(),
            tags=hot_categories,
            cold_start=True,
        )
        return UserProfileOutput.from_profile(profile).model_dump(mode="json")
