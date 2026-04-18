from __future__ import annotations

import json
import logging

from app.shared.config.settings import get_settings

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - exercised when dependency is not installed
    AsyncOpenAI = None

logger = logging.getLogger(__name__)


class LLMService:
    def __init__(self) -> None:
        settings = get_settings()
        self.model = settings.llm_model
        self._client = None

        if settings.llm_api_key and AsyncOpenAI is not None:
            self._client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
        elif settings.llm_api_key and AsyncOpenAI is None:
            logger.warning("openai package not installed; falling back to mock LLM")

    async def complete_json(self, system_prompt: str, user_prompt: str) -> str:
        if self._client is None:
            logger.info("llm_service using mock response")
            return self._mock_response(user_prompt)

        logger.info("llm_service calling remote model=%s", self.model)
        response = await self._client.chat.completions.create(
            model=self.model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return response.choices[0].message.content or "{}"

    def _mock_response(self, user_prompt: str) -> str:
        if "product_rerank_input:\n" in user_prompt:
            return self._mock_product_rerank_response(user_prompt)
        if "marketing_copy_input:\n" in user_prompt:
            return self._mock_marketing_copy_response(user_prompt)
        return self._mock_profile_response(user_prompt)

    def _mock_product_rerank_response(self, user_prompt: str) -> str:
        payload = self._extract_payload(user_prompt, "product_rerank_input:\n")
        profile = payload.get("profile") or {}
        products = payload.get("products") or []

        preferred_rank = {
            category: index
            for index, category in enumerate(profile.get("preferred_categories", []))
        }
        profile_tags = set(profile.get("tags", []))
        segments = set(profile.get("segments", []))
        raw_price_range = profile.get("price_range") or [0.0, 0.0]
        try:
            min_price = float(raw_price_range[0])
            max_price = float(raw_price_range[1])
        except (TypeError, ValueError, IndexError):
            min_price, max_price = 0.0, 0.0

        scored_products: list[tuple[float, float, str]] = []
        for index, product in enumerate(products):
            product_id = str(product.get("product_id") or product.get("id") or "").strip()
            if not product_id:
                continue

            category = str(product.get("category", "")).strip()
            try:
                price = float(product.get("price", 0.0))
            except (TypeError, ValueError):
                price = 0.0
            try:
                base_score = float(product.get("score", 0.0))
            except (TypeError, ValueError):
                base_score = 0.0

            score = base_score
            if category in preferred_rank:
                score += max(2.0 - (preferred_rank[category] * 0.4), 0.6)

            score += min(
                len(profile_tags.intersection(product.get("tags", []))) * 0.2,
                0.6,
            )

            if "high_value" in segments and price >= max(min_price, 3000.0):
                score += 0.7
            if "price_sensitive" in segments and price <= max(max_price, 800.0):
                score += 0.8
            if "active" in segments and category in preferred_rank:
                score += 0.15
            if "churn_risk" in segments and category in preferred_rank:
                score += 0.25

            scored_products.append((score, -index, product_id))

        scored_products.sort(reverse=True)
        ranked_product_ids = [product_id for _, _, product_id in scored_products]
        return json.dumps({"ranked_product_ids": ranked_product_ids}, ensure_ascii=False)

    def _mock_profile_response(self, user_prompt: str) -> str:
        summary = self._extract_payload(user_prompt, "行为摘要:\n")
        top_categories = summary.get("top_categories", [])
        average_purchase_price = float(summary.get("average_purchase_price", 0.0))
        average_view_price = float(summary.get("average_view_price", 0.0))
        rfm_score = summary.get("rfm_score", {})
        purchase_count = len(summary.get("purchases", []))

        segments: list[str] = []
        if rfm_score.get("monetary", 0.0) >= 0.8:
            segments.append("high_value")
        if 0 < rfm_score.get("recency", 0.0) <= 0.2:
            segments.append("churn_risk")
        if average_purchase_price and average_purchase_price <= 500:
            segments.append("price_sensitive")
        if not segments:
            segments.append("active")

        preferred_categories = top_categories or ["手机", "耳机"]
        reference_price = max(average_purchase_price, average_view_price, 299.0)
        min_price = max(round(reference_price * 0.5, 2), 0.0)
        max_price = round(reference_price * 1.3, 2)

        tags = []
        if purchase_count >= 2:
            tags.append("复购倾向")
        if average_purchase_price >= 5000:
            tags.append("高客单价")
        if average_purchase_price and average_purchase_price <= 500:
            tags.append("价格敏感")
        tags.extend(top_categories[:2])
        tags = list(dict.fromkeys(tags))

        payload = {
            "segments": segments,
            "preferred_categories": preferred_categories,
            "price_range": [min_price, max_price],
            "rfm_score": rfm_score,
            "tags": tags,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _mock_marketing_copy_response(self, user_prompt: str) -> str:
        payload = self._extract_payload(user_prompt, "marketing_copy_input:\n")
        template = str(payload.get("template", "active"))
        products = payload.get("products", [])

        copies: list[dict[str, str]] = []
        for product in products:
            product_id = str(product.get("product_id") or product.get("id") or "").strip()
            if not product_id:
                continue

            name = str(product.get("name", "")).strip()
            description = str(product.get("description", "")).strip()
            price = product.get("price", 0)
            try:
                price_text = f"{float(price):.0f}元"
            except (TypeError, ValueError):
                price_text = "当前到手价"

            copies.append(
                {
                    "product_id": product_id,
                    "copy_text": self._build_mock_copy(
                        template=template,
                        name=name,
                        description=description,
                        price_text=price_text,
                    ),
                },
            )

        return json.dumps({"copies": copies}, ensure_ascii=False)

    def _build_mock_copy(
        self,
        *,
        template: str,
        name: str,
        description: str,
        price_text: str,
    ) -> str:
        if template == "new_user":
            return f"{name} 现在入手更轻松，{description}，作为新客首单选择很友好。"
        if template == "high_value":
            return f"{name} 以高端质感和旗舰体验见长，{description}，适合重视品质的你。"
        if template == "price_sensitive":
            return f"{name} 把核心体验和预算控制平衡得不错，{price_text}更显划算，{description}。"
        if template == "churn_risk":
            return f"{name} 这次回来看点不一样的，{description}，现在入手更容易重新找回心动感。"
        return f"{name} 把亮点直接带进日常使用，{description}，上手体验自然顺畅。"

    def _extract_payload(self, user_prompt: str, marker: str) -> dict:
        payload = user_prompt.split(marker, 1)[1] if marker in user_prompt else user_prompt
        return json.loads(payload)
