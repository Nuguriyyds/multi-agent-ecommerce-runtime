from __future__ import annotations

import json
import logging
from typing import Any

from app.shared.data.product_catalog import ProductCatalog
from app.shared.models.domain import Product, UserProfile, UserSegment
from app.v1.agents.base import BaseAgent
from app.v1.models.agent_io import ProductRecInput, ProductRecOutput
from app.v1.services.llm_service import LLMService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an ecommerce recommendation reranker.
Return JSON only with key ranked_product_ids, an array of candidate product ids ordered from best to worst.
Do not invent product ids that are not in the candidate list.
"""


class ProductRecAgent(BaseAgent):
    def __init__(
        self,
        *,
        product_catalog: ProductCatalog | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        super().__init__(name="product_rec")
        self.product_catalog = product_catalog or ProductCatalog()
        self.llm_service = llm_service or LLMService()

    async def execute(self, input_data: dict) -> dict:
        request = ProductRecInput(**input_data)
        candidate_pool_size = max(request.num_items, request.candidate_pool_size)
        limit = request.num_items
        strategy = self._resolve_strategy(request.experiment_parameters)

        logger.info(
            "product_rec started profile=%s cold_start=%s num_items=%s strategy=%s",
            request.profile is not None,
            request.profile.cold_start if request.profile else False,
            request.num_items,
            strategy,
        )

        candidates = await self.product_catalog.get_candidate_products(limit=candidate_pool_size)
        if self._should_use_coarse_ranking(request.profile):
            products = self._coarse_rank(candidates, limit=limit)
            logger.info("product_rec returned coarse ranking count=%s", len(products))
        else:
            personalized_products = self._personalized_rank(
                candidates,
                request.profile,
                limit=candidate_pool_size,
            )
            if self._should_use_llm_rerank(request.experiment_parameters):
                products = await self._llm_rerank(
                    personalized_products,
                    request.profile,
                    experiment_name=request.experiment_name,
                    strategy=strategy,
                    limit=limit,
                )
                logger.info(
                    "product_rec returned llm reranked ranking user_id=%s count=%s experiment=%s",
                    request.profile.user_id,
                    len(products),
                    request.experiment_name or "n/a",
                )
            else:
                products = personalized_products[:limit]
                logger.info(
                    "product_rec returned rule-based personalized ranking user_id=%s count=%s",
                    request.profile.user_id,
                    len(products),
                )

        return ProductRecOutput(products=products).model_dump(mode="json", by_alias=True)

    def default_result(self, input_data: dict) -> dict:
        requested_num_items = input_data.get("num_items", 10)
        try:
            limit = max(1, min(int(requested_num_items), 20))
        except (TypeError, ValueError):
            limit = 10

        logger.warning("product_rec degrading to fallback ranking limit=%s", limit)
        products = self.product_catalog.get_fallback_products(limit=limit)
        return ProductRecOutput(products=products).model_dump(mode="json", by_alias=True)

    def _should_use_coarse_ranking(self, profile: UserProfile | None) -> bool:
        return profile is None or profile.cold_start

    def _should_use_llm_rerank(self, experiment_parameters: dict[str, Any]) -> bool:
        if not experiment_parameters:
            return False
        if experiment_parameters.get("rerank_enabled") is True:
            return True
        return str(experiment_parameters.get("strategy", "")).strip().lower() == "llm_rerank"

    def _resolve_strategy(self, experiment_parameters: dict[str, Any]) -> str:
        strategy = str(experiment_parameters.get("strategy", "")).strip().lower()
        if strategy:
            return strategy
        if self._should_use_llm_rerank(experiment_parameters):
            return "llm_rerank"
        return "rule_based"

    def _coarse_rank(self, candidates: list[Product], *, limit: int) -> list[Product]:
        ranked = sorted(candidates, key=lambda product: (-product.score, product.price))
        return ranked[:limit]

    def _personalized_rank(
        self,
        candidates: list[Product],
        profile: UserProfile,
        *,
        limit: int,
    ) -> list[Product]:
        preferred_rank = {
            category: index
            for index, category in enumerate(profile.preferred_categories)
        }
        profile_tags = set(profile.tags)
        segments = set(profile.segments)

        ranked_products: list[Product] = []
        for candidate in candidates:
            score = float(candidate.score)
            score += self._category_boost(candidate, preferred_rank)
            score += self._price_boost(candidate.price, profile.price_range)
            score += self._tag_boost(candidate.tags, profile_tags)
            score += self._segment_boost(candidate, profile, segments)

            ranked_products.append(
                candidate.model_copy(
                    update={"score": round(score, 3)},
                ),
            )

        ranked_products.sort(key=lambda product: (-product.score, product.price))
        return ranked_products[:limit]

    async def _llm_rerank(
        self,
        candidates: list[Product],
        profile: UserProfile,
        *,
        experiment_name: str,
        strategy: str,
        limit: int,
    ) -> list[Product]:
        if not candidates:
            return []

        prompt = self._build_rerank_prompt(
            candidates=candidates,
            profile=profile,
            experiment_name=experiment_name,
            strategy=strategy,
        )
        try:
            raw_response = await self.llm_service.complete_json(SYSTEM_PROMPT, prompt)
            ranked_ids = self._parse_ranked_product_ids(raw_response)
            return self._apply_rerank_order(candidates, ranked_ids, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "product_rec llm rerank failed user_id=%s experiment=%s error=%s; using rule-based ranking",
                profile.user_id,
                experiment_name or "n/a",
                exc,
            )
            return candidates[:limit]

    def _build_rerank_prompt(
        self,
        *,
        candidates: list[Product],
        profile: UserProfile,
        experiment_name: str,
        strategy: str,
    ) -> str:
        payload = {
            "experiment_name": experiment_name,
            "strategy": strategy,
            "profile": {
                "user_id": profile.user_id,
                "segments": [segment.value for segment in profile.segments],
                "preferred_categories": profile.preferred_categories,
                "price_range": list(profile.price_range),
                "tags": profile.tags,
                "cold_start": profile.cold_start,
            },
            "products": [
                {
                    "product_id": product.product_id,
                    "name": product.name,
                    "category": product.category,
                    "price": product.price,
                    "brand": product.brand,
                    "description": product.description,
                    "tags": product.tags,
                    "score": product.score,
                }
                for product in candidates
            ],
        }
        return f"product_rerank_input:\n{json.dumps(payload, ensure_ascii=False)}"

    def _parse_ranked_product_ids(self, raw_response: str) -> list[str]:
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("LLM returned invalid JSON for product rerank") from exc

        if isinstance(payload, dict):
            raw_ranked = (
                payload.get("ranked_product_ids")
                or payload.get("products")
                or payload.get("ranked_products")
                or []
            )
        elif isinstance(payload, list):
            raw_ranked = payload
        else:
            raise ValueError("LLM returned unsupported payload for product rerank")

        ranked_ids: list[str] = []
        for item in raw_ranked:
            if isinstance(item, str):
                product_id = item.strip()
            elif isinstance(item, dict):
                product_id = str(item.get("product_id") or item.get("id") or "").strip()
            else:
                continue

            if product_id:
                ranked_ids.append(product_id)

        if not ranked_ids:
            raise ValueError("LLM returned no usable product ids for rerank")
        return ranked_ids

    def _apply_rerank_order(
        self,
        candidates: list[Product],
        ranked_ids: list[str],
        *,
        limit: int,
    ) -> list[Product]:
        candidate_by_id = {
            product.product_id: product
            for product in candidates
        }
        ordered_ids: list[str] = []
        seen: set[str] = set()

        for product_id in ranked_ids:
            if product_id in candidate_by_id and product_id not in seen:
                ordered_ids.append(product_id)
                seen.add(product_id)

        for product in candidates:
            if product.product_id not in seen:
                ordered_ids.append(product.product_id)

        ordered_ids = ordered_ids[: len(candidates)]
        baseline_scores = [round(product.score, 3) for product in candidates[: len(ordered_ids)]]

        reranked_products: list[Product] = []
        for index, product_id in enumerate(ordered_ids):
            reranked_products.append(
                candidate_by_id[product_id].model_copy(
                    update={"score": baseline_scores[index]},
                ),
            )

        return reranked_products[:limit]

    def _category_boost(
        self,
        candidate: Product,
        preferred_rank: dict[str, int],
    ) -> float:
        if candidate.category not in preferred_rank:
            return 0.0

        rank = preferred_rank[candidate.category]
        return max(1.6 - (rank * 0.3), 0.7)

    def _price_boost(self, price: float, price_range: tuple[float, float]) -> float:
        min_price, max_price = price_range
        if min_price > max_price:
            min_price, max_price = max_price, min_price

        if min_price <= price <= max_price:
            return 1.2

        ceiling = max(max_price, 1.0)
        if price < min_price:
            gap_ratio = (min_price - price) / max(min_price, 1.0)
        else:
            gap_ratio = (price - max_price) / ceiling

        return max(0.0, 1.0 - gap_ratio)

    def _tag_boost(self, candidate_tags: list[str], profile_tags: set[str]) -> float:
        overlap = profile_tags.intersection(candidate_tags)
        return min(len(overlap) * 0.35, 0.9)

    def _segment_boost(
        self,
        candidate: Product,
        profile: UserProfile,
        segments: set[UserSegment],
    ) -> float:
        boost = 0.0
        min_price, max_price = profile.price_range

        if UserSegment.HIGH_VALUE in segments and candidate.price >= max(min_price, 3000):
            boost += 0.8

        if UserSegment.PRICE_SENSITIVE in segments:
            if candidate.price <= min(max(max_price, 0.0), 800):
                boost += 0.9
            elif candidate.price > max(max_price, 0.0):
                boost -= 0.6

        if UserSegment.CHURN_RISK in segments and candidate.category in profile.preferred_categories:
            boost += 0.25

        if UserSegment.ACTIVE in segments and candidate.category in profile.preferred_categories:
            boost += 0.15

        return boost
