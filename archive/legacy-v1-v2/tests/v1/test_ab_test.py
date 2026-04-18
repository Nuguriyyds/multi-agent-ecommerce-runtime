from __future__ import annotations

import random

from app.v1.services.ab_test import ABTestEngine


def test_ab_test_engine_assigns_same_user_to_same_bucket():
    engine = ABTestEngine(seed=7)

    first_assignment = engine.assign_user("u_repeat")
    second_assignment = engine.assign_user("u_repeat")

    assert first_assignment == second_assignment
    assert first_assignment.experiment_name == "rec_strategy"
    assert 0 <= first_assignment.bucket < 10_000


def test_ab_test_engine_supports_multi_experiment_configs_and_variant_parameters():
    engine = ABTestEngine(
        experiments={
            "rec_strategy": [
                {
                    "name": "control",
                    "parameters": {"rerank_enabled": False},
                },
                {
                    "name": "treatment",
                    "parameters": {"rerank_enabled": True},
                },
            ],
            "copy_style": [
                {
                    "name": "safe",
                    "parameters": {"copy_tone": "conservative"},
                },
                {
                    "name": "bold",
                    "parameters": {"copy_tone": "bold"},
                },
            ],
        },
        seed=11,
    )

    assignments = [
        engine.assign_user(f"user-{index}")
        for index in range(200)
    ]
    variants = {
        assignment.variant_name: assignment
        for assignment in assignments
    }

    assert {"control", "treatment"}.issubset(variants)
    assert variants["control"].parameters["rerank_enabled"] is False
    assert variants["treatment"].parameters["rerank_enabled"] is True

    copy_assignment = engine.assign_user("copy-user", experiment_name="copy_style")
    assert copy_assignment.parameters["copy_tone"] in {"conservative", "bold"}


def test_ab_test_engine_initial_distribution_is_close_to_even():
    engine = ABTestEngine(seed=23, sampling_draws=800)

    assignments = [
        engine.assign_user(f"user-{index}").variant_name
        for index in range(2_000)
    ]
    control_ratio = assignments.count("control") / len(assignments)
    treatment_ratio = assignments.count("treatment") / len(assignments)

    assert 0.4 <= control_ratio <= 0.6
    assert 0.4 <= treatment_ratio <= 0.6


def test_ab_test_engine_thompson_sampling_converges_to_better_variant():
    engine = ABTestEngine(seed=101, sampling_draws=400)
    feedback_rng = random.Random(2026)
    true_ctr = {
        "control": 0.12,
        "treatment": 0.35,
    }

    for index in range(600):
        user_id = f"sim-user-{index}"
        assignment = engine.assign_user(user_id)
        clicked = feedback_rng.random() < true_ctr[assignment.variant_name]
        engine.record_result(
            experiment_name="rec_strategy",
            user_id=user_id,
            clicked=clicked,
        )

    probabilities = engine.get_variant_probabilities("rec_strategy")
    stats = engine.get_experiment_stats("rec_strategy")

    assert engine.get_best_variant("rec_strategy") == "treatment"
    assert probabilities["treatment"] > probabilities["control"]
    assert stats["treatment"]["posterior_mean"] > stats["control"]["posterior_mean"]
    assert stats["treatment"]["impressions"] > stats["control"]["impressions"]
