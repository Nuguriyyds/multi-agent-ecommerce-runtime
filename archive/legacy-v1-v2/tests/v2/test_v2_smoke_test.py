from __future__ import annotations

from smoke_test_v2 import SMOKE_TRACE_IDS, SMOKE_USER_ID, run_smoke_test_v2


def test_run_smoke_test_v2_executes_key_v2_flow():
    summary = run_smoke_test_v2()

    assert summary["user_id"] == SMOKE_USER_ID
    assert summary["turn_count"] == 4
    assert summary["observed_trace_ids"] == SMOKE_TRACE_IDS
    assert summary["trace_turn_2_terminal_state"] == "reply_ready"
    assert summary["trace_turn_2_projection_event_type"] == "profile_projection"
    assert len(summary["processed_background_event_ids"]) == 2
    assert summary["task_counts"]["conversation:completed"] >= 4
    assert summary["background_task_count"] == 3
    assert summary["snapshot_counts"]["homepage"] == 2
    assert summary["completed_event_types"] == [
        "profile_projection",
        "recommendation_refresh",
        "click",
        "recommendation_refresh",
    ]
    assert summary["pending_event_types_after_feedback"] == []
    assert summary["homepage_product_ids"]
    assert summary["feedback_event_id"].startswith("evt_")
    assert summary["last_terminal_state"] == "reply_ready"
    assert summary["last_projection_trigger"] == "preference_stable"
    assert len(summary["profile_categories"]) == 1
