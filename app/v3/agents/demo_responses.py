"""Demo-mode mock LLM responses for the /ui playground.

Loaded automatically by the V3 API wiring when ECOV3_OPENAI_API_KEY is empty, so
that reviewers can open /ui without an LLM endpoint and still see a complete
multi-step turn plus a non-trivial trace.

Each key is the raw user message (the same normalization as the mock scenario
selector in LLMClient._select_mock_key). The values are either a single
AgentDecision-shaped dict (one-step turn) or a list of dicts (multi-step turn).

These seeds intentionally mirror and extend the smoke scenarios in tests/v3/smoke/:
  - Scenario A (happy path, 2 turns)
  - Scenario B (multi-turn clarification, 3 turns)
  - Scenario C (business-boundary fallback, 1 turn)
  - Scenario D (full specialist chain, 1 turn)
"""
from __future__ import annotations

from typing import Any

HAPPY_TOOL_OBSERVATION_ID = "obs-111111111111"
FULL_CHAIN_FINAL_OBSERVATION_ID = "obs-aaaaaaaaaaaa"


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
    # --- Scenario D: Full Specialist Chain ---
    "完整演示：3000 左右通勤降噪耳机，不要 Beats，帮我给出最终推荐": [
        {
            "action": {
                "kind": "call_sub_agent",
                "capability_name": "shopping_brief_specialist",
                "brief": {
                    "brief_id": "demo-brief-1",
                    "task_id": "demo-task-1",
                    "role": "shopping_brief",
                    "goal": "Extract a complete shopping brief for commute noise-canceling headphones.",
                    "constraints": {
                        "raw_user_need": "3000 左右通勤降噪耳机，不要 Beats",
                        "budget_max": 3000,
                        "category": "earphones",
                        "scene": "commute",
                        "exclude_brands": ["Beats"],
                    },
                    "allowed_capabilities": [],
                },
            },
            "rationale": "Start with structured need extraction before searching candidates.",
            "next_task_label": "extract_shopping_brief",
            "continue_loop": True,
        },
        {
            "action": {
                "kind": "call_sub_agent",
                "capability_name": "candidate_analysis_specialist",
                "brief": {
                    "brief_id": "demo-brief-2",
                    "task_id": "demo-task-2",
                    "role": "candidate_analysis",
                    "goal": "Search and analyze catalog-backed candidates for commute ANC headphones.",
                    "constraints": {
                        "query": "3000 左右 通勤 降噪耳机",
                        "category": "earphones",
                        "scene": "commute",
                        "budget_max": 3000,
                        "exclude_brands": ["Beats"],
                        "limit": 4,
                    },
                    "allowed_capabilities": ["catalog_search"],
                },
            },
            "rationale": "Use a specialist to turn the brief into catalog-backed candidates and fit reasons.",
            "next_task_label": "analyze_candidates",
            "continue_loop": True,
        },
        {
            "action": {
                "kind": "call_sub_agent",
                "capability_name": "comparison_specialist",
                "brief": {
                    "brief_id": "demo-brief-3",
                    "task_id": "demo-task-3",
                    "role": "comparison",
                    "goal": "Compare the strongest candidates on commuter-relevant dimensions.",
                    "constraints": {
                        "skus": ["EAR-SON-WH1000XM5", "EAR-BOS-QCUH"],
                        "dimensions": ["price", "battery", "noise_cancel", "weight"],
                    },
                    "allowed_capabilities": ["product_compare", "inventory_check"],
                },
            },
            "rationale": "Compare the top candidates before producing a final recommendation.",
            "next_task_label": "compare_shortlist",
            "continue_loop": True,
        },
        {
            "action": {
                "kind": "call_sub_agent",
                "capability_name": "recommendation_rationale_specialist",
                "brief": {
                    "brief_id": "demo-brief-4",
                    "task_id": "demo-task-4",
                    "role": "recommendation_rationale",
                    "goal": "Build evidence-cited rationales for the final pick.",
                    "constraints": {
                        "pick_sku": "EAR-SON-WH1000XM5",
                        "query": "Sony WH-1000XM5 通勤 降噪",
                    },
                    "allowed_capabilities": ["rag_product_knowledge"],
                },
            },
            "rationale": "Ask the rationale specialist to add traceable MCP-backed supporting evidence.",
            "next_task_label": "build_rationale",
            "continue_loop": True,
        },
        {
            "action": {
                "kind": "reply_to_user",
                "message": (
                    "完整链路结论：Sony WH-1000XM5 是这次更稳的通勤降噪耳机选择。"
                    "我先结构化了预算、场景和排除品牌，再做候选分析、双机对比，"
                    "最后补充 MCP 商品知识形成可追溯推荐理由。"
                ),
                "observation_ids": [FULL_CHAIN_FINAL_OBSERVATION_ID],
            },
            "rationale": "The final recommendation is supported by the rationale specialist observation.",
            "next_task_label": "reply_with_traceable_recommendation",
            "continue_loop": False,
        },
    ],
}


__all__ = [
    "DEMO_MOCK_RESPONSES",
    "FULL_CHAIN_FINAL_OBSERVATION_ID",
    "HAPPY_TOOL_OBSERVATION_ID",
]
