from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.shared.models.domain import MarketingCopy, Product
from app.v2.core.models import (
    ChatTurn,
    Event,
    RecommendationSnapshot,
    SessionState,
    SessionTurnRecord,
    StoredTaskRecord,
    TaskRecord,
    UserProfile,
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    memory       TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_turns (
    turn_id       TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    role          TEXT NOT NULL,
    content       TEXT NOT NULL,
    turn_number   INTEGER NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_turns_session
    ON session_turns(session_id, turn_number);

CREATE TABLE IF NOT EXISTS task_records (
    task_id        TEXT PRIMARY KEY,
    task_scope     TEXT NOT NULL,
    session_id     TEXT,
    turn_id        TEXT,
    event_id       TEXT,
    manager_name   TEXT,
    worker_name    TEXT,
    tool_name      TEXT,
    step           INTEGER,
    status         TEXT NOT NULL,
    input          TEXT NOT NULL DEFAULT '{}',
    output         TEXT,
    error          TEXT,
    latency_ms     REAL NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id               TEXT PRIMARY KEY,
    preferred_categories  TEXT NOT NULL DEFAULT '[]',
    preferred_brands      TEXT NOT NULL DEFAULT '[]',
    use_cases             TEXT NOT NULL DEFAULT '[]',
    excluded_terms        TEXT NOT NULL DEFAULT '[]',
    price_range           TEXT,
    segments              TEXT NOT NULL DEFAULT '[]',
    tags                  TEXT NOT NULL DEFAULT '[]',
    cold_start            INTEGER NOT NULL DEFAULT 0,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendation_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    scene           TEXT NOT NULL DEFAULT 'default',
    scene_context   TEXT NOT NULL DEFAULT '{}',
    products        TEXT NOT NULL,
    copies          TEXT NOT NULL DEFAULT '[]',
    generated_at    TEXT NOT NULL,
    expires_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_user_scene
    ON recommendation_snapshots(user_id, scene);

CREATE TABLE IF NOT EXISTS events (
    event_id       TEXT PRIMARY KEY,
    event_type     TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    payload        TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    retry_count    INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    created_at     TEXT NOT NULL,
    processed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_status
    ON events(status, created_at);
"""

TABLE_NAMES = (
    "events",
    "recommendation_snapshots",
    "session_turns",
    "sessions",
    "task_records",
    "user_profiles",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load_json(value: str | None, *, fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value)


def _dump_datetime(value: datetime) -> str:
    return value.isoformat()


def _load_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _resolve_database_target(database: str | Path) -> tuple[str, bool, bool]:
    if isinstance(database, Path):
        return str(database), False, False

    target = str(database)
    if target.startswith("sqlite:///"):
        target = target.removeprefix("sqlite:///")
    if target == ":memory:":
        return target, False, True
    if target.startswith("file:"):
        return target, True, "mode=memory" in target
    return target, False, False


class SQLiteDatabase:
    def __init__(self, database: str | Path) -> None:
        resolved, use_uri, in_memory = _resolve_database_target(database)
        self.database = resolved
        self._use_uri = use_uri
        self._in_memory = in_memory

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if not self._in_memory and not self.database.startswith("file:"):
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(
            self.database,
            timeout=30.0,
            uri=self._use_uri,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("PRAGMA busy_timeout = 5000;")
        if not self._in_memory:
            connection.execute("PRAGMA journal_mode = WAL;")
            connection.execute("PRAGMA synchronous = NORMAL;")

        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA_SQL)

    def journal_mode(self) -> str:
        with self.connect() as connection:
            row = connection.execute("PRAGMA journal_mode;").fetchone()
        return str(row[0])


class SessionStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save(self, session: SessionState) -> SessionState:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id,
                    user_id,
                    status,
                    memory,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    status = excluded.status,
                    memory = excluded.memory,
                    updated_at = excluded.updated_at
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.status,
                    _dump_json(session.memory),
                    _dump_datetime(session.created_at),
                    _dump_datetime(session.updated_at),
                ),
            )
        return session

    def get(self, session_id: str) -> SessionState | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT session_id, user_id, status, memory, created_at, updated_at
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return SessionState(
            session_id=row["session_id"],
            user_id=row["user_id"],
            status=row["status"],
            memory=_load_json(row["memory"], fallback={}),
            created_at=_load_datetime(row["created_at"]),
            updated_at=_load_datetime(row["updated_at"]),
        )

    def delete(self, session_id: str) -> bool:
        with self._database.connect() as connection:
            result = connection.execute(
                "DELETE FROM sessions WHERE session_id = ?",
                (session_id,),
            )
        return result.rowcount > 0


class SessionTurnStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save(self, *, turn_id: str, session_id: str, turn: ChatTurn) -> str:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO session_turns (
                    turn_id,
                    session_id,
                    role,
                    content,
                    turn_number,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    role = excluded.role,
                    content = excluded.content,
                    turn_number = excluded.turn_number,
                    created_at = excluded.created_at
                """,
                (
                    turn_id,
                    session_id,
                    turn.role,
                    turn.content,
                    turn.turn_number,
                    _dump_datetime(turn.timestamp),
                ),
            )
        return turn_id

    def get(self, turn_id: str) -> ChatTurn | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT role, content, turn_number, created_at
                FROM session_turns
                WHERE turn_id = ?
                """,
                (turn_id,),
            ).fetchone()
        if row is None:
            return None
        return ChatTurn(
            role=row["role"],
            content=row["content"],
            turn_number=row["turn_number"],
            timestamp=_load_datetime(row["created_at"]),
        )

    def list_for_session(self, session_id: str) -> list[ChatTurn]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, turn_number, created_at
                FROM session_turns
                WHERE session_id = ?
                ORDER BY turn_number ASC
                """,
                (session_id,),
            ).fetchall()
        return [
            ChatTurn(
                role=row["role"],
                content=row["content"],
                turn_number=row["turn_number"],
                timestamp=_load_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def list_records_for_session(self, session_id: str) -> list[SessionTurnRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT turn_id, session_id, role, content, turn_number, created_at
                FROM session_turns
                WHERE session_id = ?
                ORDER BY turn_number ASC, created_at ASC, turn_id ASC
                """,
                (session_id,),
            ).fetchall()
        return [
            SessionTurnRecord(
                turn_id=row["turn_id"],
                session_id=row["session_id"],
                role=row["role"],
                content=row["content"],
                turn_number=row["turn_number"],
                timestamp=_load_datetime(row["created_at"]),
            )
            for row in rows
        ]

    def delete(self, turn_id: str) -> bool:
        with self._database.connect() as connection:
            result = connection.execute(
                "DELETE FROM session_turns WHERE turn_id = ?",
                (turn_id,),
            )
        return result.rowcount > 0


class TaskRecordStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save(
        self,
        record: TaskRecord,
        *,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> TaskRecord:
        created = created_at or updated_at or _utc_now()
        updated = updated_at or created
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO task_records (
                    task_id,
                    task_scope,
                    session_id,
                    turn_id,
                    event_id,
                    manager_name,
                    worker_name,
                    tool_name,
                    step,
                    status,
                    input,
                    output,
                    error,
                    latency_ms,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    task_scope = excluded.task_scope,
                    session_id = excluded.session_id,
                    turn_id = excluded.turn_id,
                    event_id = excluded.event_id,
                    manager_name = excluded.manager_name,
                    worker_name = excluded.worker_name,
                    tool_name = excluded.tool_name,
                    step = excluded.step,
                    status = excluded.status,
                    input = excluded.input,
                    output = excluded.output,
                    error = excluded.error,
                    latency_ms = excluded.latency_ms,
                    updated_at = excluded.updated_at
                """,
                (
                    record.task_id,
                    record.task_scope,
                    record.session_id,
                    record.turn_id,
                    record.event_id,
                    record.manager_name,
                    record.worker_name,
                    record.tool_name,
                    record.step,
                    record.status,
                    _dump_json(record.input),
                    _dump_json(record.output) if record.output is not None else None,
                    record.error,
                    record.latency_ms,
                    _dump_datetime(created),
                    _dump_datetime(updated),
                ),
            )
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    task_id,
                    task_scope,
                    session_id,
                    turn_id,
                    event_id,
                    manager_name,
                    worker_name,
                    tool_name,
                    step,
                    status,
                    input,
                    output,
                    error,
                    latency_ms
                FROM task_records
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return TaskRecord(
            task_id=row["task_id"],
            task_scope=row["task_scope"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            event_id=row["event_id"],
            manager_name=row["manager_name"],
            worker_name=row["worker_name"],
            tool_name=row["tool_name"],
            step=row["step"],
            status=row["status"],
            input=_load_json(row["input"], fallback={}),
            output=_load_json(row["output"], fallback=None),
            error=row["error"],
            latency_ms=row["latency_ms"],
        )

    def list_for_turn(self, turn_id: str) -> list[StoredTaskRecord]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    task_id,
                    task_scope,
                    session_id,
                    turn_id,
                    event_id,
                    manager_name,
                    worker_name,
                    tool_name,
                    step,
                    status,
                    input,
                    output,
                    error,
                    latency_ms,
                    created_at,
                    updated_at
                FROM task_records
                WHERE turn_id = ?
                ORDER BY
                    CASE
                        WHEN worker_name IS NULL AND tool_name IS NULL THEN 0
                        WHEN worker_name IS NOT NULL AND tool_name IS NULL THEN 1
                        ELSE 2
                    END ASC,
                    COALESCE(step, 999999) ASC,
                    created_at ASC,
                    task_id ASC
                """,
                (turn_id,),
            ).fetchall()
        return [self._row_to_stored_record(row) for row in rows]

    @staticmethod
    def _row_to_stored_record(row: sqlite3.Row) -> StoredTaskRecord:
        return StoredTaskRecord(
            task_id=row["task_id"],
            task_scope=row["task_scope"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            event_id=row["event_id"],
            manager_name=row["manager_name"],
            worker_name=row["worker_name"],
            tool_name=row["tool_name"],
            step=row["step"],
            status=row["status"],
            input=_load_json(row["input"], fallback={}),
            output=_load_json(row["output"], fallback=None),
            error=row["error"],
            latency_ms=row["latency_ms"],
            created_at=_load_datetime(row["created_at"]),
            updated_at=_load_datetime(row["updated_at"]),
        )

    def delete(self, task_id: str) -> bool:
        with self._database.connect() as connection:
            result = connection.execute(
                "DELETE FROM task_records WHERE task_id = ?",
                (task_id,),
            )
        return result.rowcount > 0


class UserProfileStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save(self, profile: UserProfile, *, updated_at: datetime | None = None) -> UserProfile:
        timestamp = updated_at or _utc_now()
        price_range = list(profile.price_range) if profile.price_range is not None else None
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO user_profiles (
                    user_id,
                    preferred_categories,
                    preferred_brands,
                    use_cases,
                    excluded_terms,
                    price_range,
                    segments,
                    tags,
                    cold_start,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    preferred_categories = excluded.preferred_categories,
                    preferred_brands = excluded.preferred_brands,
                    use_cases = excluded.use_cases,
                    excluded_terms = excluded.excluded_terms,
                    price_range = excluded.price_range,
                    segments = excluded.segments,
                    tags = excluded.tags,
                    cold_start = excluded.cold_start,
                    updated_at = excluded.updated_at
                """,
                (
                    profile.user_id,
                    _dump_json(profile.preferred_categories),
                    _dump_json(profile.preferred_brands),
                    _dump_json(profile.use_cases),
                    _dump_json(profile.excluded_terms),
                    _dump_json(price_range) if price_range is not None else None,
                    _dump_json(profile.segments),
                    _dump_json(profile.tags),
                    int(profile.cold_start),
                    _dump_datetime(timestamp),
                ),
            )
        return profile

    def get(self, user_id: str) -> UserProfile | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    user_id,
                    preferred_categories,
                    preferred_brands,
                    use_cases,
                    excluded_terms,
                    price_range,
                    segments,
                    tags,
                    cold_start
                FROM user_profiles
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None

        price_range_raw = _load_json(row["price_range"], fallback=None)
        price_range = tuple(price_range_raw) if price_range_raw is not None else None
        return UserProfile(
            user_id=row["user_id"],
            preferred_categories=_load_json(row["preferred_categories"], fallback=[]),
            preferred_brands=_load_json(row["preferred_brands"], fallback=[]),
            use_cases=_load_json(row["use_cases"], fallback=[]),
            excluded_terms=_load_json(row["excluded_terms"], fallback=[]),
            price_range=price_range,
            segments=_load_json(row["segments"], fallback=[]),
            tags=_load_json(row["tags"], fallback=[]),
            cold_start=bool(row["cold_start"]),
        )

    def delete(self, user_id: str) -> bool:
        with self._database.connect() as connection:
            result = connection.execute(
                "DELETE FROM user_profiles WHERE user_id = ?",
                (user_id,),
            )
        return result.rowcount > 0


class RecommendationSnapshotStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save(self, snapshot: RecommendationSnapshot) -> RecommendationSnapshot:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO recommendation_snapshots (
                    snapshot_id,
                    user_id,
                    scene,
                    scene_context,
                    products,
                    copies,
                    generated_at,
                    expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    user_id = excluded.user_id,
                    scene = excluded.scene,
                    scene_context = excluded.scene_context,
                    products = excluded.products,
                    copies = excluded.copies,
                    generated_at = excluded.generated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.user_id,
                    snapshot.scene,
                    _dump_json(snapshot.scene_context),
                    _dump_json([product.model_dump(mode="json") for product in snapshot.products]),
                    _dump_json([copy.model_dump(mode="json") for copy in snapshot.copies]),
                    _dump_datetime(snapshot.generated_at),
                    _dump_datetime(snapshot.expires_at) if snapshot.expires_at is not None else None,
                ),
            )
        return snapshot

    def get(self, snapshot_id: str) -> RecommendationSnapshot | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    snapshot_id,
                    user_id,
                    scene,
                    scene_context,
                    products,
                    copies,
                    generated_at,
                    expires_at
                FROM recommendation_snapshots
                WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()
        return self._row_to_snapshot(row)

    def get_latest(
        self,
        *,
        user_id: str,
        scene: str,
        scene_context: dict[str, Any] | None = None,
    ) -> RecommendationSnapshot | None:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    snapshot_id,
                    user_id,
                    scene,
                    scene_context,
                    products,
                    copies,
                    generated_at,
                    expires_at
                FROM recommendation_snapshots
                WHERE user_id = ? AND scene = ?
                ORDER BY generated_at DESC, snapshot_id DESC
                """,
                (user_id, scene),
            ).fetchall()

        if scene_context is None:
            return self._row_to_snapshot(rows[0] if rows else None)

        target_context = _dump_json(scene_context)
        for row in rows:
            if _dump_json(_load_json(row["scene_context"], fallback={})) == target_context:
                return self._row_to_snapshot(row)
        return None

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row | None) -> RecommendationSnapshot | None:
        if row is None:
            return None
        return RecommendationSnapshot(
            snapshot_id=row["snapshot_id"],
            user_id=row["user_id"],
            scene=row["scene"],
            scene_context=_load_json(row["scene_context"], fallback={}),
            products=[Product.model_validate(product) for product in _load_json(row["products"], fallback=[])],
            copies=[MarketingCopy.model_validate(copy) for copy in _load_json(row["copies"], fallback=[])],
            generated_at=_load_datetime(row["generated_at"]),
            expires_at=_load_datetime(row["expires_at"]),
        )

    def delete(self, snapshot_id: str) -> bool:
        with self._database.connect() as connection:
            result = connection.execute(
                "DELETE FROM recommendation_snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            )
        return result.rowcount > 0


class EventStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save(self, event: Event) -> Event:
        with self._database.connect() as connection:
            connection.execute(
                """
                INSERT INTO events (
                    event_id,
                    event_type,
                    user_id,
                    payload,
                    status,
                    retry_count,
                    error,
                    created_at,
                    processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    event_type = excluded.event_type,
                    user_id = excluded.user_id,
                    payload = excluded.payload,
                    status = excluded.status,
                    retry_count = excluded.retry_count,
                    error = excluded.error,
                    processed_at = excluded.processed_at
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.user_id,
                    _dump_json(event.payload),
                    event.status,
                    event.retry_count,
                    event.error,
                    _dump_datetime(event.created_at),
                    _dump_datetime(event.processed_at) if event.processed_at is not None else None,
                ),
            )
        return event

    def get(self, event_id: str) -> Event | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    event_id,
                    event_type,
                    user_id,
                    payload,
                    status,
                    retry_count,
                    error,
                    created_at,
                    processed_at
                FROM events
                WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return Event(
            event_id=row["event_id"],
            event_type=row["event_type"],
            user_id=row["user_id"],
            payload=_load_json(row["payload"], fallback={}),
            status=row["status"],
            retry_count=row["retry_count"],
            error=row["error"],
            created_at=_load_datetime(row["created_at"]),
            processed_at=_load_datetime(row["processed_at"]),
        )

    def list_by_status(self, status: str, *, limit: int = 100) -> list[Event]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    event_id,
                    event_type,
                    user_id,
                    payload,
                    status,
                    retry_count,
                    error,
                    created_at,
                    processed_at
                FROM events
                WHERE status = ?
                ORDER BY created_at ASC, event_id ASC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return [
            Event(
                event_id=row["event_id"],
                event_type=row["event_type"],
                user_id=row["user_id"],
                payload=_load_json(row["payload"], fallback={}),
                status=row["status"],
                retry_count=row["retry_count"],
                error=row["error"],
                created_at=_load_datetime(row["created_at"]),
                processed_at=_load_datetime(row["processed_at"]),
            )
            for row in rows
        ]

    def list_completed_feedback_events(self, user_id: str, *, limit: int = 50) -> list[Event]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    event_id,
                    event_type,
                    user_id,
                    payload,
                    status,
                    retry_count,
                    error,
                    created_at,
                    processed_at
                FROM events
                WHERE user_id = ?
                  AND status = 'completed'
                  AND event_type IN ('click', 'skip', 'purchase')
                ORDER BY COALESCE(processed_at, created_at) DESC, event_id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [
            Event(
                event_id=row["event_id"],
                event_type=row["event_type"],
                user_id=row["user_id"],
                payload=_load_json(row["payload"], fallback={}),
                status=row["status"],
                retry_count=row["retry_count"],
                error=row["error"],
                created_at=_load_datetime(row["created_at"]),
                processed_at=_load_datetime(row["processed_at"]),
            )
            for row in rows
        ]

    def find_active_event(
        self,
        *,
        user_id: str,
        event_type: str,
        target_scene: str,
    ) -> Event | None:
        with self._database.connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    event_id,
                    event_type,
                    user_id,
                    payload,
                    status,
                    retry_count,
                    error,
                    created_at,
                    processed_at
                FROM events
                WHERE user_id = ?
                  AND event_type = ?
                  AND status IN ('pending', 'processing')
                ORDER BY created_at ASC, event_id ASC
                """,
                (user_id, event_type),
            ).fetchall()

        for row in rows:
            payload = _load_json(row["payload"], fallback={})
            scene = str(payload.get("target_scene") or payload.get("scene") or "").strip()
            if scene != target_scene:
                continue
            return Event(
                event_id=row["event_id"],
                event_type=row["event_type"],
                user_id=row["user_id"],
                payload=payload,
                status=row["status"],
                retry_count=row["retry_count"],
                error=row["error"],
                created_at=_load_datetime(row["created_at"]),
                processed_at=_load_datetime(row["processed_at"]),
            )
        return None

    def delete(self, event_id: str) -> bool:
        with self._database.connect() as connection:
            result = connection.execute(
                "DELETE FROM events WHERE event_id = ?",
                (event_id,),
            )
        return result.rowcount > 0


__all__ = [
    "EventStore",
    "RecommendationSnapshotStore",
    "SCHEMA_SQL",
    "SQLiteDatabase",
    "SessionStore",
    "SessionTurnStore",
    "TABLE_NAMES",
    "TaskRecordStore",
    "UserProfileStore",
]
