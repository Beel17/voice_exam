"""The exam store as the engine's platform: ``ExamDataAdapter`` over the local database.

The engine sees each candidate's own version of the paper (``CandidateSession.exam_id``
is the version's ID): questions in the candidate's order, options lettered as shown.
Everything written goes back to the paper's numbers and letters here, so nothing else
needs to know about versions.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Final

from voice_agent.adapters.base import (
    AdapterRejected,
    CandidateSession,
    ExamOutline,
    Question,
    RejectReason,
    SavedAnswers,
)
from voice_agent.contract import OptionLetter
from voice_agent.examstore.store import CandidateRow, ExamStore, StoreError

_REASONS: Final = {r.value: r for r in RejectReason} | {"ambiguous": RejectReason.NOT_REGISTERED}


class LocalAdapter:
    def __init__(self, store: ExamStore, clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._clock = clock

    async def _run[T](self, fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except StoreError as e:
            raise AdapterRejected(_REASONS.get(e.reason, RejectReason.NO_SUCH_EXAM)) from None

    async def verify_candidate(self, reg_no: str, exam_code: str) -> CandidateSession:
        def verify() -> CandidateSession:
            c = self._store.find_candidate(reg_no, exam_code or None)
            self._store.start(c.candidate_id)
            return CandidateSession(candidate_id=c.candidate_id, exam_id=c.version_id)

        return await self._run(verify)

    async def get_exam_outline(self, exam_id: str) -> ExamOutline:
        def outline() -> ExamOutline:
            c = self._store.candidate_by_version(exam_id)
            return self._store.exam(c.exam_id).outline.model_copy(update={"exam_id": exam_id})

        return await self._run(outline)

    async def get_question(self, exam_id: str, n: int) -> Question:
        def question() -> Question:
            c = self._store.candidate_by_version(exam_id)
            try:
                paper_number = c.version.paper_number(n)
            except ValueError:
                raise StoreError("no_such_question") from None
            return c.version.question(n, self._store.paper_question(c.exam_id, paper_number))

        return await self._run(question)

    async def get_saved_answers(self, candidate_id: str) -> SavedAnswers:
        def saved() -> SavedAnswers:
            c = self._store.candidate(candidate_id)
            return SavedAnswers(
                answers={
                    c.version.shown_number(q): c.version.shown_choice(q, letter)
                    for q, letter in self._store.answers(candidate_id).items()
                }
            )

        return await self._run(saved)

    async def save_answer(
        self, candidate_id: str, n: int, choice: OptionLetter | None, operation_id: str
    ) -> None:
        def save() -> None:
            c = self._store.candidate(candidate_id)
            self._check_time(c)
            try:
                question = c.version.paper_number(n)
                letter = None if choice is None else c.version.paper_choice(n, choice)
            except ValueError as e:
                reason = "no_such_question" if "question" in str(e) else "invalid_choice"
                raise StoreError(reason) from None
            self._store.write(operation_id, candidate_id, "save", question, letter)

        await self._run(save)

    async def get_time_left(self, candidate_id: str) -> int:
        return await self._run(lambda: self._time_left(self._store.candidate(candidate_id)))

    async def submit(self, candidate_id: str, operation_id: str) -> None:
        await self._run(lambda: self._store.write(operation_id, candidate_id, "submit"))

    def _time_left(self, c: CandidateRow) -> int:
        duration = self._store.exam(c.exam_id).outline.duration_s
        if c.started_at is None:
            return duration
        return max(0, int(duration - (self._clock() - c.started_at)))

    def _check_time(self, c: CandidateRow) -> None:
        if c.submitted_at is None and c.started_at is not None and self._time_left(c) == 0:
            raise StoreError("time_up")
