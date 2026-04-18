from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from app.v2.api.schemas import SessionMessageRequest
from app.v2.api.session_service import V2SessionService
from app.v2.background import refresh as background_refresh
from app.v2.background import worker as background_worker
from app.v2.background.worker import V2BackgroundWorker


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_background_worker"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir()
    return path


async def _emit_projection_event(service: V2SessionService, *, user_id: str) -> str:
    session_id = service.create_session(user_id).session_id
    first = await service.handle_message(session_id, SessionMessageRequest(message="budget 3000"))
    second = await service.handle_message(session_id, SessionMessageRequest(message="phone apple gaming"))
    assert first.recommendation_refresh_triggered is False
    assert second.recommendation_refresh_triggered is True
    pending_events = service.events.list_by_status("pending")
    assert len(pending_events) == 1
    return pending_events[0].event_id


@pytest.mark.asyncio
async def test_v2_background_worker_run_once_consumes_pending_events_from_shared_db():
    tempdir = _workspace_tempdir()
    database = tempdir / "v2.sqlite3"

    producer = V2SessionService(database)
    projection_event_id = await _emit_projection_event(producer, user_id="u_bg_worker_once")

    worker_service = V2SessionService(database)
    worker = V2BackgroundWorker(worker_service, limit=10, poll_interval=0.01)
    result = await worker.run_once()

    assert projection_event_id in result.processed_event_ids
    verifier = V2SessionService(database)
    assert verifier.events.get(projection_event_id).status == "completed"
    assert verifier.snapshots.get_latest(user_id="u_bg_worker_once", scene="homepage") is not None


def test_v2_background_worker_cli_once_processes_pending_events():
    tempdir = _workspace_tempdir()
    database = tempdir / "v2.sqlite3"
    producer = V2SessionService(database)
    event_id = asyncio.run(_emit_projection_event(producer, user_id="u_bg_worker_cli"))

    script = Path("scripts") / "v2" / "background_worker.py"
    result = subprocess.run(
        [sys.executable, str(script), "--database", str(database), "--once"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert event_id in result.stdout

    verifier = V2SessionService(database)
    assert verifier.events.get(event_id).status == "completed"


@pytest.mark.asyncio
async def test_v2_background_refresh_cancellation_requeues_event(monkeypatch: pytest.MonkeyPatch):
    service = V2SessionService(_workspace_tempdir() / "v2.sqlite3")
    event_id = await _emit_projection_event(service, user_id="u_bg_worker_cancel")
    processor = service.background_processor

    async def _cancelled_process(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(processor, "_process_event_impl", _cancelled_process)

    with pytest.raises(asyncio.CancelledError):
        await processor.process_event(event_id)

    stored_event = service.events.get(event_id)
    assert stored_event is not None
    assert stored_event.status == "pending"
    assert stored_event.retry_count == 0
    assert stored_event.error is None
    assert stored_event.processed_at is None

    task = service.tasks.get("bg_" + event_id + "_attempt_1")
    assert task is not None
    assert task.status == "failed"
    assert task.error == background_refresh.INTERRUPTED_ERROR_MESSAGE


def test_v2_background_worker_main_returns_sigint_exit_code(monkeypatch: pytest.MonkeyPatch):
    def _raise_keyboard_interrupt(coroutine):
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(background_worker.asyncio, "run", _raise_keyboard_interrupt)
    assert background_worker.main([]) == background_worker.SIGINT_EXIT_CODE
