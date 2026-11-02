"""An in-memory exam platform for development, demos and tests.

It follows the adapter contract exactly (the conformance suite runs against it) and can
also fail the ways a real platform does, so outbox and resume logic can be tested.

With a state folder it behaves like a platform on another machine: what it has saved
outlives the service (a service killed mid-exam must find its saves still there), and
two files there switch failures on from outside, for drills: ``platform-down`` makes
every call fail, and ``platform-slow`` (holding a number of seconds) delays every call.
"""

import asyncio
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from voice_agent.adapters.base import (
    AdapterRejected,
    AdapterUnavailable,
    CandidateSession,
    ExamOutline,
    Question,
    RejectReason,
    SavedAnswers,
)
from voice_agent.contract import Identifier, OptionLetter

BUNDLED_FIXTURES = ("sample_60", "injection")


class FixtureCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    reg_no: Identifier
    candidate_id: Identifier


class FixtureExam(BaseModel):
    """One exam as stored in ``fixtures/*.json``. Holds no answer key, by design."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    exam_code: Identifier
    exam_id: Identifier
    candidates: tuple[FixtureCandidate, ...]
    outline: ExamOutline
    questions: tuple[Question, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.outline.exam_id != self.exam_id:
            raise ValueError("outline.exam_id must match exam_id")
        numbers = [q.number for q in self.questions]
        if numbers != list(range(1, self.outline.question_count + 1)):
            raise ValueError("questions must be numbered 1..question_count in order")
        return self


def load_fixture(name: str) -> FixtureExam:
    path = resources.files("voice_agent.adapters").joinpath("fixtures", f"{name}.json")
    return FixtureExam.model_validate_json(path.read_text(encoding="utf-8"))


_SaveOp = tuple[str, int, OptionLetter | None]
_SubmitOp = tuple[str]


@dataclass
class _Candidate:
    exam: FixtureExam
    started_at: float | None = None
    answers: dict[int, OptionLetter] = field(default_factory=dict)
    submitted: bool = False
    # Every applied write by operation ID, so a repeat is recognised and not re-applied.
    operations: dict[str, _SaveOp | _SubmitOp] = field(default_factory=dict)


class FakeAdapter:
    def __init__(
        self,
        exams: Iterable[FixtureExam],
        clock: Callable[[], float] = time.monotonic,
        state_dir: Path | None = None,
    ) -> None:
        # Saved state outlives this process, so its times must too: wall-clock time.
        self._clock = time.time if state_dir is not None and clock is time.monotonic else clock
        self._state_dir = state_dir
        self._exams_by_code: dict[str, FixtureExam] = {}
        self._exams_by_id: dict[str, FixtureExam] = {}
        self._registrations: dict[tuple[str, str], str] = {}
        self._candidates: dict[str, _Candidate] = {}
        for exam in exams:
            self._exams_by_code[exam.exam_code] = exam
            self._exams_by_id[exam.exam_id] = exam
            for c in exam.candidates:
                if c.candidate_id in self._candidates:
                    raise ValueError(f"duplicate candidate_id {c.candidate_id}")
                self._registrations[(c.reg_no, exam.exam_code)] = c.candidate_id
                self._candidates[c.candidate_id] = _Candidate(exam)

        # Failure injection. ``unavailable`` fails every call before it does anything;
        # ``lose_next_acks`` applies the next N writes and then fails, as when the
        # platform saved but the reply was lost on the network.
        self.unavailable = False
        self.lose_next_acks = 0
        if state_dir is not None:
            state_dir.mkdir(parents=True, exist_ok=True)
            self._load()

    @classmethod
    def with_bundled_fixtures(
        cls,
        clock: Callable[[], float] = time.monotonic,
        load_candidates: int = 0,
        state_dir: Path | None = None,
    ) -> Self:
        """The bundled exams. ``load_candidates`` registers that many more candidates for
        the sample exam (reg_no ``LOAD00001`` and up), for load tests and benchmarks."""
        exams = [load_fixture(name) for name in BUNDLED_FIXTURES]
        if load_candidates:
            sample = exams[0]
            extra = tuple(
                FixtureCandidate(reg_no=f"LOAD{k:05d}", candidate_id=f"load-{k:05d}")
                for k in range(1, load_candidates + 1)
            )
            exams[0] = sample.model_copy(update={"candidates": sample.candidates + extra})
        return cls(exams, clock, state_dir)

    async def _enter(self) -> None:
        """Every call starts here: the failure switches, set in code or from outside."""
        if self.unavailable:
            raise AdapterUnavailable("fake platform is down")
        if self._state_dir is None:
            return
        if (self._state_dir / "platform-down").exists():
            raise AdapterUnavailable("fake platform is down (platform-down)")
        slow = self._state_dir / "platform-slow"
        if slow.exists():
            await asyncio.sleep(float(slow.read_text(encoding="ascii").strip() or 0))

    def _load(self) -> None:
        assert self._state_dir is not None
        path = self._state_dir / "platform.json"
        if not path.exists():
            return
        for candidate_id, saved in json.loads(path.read_text(encoding="utf-8")).items():
            if (c := self._candidates.get(candidate_id)) is None:
                continue
            c.started_at = saved["started_at"]
            c.answers = {int(n): letter for n, letter in saved["answers"].items()}
            c.submitted = saved["submitted"]
            c.operations = {op: tuple(v) for op, v in saved["operations"].items()}

    def _store(self) -> None:
        """Write what the platform holds, atomically: a kill mid-write leaves the old copy."""
        if self._state_dir is None:
            return
        state = {
            candidate_id: {
                "started_at": c.started_at,
                "answers": c.answers,
                "submitted": c.submitted,
                "operations": c.operations,
            }
            for candidate_id, c in self._candidates.items()
            if c.started_at is not None
        }
        temporary = self._state_dir / "platform.json.tmp"
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(self._state_dir / "platform.json")

    def _ack(self) -> None:
        if self.lose_next_acks > 0:
            self.lose_next_acks -= 1
            raise AdapterUnavailable("reply lost after the write was applied")

    def _candidate(self, candidate_id: str) -> _Candidate:
        try:
            return self._candidates[candidate_id]
        except KeyError:
            raise AdapterRejected(RejectReason.UNKNOWN_CANDIDATE) from None

    def _time_left(self, c: _Candidate) -> int:
        if c.started_at is None:
            return c.exam.outline.duration_s
        elapsed = self._clock() - c.started_at
        return max(0, int(c.exam.outline.duration_s - elapsed))

    def _exam(self, exam_id: str) -> FixtureExam:
        try:
            return self._exams_by_id[exam_id]
        except KeyError:
            raise AdapterRejected(RejectReason.NO_SUCH_EXAM) from None

    async def verify_candidate(self, reg_no: str, exam_code: str) -> CandidateSession:
        await self._enter()
        candidate_id = self._registrations.get((reg_no, exam_code))
        if candidate_id is None:
            raise AdapterRejected(RejectReason.NOT_REGISTERED)
        c = self._candidates[candidate_id]
        if c.started_at is None:  # verifying again on resume does not restart the clock
            c.started_at = self._clock()
            self._store()
        return CandidateSession(candidate_id=candidate_id, exam_id=c.exam.exam_id)

    async def get_exam_outline(self, exam_id: str) -> ExamOutline:
        await self._enter()
        return self._exam(exam_id).outline

    async def get_question(self, exam_id: str, n: int) -> Question:
        await self._enter()
        exam = self._exam(exam_id)
        if not 1 <= n <= exam.outline.question_count:
            raise AdapterRejected(RejectReason.NO_SUCH_QUESTION)
        return exam.questions[n - 1]

    async def get_saved_answers(self, candidate_id: str) -> SavedAnswers:
        await self._enter()
        return SavedAnswers(answers=dict(self._candidate(candidate_id).answers))

    async def save_answer(
        self, candidate_id: str, n: int, choice: OptionLetter | None, operation_id: str
    ) -> None:
        await self._enter()
        c = self._candidate(candidate_id)
        op: _SaveOp = ("save", n, choice)
        if (seen := c.operations.get(operation_id)) is not None:
            if seen != op:
                raise AdapterRejected(RejectReason.OPERATION_CONFLICT)
            return self._ack()
        if c.submitted:
            raise AdapterRejected(RejectReason.ALREADY_SUBMITTED)
        if self._time_left(c) == 0:
            raise AdapterRejected(RejectReason.TIME_UP)
        if not 1 <= n <= c.exam.outline.question_count:
            raise AdapterRejected(RejectReason.NO_SUCH_QUESTION)
        if choice is not None and choice not in {o.letter for o in c.exam.questions[n - 1].options}:
            raise AdapterRejected(RejectReason.INVALID_CHOICE)

        if choice is None:
            c.answers.pop(n, None)
        else:
            c.answers[n] = choice
        c.operations[operation_id] = op
        self._store()
        self._ack()

    async def get_time_left(self, candidate_id: str) -> int:
        await self._enter()
        return self._time_left(self._candidate(candidate_id))

    async def submit(self, candidate_id: str, operation_id: str) -> None:
        await self._enter()
        c = self._candidate(candidate_id)
        op: _SubmitOp = ("submit",)
        if (seen := c.operations.get(operation_id)) is not None:
            if seen != op:
                raise AdapterRejected(RejectReason.OPERATION_CONFLICT)
            return self._ack()
        if c.submitted:
            raise AdapterRejected(RejectReason.ALREADY_SUBMITTED)
        c.submitted = True
        c.operations[operation_id] = op
        self._store()
        self._ack()
