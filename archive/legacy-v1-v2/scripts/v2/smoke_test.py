from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SMOKE_USER_ID = "u_v2_smoke"
CREATE_SESSION_PATH = "/api/v2/sessions"
MESSAGE_PATH = "/api/v2/sessions/{session_id}/messages"
TRACE_PATH = "/api/v2/sessions/{session_id}/turns/{user_turn_number}/trace"
RECOMMENDATION_PATH = "/api/v2/users/{user_id}/recommendations"
FEEDBACK_PATH = "/api/v2/users/{user_id}/feedback-events"
ALT_PYTHON_ENV = "ECOM_SMOKE_V2_PYTHON"
BOOTSTRAP_ENV = "ECOM_SMOKE_V2_BOOTSTRAPPED"
SMOKE_TRACE_IDS = {
    "create_session": "trace-v2-smoke-create",
    "message_turn_1": "trace-v2-smoke-message-1",
    "message_turn_2": "trace-v2-smoke-message-2",
    "trace_turn_2": "trace-v2-smoke-trace-2",
    "read_homepage": "trace-v2-smoke-read-homepage",
    "feedback": "trace-v2-smoke-feedback",
}


def _candidate_python_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def add(command: list[str]) -> None:
        key = tuple(command)
        if command and key not in seen:
            seen.add(key)
            commands.append(command)

    env_python = os.environ.get(ALT_PYTHON_ENV)
    if env_python:
        add([env_python])
    add([sys.executable])
    for executable in ("python3", "py"):
        resolved = shutil.which(executable)
        if resolved:
            add([resolved, "-3"] if executable == "py" else [resolved])
    windows_conda = Path("D:/ProgramFiles/anaconda3/python.exe")
    if windows_conda.exists():
        add([str(windows_conda)])
    return commands


def _command_supports_fastapi(command: list[str]) -> bool:
    probe = [*command, "-c", "import fastapi; from fastapi.testclient import TestClient"]
    try:
        result = subprocess.run(probe, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20, check=False)
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
        raise RuntimeError("smoke_test_v2.py could not import FastAPI after switching interpreters.")

    current = Path(sys.executable).resolve()
    for command in _candidate_python_commands():
        candidate = Path(command[0]).resolve()
        if candidate == current or not _command_supports_fastapi(command):
            continue
        env = os.environ.copy()
        env[BOOTSTRAP_ENV] = "1"
        completed = subprocess.run([*command, str(PROJECT_ROOT / "smoke_test_v2.py")], cwd=PROJECT_ROOT, env=env, check=False)
        raise SystemExit(completed.returncode)

    raise RuntimeError(
        "smoke_test_v2.py requires FastAPI test dependencies. "
        f"Set {ALT_PYTHON_ENV} to a Python executable that can import fastapi."
    )


def _load_app_components():
    from app.v2.api.session_service import V2SessionService
    from main import app, get_v2_session_service

    return app, get_v2_session_service, V2SessionService


@contextmanager
def v2_client() -> Iterator[tuple[Any, Any]]:
    from fastapi.testclient import TestClient

    app, get_v2_session_service, service_cls = _load_app_components()
    get_v2_session_service.cache_clear()
    app.dependency_overrides.clear()

    tempdir = PROJECT_ROOT / ".tmp" / "smoke_test_v2" / uuid4().hex
    tempdir.mkdir(parents=True, exist_ok=True)
    service = service_cls(tempdir / "v2.sqlite3")
    app.dependency_overrides[get_v2_session_service] = lambda: service

    try:
        with TestClient(app) as client:
            yield client, service
    finally:
        app.dependency_overrides.clear()
        get_v2_session_service.cache_clear()


def _parse_response(response: Any, *, label: str) -> tuple[dict[str, Any], str]:
    if response.status_code != 200:
        raise AssertionError(f"{label} failed with status {response.status_code}: {response.text}")
    trace_id = response.headers.get("X-Trace-ID")
    if not trace_id:
        raise AssertionError(f"{label} missing X-Trace-ID header")
    return response.json(), trace_id


def _collect_task_counts(service: Any) -> dict[str, int]:
    with service.database.connect() as connection:
        rows = connection.execute(
            """
            SELECT task_scope, status, COUNT(*) AS task_count
            FROM task_records
            GROUP BY task_scope, status
            ORDER BY task_scope ASC, status ASC
            """
        ).fetchall()
    return {f"{row['task_scope']}:{row['status']}": int(row["task_count"]) for row in rows}


def _collect_snapshot_counts(service: Any) -> dict[str, int]:
    with service.database.connect() as connection:
        rows = connection.execute(
            """
            SELECT scene, COUNT(*) AS snapshot_count
            FROM recommendation_snapshots
            GROUP BY scene
            ORDER BY scene ASC
            """
        ).fetchall()
    return {str(row["scene"]): int(row["snapshot_count"]) for row in rows}


def _collect_event_types(service: Any, *, status: str) -> list[str]:
    return [event.event_type for event in service.events.list_by_status(status)]


def run_smoke_test_v2() -> dict[str, Any]:
    observed_trace_ids: dict[str, str] = {}

    with v2_client() as (client, service):
        create_payload, observed_trace_ids["create_session"] = _parse_response(
            client.post(CREATE_SESSION_PATH, headers={"X-Trace-ID": SMOKE_TRACE_IDS["create_session"]}, json={"user_id": SMOKE_USER_ID}),
            label="create_session",
        )
        session_id = create_payload["session_id"]

        first_turn, observed_trace_ids["message_turn_1"] = _parse_response(
            client.post(MESSAGE_PATH.format(session_id=session_id), headers={"X-Trace-ID": SMOKE_TRACE_IDS["message_turn_1"]}, json={"message": "budget 3000"}),
            label="message_turn_1",
        )
        if first_turn["recommendation_refresh_triggered"]:
            raise AssertionError("message_turn_1 should not request projection")

        second_turn, observed_trace_ids["message_turn_2"] = _parse_response(
            client.post(MESSAGE_PATH.format(session_id=session_id), headers={"X-Trace-ID": SMOKE_TRACE_IDS["message_turn_2"]}, json={"message": "phone apple gaming"}),
            label="message_turn_2",
        )
        if not second_turn["recommendation_refresh_triggered"]:
            raise AssertionError("message_turn_2 should request projection")
        if second_turn["products"] or second_turn["copies"]:
            raise AssertionError("message_turn_2 should stay advisory in V2.2")

        trace_payload, observed_trace_ids["trace_turn_2"] = _parse_response(
            client.get(TRACE_PATH.format(session_id=session_id, user_turn_number=2), headers={"X-Trace-ID": SMOKE_TRACE_IDS["trace_turn_2"]}),
            label="trace_turn_2",
        )
        if trace_payload["projection"]["event_type"] != "profile_projection":
            raise AssertionError("trace_turn_2 should expose profile projection")

        processed_events = asyncio.run(service.process_background_events())
        processed_event_ids = [event.event_id for event in processed_events]
        if len(processed_events) != 2:
            raise AssertionError("projection chain should process profile_projection and recommendation_refresh")

        homepage_payload, observed_trace_ids["read_homepage"] = _parse_response(
            client.get(RECOMMENDATION_PATH.format(user_id=SMOKE_USER_ID), headers={"X-Trace-ID": SMOKE_TRACE_IDS["read_homepage"]}, params={"scene": "homepage"}),
            label="read_homepage",
        )
        homepage_product_ids = [product["product_id"] for product in homepage_payload["products"]]
        if not homepage_product_ids:
            raise AssertionError("homepage read should return products after background projection")
        anchor_product_id = homepage_product_ids[0]

        feedback_payload, observed_trace_ids["feedback"] = _parse_response(
            client.post(
                FEEDBACK_PATH.format(user_id=SMOKE_USER_ID),
                headers={"X-Trace-ID": SMOKE_TRACE_IDS["feedback"]},
                json={"event_type": "click", "scene": "homepage", "product_id": anchor_product_id, "metadata": {"position": 1}},
            ),
            label="feedback",
        )
        feedback_background_events = asyncio.run(service.process_background_events())
        if len(feedback_background_events) != 1:
            raise AssertionError("feedback should enqueue one homepage refresh")

        session = service.sessions.get(session_id)
        profile = service.user_profiles.get(SMOKE_USER_ID)
        turns = service.turns.list_for_session(session_id)
        if session is None or profile is None:
            raise AssertionError("expected session and profile to exist after smoke flow")

    task_counts = _collect_task_counts(service)
    return {
        "user_id": SMOKE_USER_ID,
        "session_id": session_id,
        "observed_trace_ids": observed_trace_ids,
        "turn_count": len(turns),
        "processed_background_event_ids": processed_event_ids,
        "task_counts": task_counts,
        "background_task_count": task_counts.get("background:completed", 0),
        "snapshot_counts": _collect_snapshot_counts(service),
        "completed_event_types": _collect_event_types(service, status="completed"),
        "pending_event_types_after_feedback": _collect_event_types(service, status="pending"),
        "homepage_product_ids": homepage_product_ids,
        "feedback_event_id": feedback_payload["event_id"],
        "trace_turn_2_terminal_state": trace_payload["terminal_state"],
        "trace_turn_2_projection_event_type": trace_payload["projection"]["event_type"],
        "last_terminal_state": session.memory["last_terminal_state"],
        "last_projection_trigger": session.memory["last_projection_trigger"],
        "profile_categories": list(profile.preferred_categories),
    }


def main() -> int:
    ensure_runtime()
    summary = run_smoke_test_v2()
    print("V2 smoke test passed.")
    print(f"User: {summary['user_id']}")
    print(f"Session: {summary['session_id']}")
    print("Observed trace ids: " + ", ".join(f"{label}={trace_id}" for label, trace_id in summary["observed_trace_ids"].items()))
    print("Processed background events: " + ", ".join(summary["processed_background_event_ids"]))
    print("Snapshots: " + ", ".join(f"{scene}={count}" for scene, count in summary["snapshot_counts"].items()))
    print("Task counts: " + ", ".join(f"{key}={count}" for key, count in summary["task_counts"].items()))
    print("Homepage products: " + ", ".join(summary["homepage_product_ids"]))
    print(f"Feedback event: {summary['feedback_event_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
