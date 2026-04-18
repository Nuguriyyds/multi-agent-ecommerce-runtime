from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from app.shared.models.domain import MarketingCopy, Product, UserProfile, UserSegment
from app.v1.agents.base import BaseAgent
from app.v1.models.agent_io import MarketingCopyInput, MarketingCopyOutput
from app.v1.services.llm_service import LLMService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an ecommerce marketing copywriter.
Generate one concise marketing copy for each product.
Keep the style aligned with the requested user segment.
Avoid compliance-risky claims such as 最好, 第一, 100%, 绝对.
Return JSON only in this format:
{"copies":[{"product_id":"sku-1","copy_text":"..."}]}
"""

TEMPLATE_HINTS = {
    UserSegment.NEW_USER: "Warm and friendly. Lower decision friction and highlight easy first purchase.",
    UserSegment.HIGH_VALUE: "Premium and polished. Emphasize flagship quality, craftsmanship, and brand value.",
    UserSegment.PRICE_SENSITIVE: "Value-focused. Emphasize cost performance, savings, and practical benefits.",
    UserSegment.ACTIVE: "Energetic and scenario-driven. Emphasize standout features and daily usage.",
    UserSegment.CHURN_RISK: "Re-engagement tone. Emphasize renewed interest and returning value.",
}

FORBIDDEN_REPLACEMENTS = {
    "最好": "更合适",
    "第一": "优选",
    "国家级": "专业级",
    "全球首": "新一代",
    "绝对": "稳定",
    "100%": "多重",
    "永久": "长期",
    "万能": "多场景",
    "祖传": "经典",
    "纯天然": "自然灵感",
}

SEGMENT_PRIORITY = [
    UserSegment.NEW_USER,
    UserSegment.HIGH_VALUE,
    UserSegment.CHURN_RISK,
    UserSegment.PRICE_SENSITIVE,
    UserSegment.ACTIVE,
]


class MarketingCopyAgent(BaseAgent):
    def __init__(
        self,
        *,
        llm_service: LLMService | None = None,
        timeout: float | None = None,
        max_retries: int = 2,
        retry_base_delay: float = 0.5,
    ) -> None:
        super().__init__(
            name="marketing_copy",
            timeout=timeout,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
        )
        self.llm_service = llm_service or LLMService()

    async def execute(self, input_data: dict) -> dict:
        request = MarketingCopyInput(**input_data)
        template = self._select_template(request.profile)

        logger.info(
            "marketing_copy started segment=%s product_count=%s",
            template.value,
            len(request.products),
        )

        if not request.products:
            return MarketingCopyOutput(
                copies=[],
                template_used=template.value,
            ).model_dump(mode="json")

        prompt = self._build_prompt(
            profile=request.profile,
            products=request.products,
            template=template,
        )
        raw_response = await self.llm_service.complete_json(SYSTEM_PROMPT, prompt)
        copies = self._parse_copies(raw_response, request.products)

        filtered_count = 0
        compliant_copies: list[MarketingCopy] = []
        for copy in copies:
            filtered_text = self._apply_compliance_filter(copy.copy_text)
            if filtered_text != copy.copy_text:
                filtered_count += 1
            compliant_copies.append(
                copy.model_copy(update={"copy_text": filtered_text}),
            )

        logger.info(
            "marketing_copy completed segment=%s copies=%s filtered=%s",
            template.value,
            len(compliant_copies),
            filtered_count,
        )
        return MarketingCopyOutput(
            copies=compliant_copies,
            template_used=template.value,
        ).model_dump(mode="json")

    def default_result(self, input_data: dict) -> dict:
        profile = self._coerce_profile(input_data.get("profile"))
        products = self._coerce_products(input_data.get("products"))
        template = self._select_template(profile)

        logger.warning(
            "marketing_copy degrading to product descriptions segment=%s product_count=%s",
            template.value,
            len(products),
        )

        return MarketingCopyOutput(
            copies=[
                MarketingCopy(
                    product_id=product.product_id,
                    copy_text=self._default_copy_text(product),
                )
                for product in products
            ],
            template_used=template.value,
        ).model_dump(mode="json")

    def _build_prompt(
        self,
        *,
        profile: UserProfile | None,
        products: list[Product],
        template: UserSegment,
    ) -> str:
        payload = {
            "template": template.value,
            "style_hint": TEMPLATE_HINTS[template],
            "profile": self._profile_payload(profile),
            "products": [
                {
                    "product_id": product.product_id,
                    "name": product.name,
                    "category": product.category,
                    "price": product.price,
                    "description": product.description,
                    "brand": product.brand,
                    "tags": product.tags,
                }
                for product in products
            ],
        }
        return f"marketing_copy_input:\n{json.dumps(payload, ensure_ascii=False)}"

    def _parse_copies(self, raw_response: str, products: list[Product]) -> list[MarketingCopy]:
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM returned invalid JSON for marketing copy") from exc

        if isinstance(payload, dict):
            raw_copies = payload.get("copies", [])
        elif isinstance(payload, list):
            raw_copies = payload
        else:
            raise ValueError("LLM returned unsupported payload for marketing copy")

        copy_by_product_id: dict[str, str] = {}
        for item in raw_copies:
            if not isinstance(item, dict):
                continue

            product_id = str(item.get("product_id", "")).strip()
            copy_text = str(item.get("copy_text") or item.get("copy") or "").strip()
            if not product_id or not copy_text:
                continue
            copy_by_product_id[product_id] = copy_text

        if not copy_by_product_id and products:
            raise ValueError("LLM returned no usable marketing copies")

        return [
            MarketingCopy(
                product_id=product.product_id,
                copy_text=copy_by_product_id.get(
                    product.product_id,
                    self._default_copy_text(product),
                ),
            )
            for product in products
        ]

    def _apply_compliance_filter(self, text: str) -> str:
        filtered = text
        for forbidden, replacement in FORBIDDEN_REPLACEMENTS.items():
            filtered = re.sub(re.escape(forbidden), replacement, filtered)
        return " ".join(filtered.split())

    def _select_template(self, profile: UserProfile | None) -> UserSegment:
        if profile is None or not profile.segments:
            return UserSegment.ACTIVE

        segments = set(profile.segments)
        for segment in SEGMENT_PRIORITY:
            if segment in segments:
                return segment
        return UserSegment.ACTIVE

    def _profile_payload(self, profile: UserProfile | None) -> dict:
        if profile is None:
            return {}
        return {
            "user_id": profile.user_id,
            "segments": [segment.value for segment in profile.segments],
            "preferred_categories": profile.preferred_categories,
            "price_range": list(profile.price_range),
            "tags": profile.tags,
            "cold_start": profile.cold_start,
        }

    def _default_copy_text(self, product: Product) -> str:
        return product.description or product.name

    def _coerce_profile(self, raw_profile: object) -> UserProfile | None:
        if isinstance(raw_profile, UserProfile):
            return raw_profile
        if raw_profile is None:
            return None
        try:
            return UserProfile.model_validate(raw_profile)
        except ValidationError:
            return None

    def _coerce_products(self, raw_products: object) -> list[Product]:
        products: list[Product] = []
        if not isinstance(raw_products, list):
            return products

        for raw_product in raw_products:
            try:
                products.append(Product.model_validate(raw_product))
            except ValidationError:
                continue
        return products
