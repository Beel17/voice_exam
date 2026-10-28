"""The session store: each session's append-only event log, and the outbox, in SQLite.

State is never stored; it is ``machine.replay`` of the log, so the audit trail and the
state cannot disagree. The log holds events only: question numbers and option letters,
never question text and never an answer key.

SQLite runs in WAL mode with ``synchronous=FULL``, so a commit is on disk before the
candidate hears "Saved", even through a power cut. All database work runs on one
dedicated thread, keeping the event loop free and the connection single-threaded.
"""

import asyncio
import sqlite3
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import TypeAdapter

from voice_agent.session.machine import Event, Started
from voice_agent.session.outbox import OutboxItem

_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    exam_id      TEXT NOT NULL,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_by_candidate ON sessions (candidate_id, exam_id);

CREATE TABLE IF NOT EXISTS events (
    session_id TEXT    NOT NULL REFERENCES sessions (session_id),
    seq        INTEGER NOT NULL,
    at         REAL    NOT NULL,
    kind       TEXT    NOT NULL,
    body       TEXT    NOT NULL,
    PRIMARY KEY (session_id, seq)
) WITHOUT ROWID;

-- rowid order is confirmation order, which is delivery order.
CREATE TABLE IF NOT EXISTS outbox (
    operation_id TEXT PRIMARY KEY,
    session_id   TEXT    NOT NULL REFERENCES sessions (session_id),
    candidate_id TEXT    NOT NULL,
    kind         TEXT    NOT NULL,
    question     INTEGER,
    choice       TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    status       TEXT    NOT NULL DEFAULT 'pending'
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox (status, session_id);
"""

_events: TypeAdapter[Event] = TypeAdapter(Event)


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    candidate_id: str
    exam_id: str
    created_at: float


@dataclass(frozen=True, slots=True)
class StoredEvent:
    seq: int
    at: float
    event: Event


Settled = tuple[str, Literal["saved", "rejected"]]


class SessionStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-store")
        self._db: sqlite3.Connection | None = None

    @classmethod
    async def open(cls, path: Path, clock: Callable[[], float] = time.time) -> Self:
        store = cls(path, clock)
        await store._run(store._connect)
        return store

    async def close(self) -> None:
        await self._run(self._disconnect)
        self._thread.shutdown(wait=True)

    async def _run[T](self, fn: Callable[[], T]) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._thread, fn)

    @property
    def _conn(self) -> sqlite3.Connection:
        assert self._db is not None, "store is not open"
        return self._db

    # --- Public, async ----------------------------------------------------------------

    async def create_session(self, record: SessionRecord, started: Started) -> None:
        """Create a session and its first event in one transaction."""
        await self._run(partial(self._create_session, record, started))

    async def append(
        self,
        session_id: str,
        seq: int,
        event: Event,
        enqueue: Sequence[OutboxItem] = (),
        settle: Settled | None = None,
    ) -> None:
        """Append one event, with the outbox changes it causes, atomically."""
        await self._run(partial(self._append, session_id, seq, event, enqueue, settle))

    async def load(self, session_id: str) -> list[StoredEvent]:
        return await self._run(partial(self._load, session_id))

    async def get_session(self, session_id: str) -> SessionRecord | None:
        return await self._run(partial(self._get_session, session_id))

    async def find_session(self, candidate_id: str, exam_id: str) -> SessionRecord | None:
        """The candidate's session for this exam, if one was ever started."""
        return await self._run(partial(self._find_session, candidate_id, exam_id))

    async def pending_writes(self, session_id: str | None = None) -> list[OutboxItem]:
        """Undelivered writes in delivery order, for one session or all."""
        return await self._run(partial(self._pending_writes, session_id))

    async def record_attempt(self, operation_id: str) -> None:
        await self._run(partial(self._record_attempt, operation_id))

    # --- On the store thread ----------------------------------------------------------

    def _connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript(_SCHEMA)
        self._db = db

    def _disconnect(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    def _create_session(self, record: SessionRecord, started: Started) -> None:
        db = self._conn
        with _transaction(db):
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?)",
                (record.session_id, record.candidate_id, record.exam_id, record.created_at),
            )
            self._insert_event(record.session_id, 1, started)

    def _append(
        self,
        session_id: str,
        seq: int,
        event: Event,
        enqueue: Sequence[OutboxItem],
        settle: Settled | None,
    ) -> None:
        db = self._conn
        with _transaction(db):
            self._insert_event(session_id, seq, event)
            db.executemany(
                "INSERT INTO outbox"
                " (operation_id, session_id, candidate_id, kind, question, choice)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (i.operation_id, i.session_id, i.candidate_id, i.kind, i.question, i.choice)
                    for i in enqueue
                ],
            )
            if settle is not None:
                operation_id, status = settle
                db.execute(
                    "UPDATE outbox SET status = ? WHERE operation_id = ?", (status, operation_id)
                )

    def _insert_event(self, session_id: str, seq: int, event: Event) -> None:
        # The primary key rejects a reused seq: two writers on one session is a bug, and
        # failing loudly beats a log that replays differently from what happened.
        self._conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (session_id, seq, self._clock(), event.kind, _events.dump_json(event).decode()),
        )

    def _load(self, session_id: str) -> list[StoredEvent]:
        rows = self._conn.execute(
            "SELECT seq, at, body FROM events WHERE session_id = ? ORDER BY seq", (session_id,)
        )
        return [StoredEvent(seq, at, _events.validate_json(body)) for seq, at, body in rows]

    def _get_session(self, session_id: str) -> SessionRecord | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return SessionRecord(*row) if row else None

    def _find_session(self, candidate_id: str, exam_id: str) -> SessionRecord | None:
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE candidate_id = ? AND exam_id = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (candidate_id, exam_id),
        ).fetchone()
        return SessionRecord(*row) if row else None

    def _pending_writes(self, session_id: str | None) -> list[OutboxItem]:
        query = (
            "SELECT operation_id, session_id, candidate_id, kind, question, choice, attempts"
            " FROM outbox WHERE status = 'pending'"
        )
        params: tuple[str, ...] = ()
        if session_id is not None:
            query += " AND session_id = ?"
            params = (session_id,)
        rows = self._conn.execute(query + " ORDER BY rowid", params)
        return [OutboxItem(*row) for row in rows]

    def _record_attempt(self, operation_id: str) -> None:
        self._conn.execute(
            "UPDATE outbox SET attempts = attempts + 1 WHERE operation_id = ?", (operation_id,)
        )


@contextmanager
def _transaction(db: sqlite3.Connection) -> Iterator[None]:
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.execute("ROLLBACK")
        raise
    db.execute("COMMIT")
