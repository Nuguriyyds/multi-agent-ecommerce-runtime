from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.shared.models.domain import MarketingCopy, Product
from app.v2.core.models import ChatTurn, Event, RecommendationSnapshot, SessionState, TaskRecord, UserProfile
from app.v2.core.persistence import (
    EventStore,
    RecommendationSnapshotStore,
    SQLiteDatabase,
    SessionStore,
    SessionTurnStore,
    TABLE_NAMES,
    TaskRecordStore,
    UserProfileStore,
)


def _workspace_tempdir() -> Path:
    base = Path(".tmp") / "test_v2_persistence"
    base.mkdir(parents=True, exist_ok=True)
    path = base / uuid4().hex
    path.mkdir()
    return path


def _build_database(tmp_path: Path) -> SQLiteDatabase:
    database = SQLiteDatabase(tmp_path / "v2.sqlite3")
    database.initialize()
    return database


def test_v2_persistence_schema_creates_all_tables_indexes_and_wal():
    tmp_path = _workspace_tempdir()
    database = _build_database(tmp_path)

    with database.connect() as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """,
            ).fetchall()
        }
        indexes = {
            row["name"]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'index' AND name NOT LIKE 'sqlite_%'
                """,
            ).fetchall()
        }
        sessions_columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        ]
        turns_columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(session_turns)").fetchall()
        ]
        task_columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(task_records)").fetchall()
        ]
        profile_columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(user_profiles)").fetchall()
        ]
        snapshot_columns = [
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(recommendation_snapshots)",
            ).fetchall()
        ]
        event_columns = [
            row["name"]
            for row in connection.execute("PRAGMA table_info(events)").fetchall()
        ]

    assert tables == set(TABLE_NAMES)
    assert indexes >= {
        "idx_events_status",
        "idx_session_turns_session",
        "idx_snapshots_user_scene",
    }
    assert database.journal_mode().lower() == "wal"
    assert sessions_columns == [
        "session_id",
        "user_id",
        "status",
        "memory",
        "created_at",
        "updated_at",
    ]
    assert turns_columns == [
        "turn_id",
        "session_id",
        "role",
        "content",
        "turn_number",
        "created_at",
    ]
    assert task_columns == [
        "task_id",
        "task_scope",
        "session_id",
        "turn_id",
        "event_id",
        "manager_name",
        "worker_name",
        "tool_name",
        "step",
        "status",
        "input",
        "output",
        "error",
        "latency_ms",
        "created_at",
        "updated_at",
    ]
    assert profile_columns == [
        "user_id",
        "preferred_categories",
        "preferred_brands",
        "use_cases",
        "excluded_terms",
        "price_range",
        "segments",
        "tags",
        "cold_start",
        "updated_at",
    ]
    assert snapshot_columns == [
        "snapshot_id",
        "user_id",
        "scene",
        "scene_context",
        "products",
        "copies",
        "generated_at",
        "expires_at",
    ]
    assert event_columns == [
        "event_id",
        "event_type",
        "user_id",
        "payload",
        "status",
        "retry_count",
        "error",
        "created_at",
        "processed_at",
    ]


def test_v2_persistence_repositories_support_minimal_crud():
    tmp_path = _workspace_tempdir()
    database = _build_database(tmp_path)
    now = datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc)

    sessions = SessionStore(database)
    turns = SessionTurnStore(database)
    tasks = TaskRecordStore(database)
    profiles = UserProfileStore(database)
    snapshots = RecommendationSnapshotStore(database)
    events = EventStore(database)

    session = SessionState(
        session_id="sess_1",
        user_id="u_1",
        memory={"budget": "3000"},
        created_at=now,
        updated_at=now,
    )
    sessions.save(session)
    assert sessions.get("sess_1") == session

    updated_session = session.model_copy(
        update={
            "memory": {"budget": "3500", "category": "phone"},
            "updated_at": now + timedelta(minutes=5),
        },
    )
    sessions.save(updated_session)
    assert sessions.get("sess_1") == updated_session

    user_turn = ChatTurn(
        role="user",
        content="预算 3000 买手机",
        turn_number=1,
        timestamp=now,
    )
    assistant_turn = ChatTurn(
        role="assistant",
        content="更偏向游戏还是拍照？",
        turn_number=2,
        timestamp=now + timedelta(seconds=10),
    )
    turns.save(turn_id="turn_1", session_id="sess_1", turn=user_turn)
    turns.save(turn_id="turn_2", session_id="sess_1", turn=assistant_turn)
    assert turns.get("turn_1") == user_turn
    assert turns.list_for_session("sess_1") == [user_turn, assistant_turn]

    pending_task = TaskRecord(
        task_id="task_1",
        task_scope="conversation",
        session_id="sess_1",
        turn_id="turn_1",
        manager_name="shopping",
        worker_name="preference_worker",
        step=1,
        status="pending",
        input={"message": "预算 3000 买手机"},
    )
    tasks.save(pending_task, created_at=now, updated_at=now)
    completed_task = pending_task.model_copy(
        update={
            "status": "completed",
            "output": {"signals": [{"category": "budget", "value": "3000"}]},
            "latency_ms": 42.5,
        },
    )
    tasks.save(
        completed_task,
        created_at=now,
        updated_at=now + timedelta(seconds=30),
    )
    assert tasks.get("task_1") == completed_task

    profile = UserProfile(
        user_id="u_1",
        preferred_categories=["phone"],
        preferred_brands=["Acme"],
        use_cases=["gaming"],
        excluded_terms=["refurbished"],
        price_range=(2500.0, 3500.0),
        segments=["price_sensitive"],
        tags=["chat_collected"],
        cold_start=False,
    )
    profiles.save(profile, updated_at=now)
    profile_update = profile.model_copy(
        update={
            "preferred_brands": ["Acme", "Nova"],
            "tags": ["chat_collected", "stable_preference"],
        },
    )
    profiles.save(profile_update, updated_at=now + timedelta(minutes=1))
    assert profiles.get("u_1") == profile_update

    snapshot = RecommendationSnapshot(
        snapshot_id="snap_1",
        user_id="u_1",
        scene="homepage",
        scene_context={"entry": "home_feed"},
        products=[
            Product(
                product_id="p_1",
                name="Phone X",
                category="phone",
                price=2999,
                brand="Acme",
            ),
        ],
        copies=[MarketingCopy(product_id="p_1", copy_text="适合预算 3000 的游戏手机")],
        generated_at=now,
        expires_at=now + timedelta(hours=4),
    )
    newer_snapshot = snapshot.model_copy(
        update={
            "snapshot_id": "snap_2",
            "generated_at": now + timedelta(minutes=15),
        },
    )
    snapshots.save(snapshot)
    snapshots.save(newer_snapshot)
    assert snapshots.get("snap_1") == snapshot
    assert snapshots.get_latest(user_id="u_1", scene="homepage") == newer_snapshot

    pending_event = Event(
        event_id="evt_1",
        event_type="recommendation_refresh",
        user_id="u_1",
        payload={"trigger": "preference_stable"},
        status="pending",
        created_at=now,
    )
    events.save(pending_event)
    completed_event = pending_event.model_copy(
        update={
            "status": "completed",
            "retry_count": 1,
            "processed_at": now + timedelta(minutes=2),
        },
    )
    events.save(completed_event)
    assert events.get("evt_1") == completed_event
    assert events.list_by_status("completed") == [completed_event]

    assert turns.delete("turn_2") is True
    assert events.delete("evt_1") is True
    assert snapshots.delete("snap_2") is True
    assert profiles.delete("u_1") is True
    assert tasks.delete("task_1") is True
    assert sessions.delete("sess_1") is True


def test_v2_persistence_state_survives_database_reopen():
    tmp_path = _workspace_tempdir()
    db_path = tmp_path / "v2.sqlite3"
    first_boot = SQLiteDatabase(db_path)
    first_boot.initialize()

    now = datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc)
    SessionStore(first_boot).save(
        SessionState(
            session_id="sess_recover",
            user_id="u_recover",
            memory={"use_case": "commute"},
            created_at=now,
            updated_at=now,
        ),
    )
    SessionTurnStore(first_boot).save(
        turn_id="turn_recover",
        session_id="sess_recover",
        turn=ChatTurn(
            role="user",
            content="通勤用，轻一点",
            turn_number=1,
            timestamp=now,
        ),
    )
    TaskRecordStore(first_boot).save(
        TaskRecord(
            task_id="task_recover",
            task_scope="background",
            event_id="evt_recover",
            manager_name="shopping",
            worker_name="catalog_worker",
            step=1,
            status="running",
            input={"scene": "homepage"},
        ),
        created_at=now,
        updated_at=now,
    )
    UserProfileStore(first_boot).save(
        UserProfile(
            user_id="u_recover",
            preferred_categories=["laptop"],
            use_cases=["commute"],
            cold_start=False,
        ),
        updated_at=now,
    )
    RecommendationSnapshotStore(first_boot).save(
        RecommendationSnapshot(
            snapshot_id="snap_recover",
            user_id="u_recover",
            scene="default",
            products=[
                Product(
                    product_id="p_light",
                    name="LightBook",
                    category="laptop",
                    price=4999,
                ),
            ],
            generated_at=now,
        ),
    )
    EventStore(first_boot).save(
        Event(
            event_id="evt_recover",
            event_type="recommendation_refresh",
            user_id="u_recover",
            payload={"source": "restart_test"},
            status="pending",
            created_at=now,
        ),
    )

    second_boot = SQLiteDatabase(db_path)
    second_boot.initialize()

    assert SessionStore(second_boot).get("sess_recover") is not None
    assert SessionTurnStore(second_boot).get("turn_recover") is not None
    assert TaskRecordStore(second_boot).get("task_recover") is not None
    assert UserProfileStore(second_boot).get("u_recover") is not None
    assert RecommendationSnapshotStore(second_boot).get("snap_recover") is not None
    assert EventStore(second_boot).get("evt_recover") is not None

    with sqlite3.connect(db_path) as connection:
        task_timestamps = connection.execute(
            """
            SELECT created_at, updated_at
            FROM task_records
            WHERE task_id = 'task_recover'
            """,
        ).fetchone()

    assert task_timestamps is not None
    assert task_timestamps[0]
    assert task_timestamps[1]
