from __future__ import annotations

import asyncio
import logging
import time
from uuid import uuid4

from pydantic import ValidationError

from app.shared.models.domain import (
    AgentExecutionDetail,
    InventoryStatus,
    MarketingCopy,
    Product,
    RecommendationRequest,
    RecommendationResponse,
    UserProfile,
)
from app.shared.observability.logging_utils import configure_logging
from app.v1.agents.inventory import InventoryAgent
from app.v1.agents.marketing_copy import MarketingCopyAgent
from app.v1.agents.product_rec import ProductRecAgent
from app.v1.agents.user_profile import UserProfileAgent
from app.v1.models.agent_result import AgentResult

configure_logging()
logger = logging.getLogger(__name__)


class Supervisor:
    def __init__(
        self,
        *,
        user_profile_agent: UserProfileAgent | None = None,
        product_rec_agent: ProductRecAgent | None = None,
        inventory_agent: InventoryAgent | None = None,
        marketing_copy_agent: MarketingCopyAgent | None = None,
        candidate_multiplier: int = 3,
    ) -> None:
        self.user_profile_agent = user_profile_agent or UserProfileAgent()
        self.product_rec_agent = product_rec_agent or ProductRecAgent()
        self.inventory_agent = inventory_agent or InventoryAgent()
        self.marketing_copy_agent = marketing_copy_agent or MarketingCopyAgent()
        self.candidate_multiplier = max(1, candidate_multiplier)

    async def recommend(self, input_data: dict) -> RecommendationResponse:
        request = RecommendationRequest(**input_data)
        request_id = str(input_data.get("request_id") or uuid4())
        start = time.perf_counter()
        candidate_count = max(request.num_items * self.candidate_multiplier, request.num_items)

        logger.info(
            "supervisor_started",
            extra={
                "request_id": request_id,
                "user_id": request.user_id,
                "scene": request.scene,
                "num_items": request.num_items,
            },
        )

        profile_result, coarse_result = await asyncio.gather(
            self.user_profile_agent.run({"user_id": request.user_id}),
            self.product_rec_agent.run(
                {
                    "num_items": candidate_count,
                    "candidate_pool_size": candidate_count,
                },
            ),
        )

        profile = self._coerce_profile(profile_result.data.get("profile"))
        coarse_products = self._coerce_products(coarse_result.data.get("products"))

        rerank_payload: dict = {
            "num_items": request.num_items,
            "candidate_pool_size": candidate_count,
            "experiment_name": request.experiment_name,
            "experiment_parameters": request.experiment_parameters,
        }
        if profile is not None:
            rerank_payload["profile"] = profile.model_dump(mode="json")

        inventory_payload = {
            "products": [
                product.model_dump(mode="json", by_alias=True)
                for product in coarse_products
            ],
        }

        ranked_result, inventory_result = await asyncio.gather(
            self.product_rec_agent.run(rerank_payload),
            self.inventory_agent.run(inventory_payload),
        )

        ranked_products = self._coerce_products(ranked_result.data.get("products"))
        inventory_products = self._coerce_products(inventory_result.data.get("products"))
        inventory_status = self._coerce_inventory_statuses(
            inventory_result.data.get("inventory_status"),
        )

        ranking_source = ranked_products or coarse_products
        final_products = self._merge_ranked_and_inventory(
            ranked_products=ranking_source,
            inventory_products=inventory_products,
            limit=request.num_items,
        )

        copy_payload: dict = {
            "products": [
                product.model_dump(mode="json", by_alias=True)
                for product in final_products
            ],
        }
        if profile is not None:
            copy_payload["profile"] = profile.model_dump(mode="json")

        copy_result = await self.marketing_copy_agent.run(copy_payload)
        copies = self._align_copies(
            self._coerce_copies(copy_result.data.get("copies")),
            final_products,
        )

        response = RecommendationResponse(
            request_id=request_id,
            user_id=request.user_id,
            profile=profile,
            recommendations=final_products,
            copies=copies,
            inventory_status=inventory_status,
            agent_details={
                "user_profile": self._build_agent_detail(profile_result),
                "product_rec_coarse": self._build_agent_detail(coarse_result),
                "product_rec_ranked": self._build_agent_detail(ranked_result),
                "inventory": self._build_agent_detail(inventory_result),
                "marketing_copy": self._build_agent_detail(copy_result),
            },
            latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )

        self._log_degradation(
            request_id=request_id,
            profile_result=profile_result,
            coarse_result=coarse_result,
            ranked_result=ranked_result,
            inventory_result=inventory_result,
            copy_result=copy_result,
        )
        logger.info(
            "supervisor_completed",
            extra={
                "request_id": request_id,
                "recommendations": len(response.recommendations),
                "copies": len(response.copies),
                "latency_ms": response.latency_ms,
                "stage_latencies_ms": {
                    stage: detail.latency_ms
                    for stage, detail in response.agent_details.items()
                },
                "degraded_stages": [
                    stage
                    for stage, detail in response.agent_details.items()
                    if detail.degraded
                ],
            },
        )
        return response

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
        if not isinstance(raw_products, list):
            return []

        products: list[Product] = []
        for raw_product in raw_products:
            try:
                products.append(Product.model_validate(raw_product))
            except ValidationError:
                continue
        return products

    def _coerce_inventory_statuses(self, raw_statuses: object) -> list[InventoryStatus]:
        if not isinstance(raw_statuses, list):
            return []

        statuses: list[InventoryStatus] = []
        for raw_status in raw_statuses:
            try:
                statuses.append(InventoryStatus.model_validate(raw_status))
            except ValidationError:
                continue
        return statuses

    def _coerce_copies(self, raw_copies: object) -> list[MarketingCopy]:
        if not isinstance(raw_copies, list):
            return []

        copies: list[MarketingCopy] = []
        for raw_copy in raw_copies:
            try:
                copies.append(MarketingCopy.model_validate(raw_copy))
            except ValidationError:
                continue
        return copies

    def _merge_ranked_and_inventory(
        self,
        *,
        ranked_products: list[Product],
        inventory_products: list[Product],
        limit: int,
    ) -> list[Product]:
        available_by_id = {
            product.product_id: product
            for product in inventory_products
        }
        final_products: list[Product] = []

        for ranked_product in ranked_products:
            available_product = available_by_id.get(ranked_product.product_id)
            if available_product is None:
                continue
            final_products.append(
                ranked_product.model_copy(
                    update={"stock": available_product.stock},
                    deep=True,
                ),
            )
            if len(final_products) >= limit:
                break

        return final_products

    def _align_copies(
        self,
        copies: list[MarketingCopy],
        products: list[Product],
    ) -> list[MarketingCopy]:
        copy_by_product_id = {
            copy.product_id: copy
            for copy in copies
        }
        return [
            copy_by_product_id.get(
                product.product_id,
                MarketingCopy(
                    product_id=product.product_id,
                    copy_text=product.description or product.name,
                ),
            )
            for product in products
        ]

    def _build_agent_detail(self, result: AgentResult) -> AgentExecutionDetail:
        return AgentExecutionDetail(
            success=result.success,
            degraded=result.degraded,
            attempts=result.attempts,
            error=result.error,
            latency_ms=result.latency_ms,
        )

    def _log_degradation(
        self,
        *,
        request_id: str,
        profile_result: AgentResult,
        coarse_result: AgentResult,
        ranked_result: AgentResult,
        inventory_result: AgentResult,
        copy_result: AgentResult,
    ) -> None:
        result_by_stage = {
            "user_profile": profile_result,
            "product_rec_coarse": coarse_result,
            "product_rec_ranked": ranked_result,
            "inventory": inventory_result,
            "marketing_copy": copy_result,
        }

        for stage, result in result_by_stage.items():
            if result.degraded:
                logger.warning(
                    "supervisor_stage_degraded",
                    extra={
                        "request_id": request_id,
                        "stage": stage,
                        "error": result.error,
                    },
                )
