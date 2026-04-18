from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEMO_USERS = [
    "u_high_value",
    "u_price_sensitive",
    "u_churn_risk",
    "u_missing_a",
    "u_missing_b",
]
API_PATH = "/api/v1/recommend"
ALT_PYTHON_ENV = "ECOM_SMOKE_PYTHON"
BOOTSTRAP_ENV = "ECOM_SMOKE_BOOTSTRAPPED"


def _candidate_python_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def add(command: list[str]) -> None:
        key = tuple(command)
        if not command or key in seen:
            return
        seen.add(key)
        commands.append(command)

    env_python = os.environ.get(ALT_PYTHON_ENV)
    if env_python:
        add([env_python])

    add([sys.executable])

    for executable in ("python3", "py"):
        resolved = shutil.which(executable)
        if not resolved:
            continue
        if executable == "py":
            add([resolved, "-3"])
        else:
            add([resolved])

    windows_conda = Path("D:/ProgramFiles/anaconda3/python.exe")
    if windows_conda.exists():
        add([str(windows_conda)])

    return commands


def _command_supports_fastapi(command: list[str]) -> bool:
    probe = [
        *command,
        "-c",
        "import fastapi; from fastapi.testclient import TestClient",
    ]
    try:
        result = subprocess.run(
            probe,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def ensure_runtime() -> None:
    try:
        import fastapi  # noqa: F401
        from fastapi.testclient import TestClient  # noqa: F401

        return
    except Exception:  # noqa: BLE001
        pass

    if os.environ.get(BOOTSTRAP_ENV) == "1":
        raise RuntimeError(
            "smoke_test.py could not import FastAPI after switching interpreters."
        )

    current = Path(sys.executable).resolve()
    for command in _candidate_python_commands():
        candidate = Path(command[0]).resolve()
        if candidate == current:
            continue
        if not _command_supports_fastapi(command):
            continue

        env = os.environ.copy()
        env[BOOTSTRAP_ENV] = "1"
        completed = subprocess.run(
            [*command, str(PROJECT_ROOT / "smoke_test.py")],
            cwd=PROJECT_ROOT,
            env=env,
            check=False,
        )
        raise SystemExit(completed.returncode)

    raise RuntimeError(
        "smoke_test.py requires FastAPI test dependencies. "
        f"Set {ALT_PYTHON_ENV} to a Python executable that can import fastapi."
    )


def _load_app_components():
    from main import app, get_ab_test_engine, get_supervisor

    return app, get_supervisor, get_ab_test_engine


def _reset_singletons() -> None:
    _app, get_supervisor, get_ab_test_engine = _load_app_components()
    get_supervisor.cache_clear()
    get_ab_test_engine.cache_clear()


@contextmanager
def recommendation_client(*, supervisor: Any | None = None) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    app, get_supervisor, get_ab_test_engine = _load_app_components()
    _reset_singletons()
    app.dependency_overrides.clear()

    if supervisor is not None:
        app.dependency_overrides[get_supervisor] = lambda: supervisor

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        get_supervisor.cache_clear()
        get_ab_test_engine.cache_clear()


def post_recommendation(
    client: Any,
    *,
    user_id: str,
    num_items: int = 3,
) -> dict[str, Any]:
    response = client.post(
        API_PATH,
        json={
            "user_id": user_id,
            "num_items": num_items,
        },
    )
    if response.status_code != 200:
        raise AssertionError(
            f"request for {user_id} failed with status {response.status_code}: {response.text}"
        )

    payload = response.json()
    trace_id = response.headers.get("X-Trace-ID")
    if not trace_id:
        raise AssertionError(f"missing X-Trace-ID header for {user_id}")
    if payload.get("request_id") != trace_id:
        raise AssertionError(f"trace_id mismatch for {user_id}")
    return payload


def run_demo_requests() -> dict[str, dict[str, Any]]:
    responses: dict[str, dict[str, Any]] = {}
    with recommendation_client() as client:
        for user_id in DEMO_USERS:
            responses[user_id] = post_recommendation(client, user_id=user_id)
    return responses


def _copy_map(payload: dict[str, Any]) -> dict[str, str]:
    return {
        copy["product_id"]: copy["copy_text"]
        for copy in payload["copies"]
    }


def verify_copy_personalization(responses: dict[str, dict[str, Any]]) -> str:
    high_value = responses["u_high_value"]
    price_sensitive = responses["u_price_sensitive"]

    if high_value["profile"]["segments"] == price_sensitive["profile"]["segments"]:
        raise AssertionError("expected different profile segments for personalization check")

    high_value_copies = _copy_map(high_value)
    price_sensitive_copies = _copy_map(price_sensitive)
    shared_products = sorted(set(high_value_copies) & set(price_sensitive_copies))
    if not shared_products:
        raise AssertionError("expected at least one shared product across user profiles")

    product_id = shared_products[0]
    if high_value_copies[product_id] == price_sensitive_copies[product_id]:
        raise AssertionError(
            f"expected different marketing copy for shared product {product_id}"
        )

    return product_id


def build_zero_inventory_supervisor():
    from app.shared.data.inventory_store import InventoryStore
    from app.v1.agents.inventory import InventoryAgent
    from app.v1.orchestrator.supervisor import Supervisor

    return Supervisor(
        inventory_agent=InventoryAgent(
            inventory_store=InventoryStore(
                seed_inventory={
                    "sku-iphone-16-pro": 0,
                    "sku-huawei-mate-70-pro": 5,
                    "sku-airpods-pro-3": 12,
                    "sku-ipad-air-m3": 8,
                    "sku-watch-ultra-3": 7,
                    "sku-xiaomi-15": 9,
                },
            ),
        ),
    )


def verify_zero_inventory_filtering() -> str:
    target_product_id = "sku-iphone-16-pro"

    with recommendation_client(supervisor=build_zero_inventory_supervisor()) as client:
        payload = post_recommendation(client, user_id="u_high_value", num_items=3)

    returned_ids = [product["id"] for product in payload["recommendations"]]
    if target_product_id in returned_ids:
        raise AssertionError("out-of-stock product should be removed from recommendations")

    inventory_status = next(
        (
            status
            for status in payload["inventory_status"]
            if status["product_id"] == target_product_id
        ),
        None,
    )
    if inventory_status is None:
        raise AssertionError("expected out-of-stock product to remain visible in inventory status")
    if inventory_status["available"] is not False or inventory_status["stock"] != 0:
        raise AssertionError("expected zero-stock inventory status to be reported")

    return target_product_id


def verify_cold_start_hot_recommendations(responses: dict[str, dict[str, Any]]) -> list[str]:
    from app.shared.data.product_catalog import ProductCatalog

    cold_start = responses["u_missing_a"]
    if cold_start["profile"]["cold_start"] is not True:
        raise AssertionError("expected unknown user to follow cold-start path")
    if cold_start["profile"]["segments"] != ["new_user"]:
        raise AssertionError("expected cold-start user segment to be new_user")

    expected_ids = [
        product.product_id
        for product in ProductCatalog().get_fallback_products(limit=3)
    ]
    actual_ids = [product["id"] for product in cold_start["recommendations"]]
    if actual_ids != expected_ids:
        raise AssertionError(
            f"expected hot recommendations {expected_ids}, got {actual_ids}"
        )

    return actual_ids


def run_smoke_test() -> dict[str, Any]:
    responses = run_demo_requests()
    shared_product_id = verify_copy_personalization(responses)
    zero_stock_product_id = verify_zero_inventory_filtering()
    hot_recommendations = verify_cold_start_hot_recommendations(responses)

    return {
        "demo_users": DEMO_USERS,
        "segments": {
            user_id: payload["profile"]["segments"]
            for user_id, payload in responses.items()
        },
        "shared_product_for_copy_check": shared_product_id,
        "zero_stock_filtered_product": zero_stock_product_id,
        "cold_start_hot_recommendations": hot_recommendations,
    }


def main() -> int:
    ensure_runtime()
    summary = run_smoke_test()

    print("Smoke test passed.")
    print(f"Demo users: {', '.join(summary['demo_users'])}")
    print(
        "Segments: "
        + ", ".join(
            f"{user_id}={segments}"
            for user_id, segments in summary["segments"].items()
        )
    )
    print(
        "Copy personalization shared product: "
        f"{summary['shared_product_for_copy_check']}"
    )
    print(
        "Zero-stock filtered product: "
        f"{summary['zero_stock_filtered_product']}"
    )
    print(
        "Cold-start hot recommendations: "
        + ", ".join(summary["cold_start_hot_recommendations"])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
