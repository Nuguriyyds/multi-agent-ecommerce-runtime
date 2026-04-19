from __future__ import annotations

import argparse
import json
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEMO_MESSAGE = "V3.1 演示：根据我的通勤耳机偏好，召回商品、查库存、生成首页推荐文案"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a V3 observability demo against a local server.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        session_id = _create_session(client)
        _post_message(client, session_id, DEMO_MESSAGE)
        picks = _get_picks(client, session_id)
        _post_feedback(client, session_id, picks)
        snapshot = _get_observability(client, session_id)

    runtime = snapshot["runtime"]
    feedback = snapshot["feedback"]
    capability_counts = runtime.get("capability_counts", {})
    rag_calls = capability_counts.get("rag_product_knowledge", 0)

    print("V3 MCP 观测系统自测结果")
    print(f"- session_id: {session_id}")
    print(f"- turn_count: {runtime['turn_count']}")
    print(f"- avg_turn_latency_ms: {runtime['avg_turn_latency_ms']}")
    print(f"- total_invocations: {runtime['total_invocations']}")
    print(f"- rag_product_knowledge_calls: {rag_calls}")
    print(f"- fallback_count: {runtime['fallback_count']}")
    print(f"- feedback_events: {feedback['total_events']}")
    print(f"- interest_rate: {feedback['interest_rate']}")
    print("- capability_counts:")
    for name, count in sorted(capability_counts.items()):
        print(f"  - {name}: {count}")
    print("- raw_snapshot:")
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))


def _create_session(client: httpx.Client) -> str:
    response = client.post("/api/v3/sessions", json={})
    response.raise_for_status()
    return str(response.json()["session_id"])


def _post_message(client: httpx.Client, session_id: str, message: str) -> dict[str, Any]:
    response = client.post(
        f"/api/v3/sessions/{session_id}/messages",
        json={"message": message},
    )
    response.raise_for_status()
    return response.json()


def _get_picks(client: httpx.Client, session_id: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/v3/sessions/{session_id}/personalized_picks")
    response.raise_for_status()
    return list(response.json().get("picks") or [])


def _post_feedback(
    client: httpx.Client,
    session_id: str,
    picks: list[dict[str, Any]],
) -> None:
    signals = ("interested", "not_interested")
    for index, pick in enumerate(picks[:2]):
        sku = pick.get("sku")
        if not sku:
            continue
        response = client.post(
            f"/api/v3/sessions/{session_id}/recommendation_feedback",
            json={
                "sku": sku,
                "signal": signals[index],
                "source": "self_test_script",
            },
        )
        response.raise_for_status()


def _get_observability(client: httpx.Client, session_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v3/sessions/{session_id}/observability")
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    main()
