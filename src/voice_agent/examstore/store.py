"""The exam store's database: one SQLite file at the centre.

It is the exam platform of record until results are uploaded, so every write is durable
(WAL, synchronous=FULL) and every answer and submission is kept twice: as the current
state (``answers``, ``candidates.submitted_at``) and in an append-only log of operations
(``operations``) that sync uploads and the platform can replay.

A paper must be opened before candidates can sign in to it: a candidate who sits several
papers has one examination number, and only the open papers are searched for it.
"""

import json
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from voice_agent.adapters.base import Question
from voice_agent.contract import OptionLetter
from voice_agent.examstore.package import ExamPackage, LoadedPackage, PackageCandidate
from voice_agent.examstore.versions import Version, make_version

SCHEMA_VERSION: Final = 1
_SCHEMA: Final = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exams (
    exam_id     TEXT PRIMARY KEY,
    exam_code   TEXT NOT NULL,
    package_id  TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,          -- the package, without candidates
    imported_at REAL NOT NULL,
    is_open     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id TEXT PRIMARY KEY,
    exam_id      TEXT NOT NULL REFERENCES exams (exam_id),
    exam_number  TEXT NOT NULL,
    version_id   TEXT NOT NULL UNIQUE,  -- what the engine knows this candidate's paper as
    profile      TEXT NOT NULL,
    photo        BLOB,
    version      TEXT NOT NULL,
    started_at   REAL,
    submitted_at REAL,
    UNIQUE (exam_id, exam_number)
);
CREATE TABLE IF NOT EXISTS answers (
    candidate_id TEXT NOT NULL REFERENCES candidates (candidate_id),
    question     INTEGER NOT NULL,      -- the paper's number, never the shown one
    choice       TEXT NOT NULL,         -- the paper's letter
    PRIMARY KEY (candidate_id, question)
) WITHOUT ROWID;
-- Every write, in order: idempotency, the audit trail, and what sync uploads.
CREATE TABLE IF NOT EXISTS operations (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE,
    candidate_id TEXT NOT NULL,
    kind         TEXT NOT NULL,         -- "save" or "submit"
    question     INTEGER,
    choice       TEXT,                  -- NULL when an answer is cleared
    at           REAL NOT NULL,
    uploaded     INTEGER NOT NULL DEFAULT 0
);
"""


class StoreError(Exception):
    """A refusal with a reason the caller maps to its own (not registered, submitted...)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ExamRow:
    exam_id: str
    exam_code: str
    title: str
    package: ExamPackage  # without candidates
    is_open: bool
    candidates: int


@dataclass(frozen=True, slots=True)
class CandidateRow:
    candidate_id: str
    exam_id: str
    version_id: str
    profile: PackageCandidate
    version: Version
    started_at: float | None
    submitted_at: float | None


def version_id_for(exam_id: str, candidate_id: str) -> str:
    import hashlib

    return "v:" + hashlib.sha256(f"{exam_id}\x00{candidate_id}".encode()).hexdigest()[:30]


class ExamStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)  # idempotent; it commits by itself
        with self._transaction():
            self._db.execute(
                "INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
        (found,) = self._db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if int(found) != SCHEMA_VERSION:
            raise StoreError(f"{path} has schema {found}; this version needs {SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    # --- Setting up ---------------------------------------------------------------------

    def import_package(self, loaded: LoadedPackage) -> str:
        """Load a package. The same package again changes nothing; a different package
        for an exam that already has answers is refused, so no answer is ever orphaned."""
        p = loaded.package
        body = p.model_copy(update={"candidates": ()}).model_dump_json()
        keep = frozenset(p.shuffle.keep_option_order)
        with self._transaction():
            row = self._db.execute(
                "SELECT sha256, is_open FROM exams WHERE exam_id = ?", (p.exam_id,)
            ).fetchone()
            was_open = bool(row[1]) if row else False  # a corrected package stays open
            if row is not None:
                if row[0] == loaded.sha256:
                    return "already imported"
                (answered,) = self._db.execute(
                    "SELECT COUNT(*) FROM operations o JOIN candidates c USING (candidate_id) "
                    "WHERE c.exam_id = ?",
                    (p.exam_id,),
                ).fetchone()
                if answered:
                    raise StoreError("this exam already has answers: it cannot be replaced")
                self._db.execute("DELETE FROM candidates WHERE exam_id = ?", (p.exam_id,))
                self._db.execute("DELETE FROM exams WHERE exam_id = ?", (p.exam_id,))
            self._db.execute(
                "INSERT INTO exams (exam_id, exam_code, package_id, sha256, title, body, "
                "imported_at, is_open) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    p.exam_id,
                    p.exam_code,
                    p.package_id,
                    loaded.sha256,
                    p.title,
                    body,
                    self._clock(),
                    int(was_open),
                ),
            )
            for c in p.candidates:
                version = make_version(
                    p.exam_id,
                    c.candidate_id,
                    p.outline,
                    p.questions,
                    shuffle_questions=p.shuffle.questions,
                    shuffle_options=p.shuffle.options,
                    keep_option_order=keep,
                )
                self._db.execute(
                    "INSERT INTO candidates (candidate_id, exam_id, exam_number, version_id, "
                    "profile, photo, version) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        c.candidate_id,
                        p.exam_id,
                        c.exam_number,
                        version_id_for(p.exam_id, c.candidate_id),
                        c.model_dump_json(),
                        loaded.photos.get(c.candidate_id),
                        json.dumps(version.to_json()),
                    ),
                )
        return "imported"

    def set_open(self, exam_id: str, is_open: bool) -> None:
        with self._transaction():
            done = self._db.execute(
                "UPDATE exams SET is_open = ? WHERE exam_id = ?", (int(is_open), exam_id)
            )
            if done.rowcount == 0:
                raise StoreError("no such exam")

    def exams(self) -> list[ExamRow]:
        with self._lock:
            rows = self._db.execute(
                "SELECT e.exam_id, e.exam_code, e.title, e.body, e.is_open, "
                "(SELECT COUNT(*) FROM candidates c WHERE c.exam_id = e.exam_id) "
                "FROM exams e ORDER BY e.imported_at"
            ).fetchall()
        return [
            ExamRow(r[0], r[1], r[2], ExamPackage.model_validate_json(r[3]), bool(r[4]), r[5])
            for r in rows
        ]

    # --- What the engine asks -----------------------------------------------------------

    def find_candidate(self, exam_number: str, exam_code: str | None) -> CandidateRow:
        """By examination number, among the open papers (or the one ``exam_code`` names)."""
        query = (
            "SELECT c.candidate_id FROM candidates c JOIN exams e USING (exam_id) "
            "WHERE c.exam_number = ? AND e.is_open = 1"
        )
        args: tuple[str, ...] = (exam_number,)
        if exam_code:
            query += " AND (e.exam_code = ? OR e.exam_id = ?)"
            args += (exam_code, exam_code)
        with self._lock:
            found = self._db.execute(query, args).fetchall()
        if len(found) != 1:
            # None: wrong number or no open paper. Several: two open papers share the
            # number, and guessing which would put the candidate in the wrong exam.
            raise StoreError("not_registered" if not found else "ambiguous")
        return self.candidate(found[0][0])

    def candidate(self, candidate_id: str) -> CandidateRow:
        with self._lock:
            r = self._db.execute(
                "SELECT candidate_id, exam_id, version_id, profile, version, started_at, "
                "submitted_at FROM candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        if r is None:
            raise StoreError("unknown_candidate")
        return CandidateRow(
            r[0], r[1], r[2], PackageCandidate.model_validate_json(r[3]),
            Version.from_json(json.loads(r[4])), r[5], r[6],
        )  # fmt: skip

    def candidate_by_version(self, version_id: str) -> CandidateRow:
        with self._lock:
            r = self._db.execute(
                "SELECT candidate_id FROM candidates WHERE version_id = ?", (version_id,)
            ).fetchone()
        if r is None:
            raise StoreError("no_such_exam")
        return self.candidate(r[0])

    def photo(self, candidate_id: str) -> bytes | None:
        with self._lock:
            r = self._db.execute(
                "SELECT photo FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        return bytes(r[0]) if r and r[0] is not None else None

    def exam(self, exam_id: str) -> ExamPackage:
        with self._lock:
            r = self._db.execute("SELECT body FROM exams WHERE exam_id = ?", (exam_id,)).fetchone()
        if r is None:
            raise StoreError("no_such_exam")
        return ExamPackage.model_validate_json(r[0])

    def paper_question(self, exam_id: str, number: int) -> Question:
        package = self.exam(exam_id)
        if not 1 <= number <= len(package.questions):
            raise StoreError("no_such_question")
        return package.questions[number - 1]

    def start(self, candidate_id: str) -> float:
        """When the candidate's clock started: now, the first time they sign in."""
        with self._transaction():
            self._db.execute(
                "UPDATE candidates SET started_at = ? "
                "WHERE candidate_id = ? AND started_at IS NULL",
                (self._clock(), candidate_id),
            )
            (started,) = self._db.execute(
                "SELECT started_at FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        return float(started)

    def answers(self, candidate_id: str) -> dict[int, OptionLetter]:
        """The paper's numbers and letters."""
        with self._lock:
            rows = self._db.execute(
                "SELECT question, choice FROM answers WHERE candidate_id = ?", (candidate_id,)
            ).fetchall()
        return {int(q): c for q, c in rows}

    def write(
        self,
        operation_id: str,
        candidate_id: str,
        kind: str,
        question: int | None = None,
        choice: OptionLetter | None = None,
    ) -> None:
        """Apply a save (choice None clears) or a submit, exactly once per operation ID."""
        with self._transaction():
            seen = self._db.execute(
                "SELECT candidate_id, kind, question, choice FROM operations "
                "WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if seen is not None:
                if tuple(seen) != (candidate_id, kind, question, choice):
                    raise StoreError("operation_conflict")
                return  # already applied: a retry
            found = self._db.execute(
                "SELECT submitted_at FROM candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            if found is None:
                raise StoreError("unknown_candidate")
            if found[0] is not None:
                raise StoreError("already_submitted")
            now = self._clock()
            if kind == "save":
                if choice is None:
                    self._db.execute(
                        "DELETE FROM answers WHERE candidate_id = ? AND question = ?",
                        (candidate_id, question),
                    )
                else:
                    self._db.execute(
                        "INSERT INTO answers VALUES (?, ?, ?) ON CONFLICT (candidate_id, "
                        "question) DO UPDATE SET choice = excluded.choice",
                        (candidate_id, question, choice),
                    )
            else:
                self._db.execute(
                    "UPDATE candidates SET submitted_at = ? WHERE candidate_id = ?",
                    (now, candidate_id),
                )
            self._db.execute(
                "INSERT INTO operations (operation_id, candidate_id, kind, question, choice, "
                "at) VALUES (?, ?, ?, ?, ?, ?)",
                (operation_id, candidate_id, kind, question, choice, now),
            )
