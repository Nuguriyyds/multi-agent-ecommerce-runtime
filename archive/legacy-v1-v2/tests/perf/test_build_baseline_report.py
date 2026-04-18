from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.perf.build_baseline_report import extract_k6_metrics


def test_extract_k6_metrics_collects_endpoint_and_custom_metrics():
    summary = {
        "metrics": {
            "http_reqs": {
                "count": 20,
                "rate": 10,
                "submetrics": [
                    {
                        "tags": {"endpoint": "v2_create_session"},
                        "values": {"count": 5, "rate": 2.5},
                    },
                    {
                        "tags": {"endpoint": "v2_feedback"},
                        "values": {"count": 5, "rate": 2.5},
                    },
                ],
            },
            "http_req_failed": {
                "rate": 0.05,
                "submetrics": [
                    {
                        "tags": {"endpoint": "v2_create_session"},
                        "values": {"rate": 0.0},
                    }
                ],
            },
            "http_req_duration": {
                "avg": 90,
                "med": 80,
                "p(95)": 120,
                "p(99)": 150,
                "max": 180,
                "submetrics": [
                    {
                        "tags": {"endpoint": "v2_create_session"},
                        "values": {"avg": 40, "med": 35, "p(95)": 50, "p(99)": 60, "max": 70},
                    }
                ],
            },
            "iterations": {"count": 4, "rate": 2},
            "v1_agent_latency_user_profile_ms": {"avg": 11, "med": 10, "p(95)": 15, "p(99)": 18},
            "v1_agent_degraded_user_profile": {"rate": 0.25},
            "v2_reply_ready": {"rate": 0.8},
            "v2_refresh_triggered": {"rate": 0.5},
            "v2_message_reported_latency_ms": {"avg": 65, "med": 60, "p(95)": 90, "p(99)": 100},
        }
    }

    metrics = extract_k6_metrics(summary)

    assert metrics["http_reqs"]["count"] == 20
    assert metrics["endpoint_metrics"]["v2_create_session"]["http_reqs"]["count"] == 5
    assert metrics["endpoint_metrics"]["v2_create_session"]["duration_ms"]["p95"] == 50
    assert metrics["custom_metrics"]["v1"]["agent_latency_ms"]["user_profile"]["avg"] == 11
    assert metrics["custom_metrics"]["v1"]["agent_degraded_ratio"]["user_profile"] == 0.25
    assert metrics["custom_metrics"]["v2"]["reply_ready_ratio"] == 0.8
    assert metrics["custom_metrics"]["v2"]["message_reported_latency_ms"]["p99"] == 100


def test_extract_k6_metrics_marks_missing_p99_as_none():
    summary = {
        "metrics": {
            "http_req_duration": {
                "avg": 90,
                "med": 80,
                "p(95)": 120,
                "max": 180,
            },
            "v2_message_reported_latency_ms": {
                "avg": 65,
                "med": 60,
                "p(95)": 90,
            },
        }
    }

    metrics = extract_k6_metrics(summary)

    assert metrics["http_req_duration_ms"]["p99"] is None
    assert metrics["custom_metrics"]["v2"]["message_reported_latency_ms"]["p99"] is None
