from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_EXPERIMENTS: dict[str, list[dict[str, Any]]] = {
    "rec_strategy": [
        {
            "name": "control",
            "parameters": {
                "strategy": "rule_based",
                "rerank_enabled": False,
            },
            "prior_alpha": 1.0,
            "prior_beta": 1.0,
        },
        {
            "name": "treatment",
            "parameters": {
                "strategy": "llm_rerank",
                "rerank_enabled": True,
            },
            "prior_alpha": 1.0,
            "prior_beta": 1.0,
        },
    ],
}


@dataclass(frozen=True)
class ExperimentAssignment:
    experiment_name: str
    variant_name: str
    bucket: int
    parameters: dict[str, Any]


@dataclass
class VariantRuntime:
    name: str
    parameters: dict[str, Any]
    prior_alpha: float = 1.0
    prior_beta: float = 1.0
    impressions: int = 0
    successes: int = 0
    failures: int = 0

    @property
    def alpha(self) -> float:
        return self.prior_alpha + self.successes

    @property
    def beta(self) -> float:
        return self.prior_beta + self.failures

    @property
    def posterior_mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


@dataclass
class ExperimentRuntime:
    name: str
    variants: list[VariantRuntime]
    assignments: dict[str, ExperimentAssignment] = field(default_factory=dict)

    @property
    def variant_by_name(self) -> dict[str, VariantRuntime]:
        return {
            variant.name: variant
            for variant in self.variants
        }


class ABTestEngine:
    def __init__(
        self,
        experiments: dict[str, list[dict[str, Any]]] | None = None,
        *,
        sampling_draws: int = 200,
        seed: int = 42,
    ) -> None:
        if sampling_draws <= 0:
            raise ValueError("sampling_draws must be greater than 0")

        self.sampling_draws = sampling_draws
        self._rng = random.Random(seed)
        self._experiments = self._build_experiments(experiments or DEFAULT_EXPERIMENTS)

    def assign_user(
        self,
        user_id: str,
        experiment_name: str = "rec_strategy",
    ) -> ExperimentAssignment:
        experiment = self._get_experiment(experiment_name)
        cached_assignment = experiment.assignments.get(user_id)
        if cached_assignment is not None:
            return cached_assignment

        bucket = self._hash_bucket(user_id, experiment_name)
        probabilities = self.get_variant_probabilities(experiment_name)
        variant = self._variant_for_bucket(experiment, bucket, probabilities)
        variant.impressions += 1

        assignment = ExperimentAssignment(
            experiment_name=experiment_name,
            variant_name=variant.name,
            bucket=bucket,
            parameters=dict(variant.parameters),
        )
        experiment.assignments[user_id] = assignment

        logger.info(
            "ab_test assigned user_id=%s experiment=%s variant=%s bucket=%s",
            user_id,
            experiment_name,
            variant.name,
            bucket,
        )
        return assignment

    def record_result(
        self,
        *,
        experiment_name: str,
        user_id: str,
        clicked: bool,
    ) -> ExperimentAssignment:
        assignment = self.assign_user(user_id=user_id, experiment_name=experiment_name)
        self.record_variant_result(
            experiment_name=experiment_name,
            variant_name=assignment.variant_name,
            clicked=clicked,
        )
        return assignment

    def record_variant_result(
        self,
        *,
        experiment_name: str,
        variant_name: str,
        clicked: bool,
    ) -> None:
        variant = self._get_variant(experiment_name, variant_name)
        if clicked:
            variant.successes += 1
        else:
            variant.failures += 1

        logger.info(
            "ab_test result recorded experiment=%s variant=%s clicked=%s successes=%s failures=%s",
            experiment_name,
            variant_name,
            clicked,
            variant.successes,
            variant.failures,
        )

    def get_variant_probabilities(self, experiment_name: str = "rec_strategy") -> dict[str, float]:
        experiment = self._get_experiment(experiment_name)
        wins = {
            variant.name: 0
            for variant in experiment.variants
        }

        for _ in range(self.sampling_draws):
            sampled_scores = {
                variant.name: self._rng.betavariate(variant.alpha, variant.beta)
                for variant in experiment.variants
            }
            winning_variant = max(sampled_scores, key=sampled_scores.get)
            wins[winning_variant] += 1

        return {
            variant_name: wins[variant_name] / self.sampling_draws
            for variant_name in wins
        }

    def get_best_variant(self, experiment_name: str = "rec_strategy") -> str:
        experiment = self._get_experiment(experiment_name)
        return max(
            experiment.variants,
            key=lambda variant: (variant.posterior_mean, variant.impressions),
        ).name

    def get_experiment_stats(self, experiment_name: str = "rec_strategy") -> dict[str, dict[str, float | int]]:
        experiment = self._get_experiment(experiment_name)
        return {
            variant.name: {
                "impressions": variant.impressions,
                "successes": variant.successes,
                "failures": variant.failures,
                "posterior_mean": variant.posterior_mean,
            }
            for variant in experiment.variants
        }

    def _build_experiments(
        self,
        raw_experiments: dict[str, list[dict[str, Any]]],
    ) -> dict[str, ExperimentRuntime]:
        experiments: dict[str, ExperimentRuntime] = {}

        for experiment_name, raw_variants in raw_experiments.items():
            if len(raw_variants) < 2:
                raise ValueError(f"experiment {experiment_name} must define at least two variants")

            variants: list[VariantRuntime] = []
            seen_names: set[str] = set()
            for raw_variant in raw_variants:
                name = str(raw_variant["name"])
                if name in seen_names:
                    raise ValueError(f"variant names must be unique in experiment {experiment_name}")
                seen_names.add(name)

                variants.append(
                    VariantRuntime(
                        name=name,
                        parameters=dict(raw_variant.get("parameters", {})),
                        prior_alpha=float(raw_variant.get("prior_alpha", 1.0)),
                        prior_beta=float(raw_variant.get("prior_beta", 1.0)),
                    ),
                )

            experiments[experiment_name] = ExperimentRuntime(
                name=experiment_name,
                variants=variants,
            )

        return experiments

    def _variant_for_bucket(
        self,
        experiment: ExperimentRuntime,
        bucket: int,
        probabilities: dict[str, float],
    ) -> VariantRuntime:
        bucket_ratio = bucket / 10_000
        cumulative = 0.0
        selected_variant = experiment.variants[-1]

        for variant in experiment.variants:
            cumulative += probabilities.get(variant.name, 0.0)
            if bucket_ratio < cumulative:
                selected_variant = variant
                break

        return selected_variant

    def _get_experiment(self, experiment_name: str) -> ExperimentRuntime:
        experiment = self._experiments.get(experiment_name)
        if experiment is None:
            raise KeyError(f"unknown experiment: {experiment_name}")
        return experiment

    def _get_variant(self, experiment_name: str, variant_name: str) -> VariantRuntime:
        experiment = self._get_experiment(experiment_name)
        variant = experiment.variant_by_name.get(variant_name)
        if variant is None:
            raise KeyError(f"unknown variant {variant_name} in experiment {experiment_name}")
        return variant

    def _hash_bucket(self, user_id: str, experiment_name: str) -> int:
        digest = hashlib.md5(f"{experiment_name}:{user_id}".encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % 10_000
