import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from voice_agent.session import machine as m
from voice_agent.session.events import SessionRecord, SessionStore
from voice_agent.session.outbox import OutboxItem
from voice_agent.understanding import intents as i

STARTED = m.Started(
    session_id="s-1",
    candidate_id="c-1",
    exam_id="e-1",
    question_count=10,
    sections=(),
    duration_s=600,
)
RECORD = SessionRecord("s-1", "c-1", "e-1", created_at=100.0)
NEXT = m.Understood(intent=i.Next(), source=m.Source.RULES, text="next")


def item(op: str, question: int = 1) -> OutboxItem:
    return OutboxItem(op, "s-1", "c-1", "save", question, "A")


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[SessionStore]:
    s = await SessionStore.open(tmp_path / "sessions.db", clock=lambda: 123.0)
    yield s
    await s.close()


async def test_durability_settings(tmp_path: Path, store: SessionStore) -> None:
    db = sqlite3.connect(tmp_path / "sessions.db")
    assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    db.close()
    sync = await store._run(lambda: store._conn.execute("PRAGMA synchronous").fetchone())
    assert sync == (2,)  # FULL


async def test_events_round_trip(store: SessionStore) -> None:
    await store.create_session(RECORD, STARTED)
    await store.append("s-1", 2, NEXT)
    stored = await store.load("s-1")
    assert [(e.seq, e.at, e.event) for e in stored] == [(1, 123.0, STARTED), (2, 123.0, NEXT)]


async def test_a_reused_sequence_number_fails_and_writes_nothing(store: SessionStore) -> None:
    await store.create_session(RECORD, STARTED)
    with pytest.raises(sqlite3.IntegrityError):
        await store.append("s-1", 1, NEXT, enqueue=[item("op-1")])
    assert len(await store.load("s-1")) == 1
    assert await store.pending_writes() == []


async def test_sessions_are_found_by_id_and_by_candidate(store: SessionStore) -> None:
    await store.create_session(RECORD, STARTED)
    later = SessionRecord("s-2", "c-1", "e-1", created_at=200.0)
    await store.create_session(later, STARTED.model_copy(update={"session_id": "s-2"}))
    assert await store.get_session("s-1") == RECORD
    assert await store.get_session("nope") is None
    assert await store.find_session("c-1", "e-1") == later
    assert await store.find_session("c-1", "other") is None


async def test_outbox_keeps_confirmation_order_until_settled(store: SessionStore) -> None:
    await store.create_session(RECORD, STARTED)
    await store.append("s-1", 2, NEXT, enqueue=[item("op-b", 2)])
    await store.append("s-1", 3, NEXT, enqueue=[item("op-a", 1)])
    assert [w.operation_id for w in await store.pending_writes()] == ["op-b", "op-a"]

    await store.record_attempt("op-b")
    await store.record_attempt("op-b")
    assert (await store.pending_writes("s-1"))[0].attempts == 2

    outcome = m.WriteOutcome(operation_id="op-b", outcome="saved")
    await store.append("s-1", 4, outcome, settle=("op-b", "saved"))
    assert [w.operation_id for w in await store.pending_writes("s-1")] == ["op-a"]
    assert await store.pending_writes("someone-else") == []


async def test_everything_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "sessions.db"
    first = await SessionStore.open(path)
    await first.create_session(RECORD, STARTED)
    await first.append("s-1", 2, NEXT, enqueue=[item("op-1")])
    await first.close()

    second = await SessionStore.open(path)
    assert len(await second.load("s-1")) == 2
    assert [w.operation_id for w in await second.pending_writes()] == ["op-1"]
    await second.close()
