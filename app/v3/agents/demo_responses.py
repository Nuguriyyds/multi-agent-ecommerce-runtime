"""Demo-mode mock LLM responses for the /ui playground.

Loaded automatically by the V3 API wiring when ECOV3_OPENAI_API_KEY is empty, so
that reviewers can open /ui without an LLM endpoint and still see a complete
multi-step turn plus a non-trivial trace.

Each key is the raw user message (the same normalization as the mock scenario
selector in LLMClient._select_mock_key). The values are either a single
AgentDecision-shaped dict (one-step turn) or a list of dicts (multi-step turn).

These seeds intentionally mirror the 3 smoke scenarios in tests/v3/smoke/:
  - Scenario A (happy path, 2 turns)
  - Scenario B (multi-turn clarification, 3 turns)
  - Scenario C (business-boundary fallback, 1 turn)
"""
from __future__ import annotations

from typing import Any

HAPPY_TOOL_OBSERVATION_ID = "obs-111111111111"


DEMO_MOCK_RESPONSES: dict[str, Any] = {
    # --- Scenario A: Happy Path ---
    "帮我看看 3000 左右的降噪耳机": {
        "action": {
            "kind": "ask_clarification",
            "question": "好的，3000 左右的降噪耳机。你主要在什么场景用？有没有特别不想要的品牌？",
            "missing_slots": ["scene", "exclusions"],
        },
        "rationale": "Need scene and brand exclusions before the search can continue safely.",
        "next_task_label": "clarify_constraints",
        "continue_loop": False,
    },
    "通勤用，不要 beats": [
        {
            "action": {
                "kind": "call_tool",
                "capability_name": "catalog_search",
                "arguments": {
                    "query": "3000 左右 通勤 降噪耳机",
                    "filters": {
                        "category": "earphones",
                        "scene": "commute",
                        "price_min": 2500,
                        "price_max": 3500,
                        "exclude_brands": ["Beats"],
                        "limit": 4,
                    },
                },
            },
            "rationale": "The user has enough confirmed constraints to search the catalog.",
            "next_task_label": "search_candidates",
            "continue_loop": True,
        },
        {
            "action": {
                "kind": "reply_to_user",
                "message": (
                    "3000 左右通勤降噪耳机里，Sony WH-1000XM5 最均衡，"
                    "Bose QuietComfort Ultra 更偏舒适佩戴。要不要我再继续对比音质差异？"
                ),
                "observation_ids": [HAPPY_TOOL_OBSERVATION_ID],
            },
            "rationale": "One catalog-backed observation is enough for the first recommendation reply.",
            "next_task_label": "reply_to_user",
            "continue_loop": False,
        },
    ],
    # --- Scenario B: Multi-turn Clarification ---
    "帮我选个礼物": {
        "action": {
            "kind": "ask_clarification",
            "question": "好的，帮你选礼物。先了解一下：送给谁？大概什么预算？",
            "missing_slots": ["recipient", "budget"],
        },
        "rationale": "No searchable constraints are available yet.",
        "next_task_label": "clarify_gift_context",
        "continue_loop": False,
    },
    "送女朋友的，生日礼物": {
        "action": {
            "kind": "ask_clarification",
            "question": "生日礼物，明白了。预算大概多少？有没有她比较喜欢的品类方向？",
            "missing_slots": ["budget", "category"],
        },
        "rationale": "The recipient and occasion are clear, but budget and category are still missing.",
        "next_task_label": "clarify_budget_and_category",
        "continue_loop": False,
    },
    "1000-2000 吧，她喜欢听歌": {
        "action": {
            "kind": "ask_clarification",
            "question": "1000-2000，她爱听歌。你是想送耳机之类的数码产品，还是其他方向？",
            "missing_slots": ["category_confirmation"],
        },
        "rationale": "Earphones are only an inferred direction and still need explicit confirmation.",
        "next_task_label": "confirm_category_direction",
        "continue_loop": False,
    },
    # --- Scenario C: Business-boundary Fallback ---
    "帮我下单": {
        "action": {
            "kind": "fallback",
            "reason": "business_scope_violation",
            "user_message": (
                "目前我只能帮你做导购咨询。下单需要你到电商平台直接购买，"
                "如果你想继续比较商品我可以接着帮你看。"
            ),
        },
        "rationale": "Checkout is outside the V3.0 shopping-guidance boundary.",
        "next_task_label": "fallback",
        "continue_loop": False,
    },
    "就这个了，帮我下单": {
        "action": {
            "kind": "fallback",
            "reason": "business_scope_violation",
            "user_message": (
                "目前我只能帮你做导购咨询。下单需要你到电商平台直接购买，"
                "如果你想继续比较商品我可以接着帮你看。"
            ),
        },
        "rationale": "Checkout is outside the V3.0 shopping-guidance boundary.",
        "next_task_label": "fallback",
        "continue_loop": False,
    },
}


__all__ = ["DEMO_MOCK_RESPONSES", "HAPPY_TOOL_OBSERVATION_ID"]
