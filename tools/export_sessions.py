"""Write each test session as a readable transcript, for testers' feedback.

    python -m tools.export_sessions --data data --out feedback

One text file per session: what the candidate said (as recognised, with the
recogniser's score) or pressed, what the service understood and how, and what it said back.
The replies are rebuilt by replaying the session log through the state machine, so they
are the machine's words; notices about the service's own state (keyboard mode, "one
moment") are not in the log and do not appear. Run it with the service stopped.

Only for the fake platform's fictional exams: a transcript quotes the exam text.
"""

import argparse
import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from voice_agent.adapters.base import ExamDataAdapter, Question
from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.session import machine as m
from voice_agent.session import replies as r

_events: TypeAdapter[m.Event] = TypeAdapter(m.Event)


async def transcript(rows: list[tuple[float, str]], adapter: ExamDataAdapter) -> list[str]:
    """The lines for one session, from its (time, event JSON) rows in order."""
    events = [(at, _events.validate_json(body)) for at, body in rows]
    first_at, started = events[0]
    if not isinstance(started, m.Started):
        raise ValueError("an event log must begin with Started")
    began = datetime.fromtimestamp(first_at, UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Session {started.session_id}: exam {started.exam_id}, started {began}", ""]
    state, effects = m.start(started)
    lines += await _said(effects, started.exam_id, adapter)
    for at, event in events[1:]:
        clock = _clock(at - first_at)
        if isinstance(event, m.Understood):
            lines += ["", f"{clock}  CANDIDATE {_heard(event)}"]
            lines.append(f"        understood: {_intent(event)}")
        elif isinstance(event, m.TimeWarning | m.TimeUp | m.Resumed):
            lines += ["", f"{clock}  ({event.kind.replace('_', ' ')})"]
        elif isinstance(event, m.WriteOutcome) and event.outcome == "rejected":
            lines.append(f"        (the platform rejected a save: {event.reason})")
        state, effects = m.step(state, event)
        lines += await _said(effects, started.exam_id, adapter)
    return lines


def _heard(event: m.Understood) -> str:
    if event.source is m.Source.KEY:
        return f"pressed {event.text}"
    if event.confidence is None:
        return f"typed: {event.text!r}"
    # The recogniser's own score: a rough guide, not a measured accuracy.
    return f"said (recognition score {event.confidence:.2f}): {event.text!r}"


def _intent(event: m.Understood) -> str:
    args = event.intent.args.model_dump(exclude_none=True)
    words = " ".join([event.intent.action, *(f"{k}={v}" for k, v in args.items())])
    how: list[str] = [event.source]
    if event.fallback:
        how.append(f"model not used: {event.fallback}")
    if event.latency_ms is not None:
        how.append(f"{event.latency_ms} ms")
    return f"{words}  [{', '.join(how)}]"


async def _said(effects: tuple[m.Effect, ...], exam_id: str, adapter: ExamDataAdapter) -> list[str]:
    lines = []
    for effect in effects:
        if not isinstance(effect, m.Speak):
            continue
        n = r.needs_question(effect.reply)
        question: Question | None = None
        if n is not None:
            try:
                question = await adapter.get_question(exam_id, n)
            except Exception:  # not in the fixtures: say so rather than stop the export
                lines.append(f"        SERVICE: [question {n}, not in the test fixtures]")
                continue
        for spoken in r.render(effect.reply, question):
            lines.append(f"        SERVICE: {spoken.text}")
    return lines


def _clock(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


async def export(data_dir: Path, out: Path) -> list[Path]:
    db = data_dir / "sessions.db"
    if not db.is_file():
        raise SystemExit(f"no sessions yet: {db} does not exist")
    # closing(): a connection's own "with" only commits, and an open one locks the file.
    with closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)) as conn:
        sessions = conn.execute("SELECT session_id FROM sessions ORDER BY created_at").fetchall()
        logs = {
            sid: conn.execute(
                "SELECT at, body FROM events WHERE session_id = ? ORDER BY seq", (sid,)
            ).fetchall()
            for (sid,) in sessions
        }
    adapter = FakeAdapter.with_bundled_fixtures()
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}  # file name -> session id
    for sid, rows in logs.items():
        if not rows:
            continue
        path = out / f"session-{len(written) + 1:02d}.txt"
        path.write_text("\n".join(await transcript(rows, adapter)) + "\n", encoding="utf-8")
        written[path.name] = sid
    (out / "sessions.json").write_text(json.dumps(written, indent=2), encoding="utf-8")
    return [out / name for name in written]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("feedback"))
    args = parser.parse_args()
    written = asyncio.run(export(args.data, args.out))
    print(f"{len(written)} session transcripts in {args.out}")


if __name__ == "__main__":
    main()
