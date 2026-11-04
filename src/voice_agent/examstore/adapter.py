"""The exam store as the engine's platform: ``ExamDataAdapter`` over the local database.

The engine sees each candidate's own version of the paper (``CandidateSession.exam_id``
is the version's ID): questions in the candidate's order, options lettered as shown.
Everything written goes back to the paper's numbers and letters here, so nothing else
needs to know about versions.

With ``admit`` (sync.lookup), a candidate not found in an open paper is asked about on
the platform, which may confirm them and bring their paper here first.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Final

from voice_agent.adapters.base import (
    AdapterRejected,
    CandidateProfile,
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
    def __init__(
        self,
        store: ExamStore,
        clock: Callable[[], float] = time.time,
        admit: Callable[[str], Awaitable[str]] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._admit = admit  # what the candidate signed in with -> their candidate ID

    async def _run[T](self, fn: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(fn)
        except StoreError as e:
            raise AdapterRejected(_REASONS.get(e.reason, RejectReason.NO_SUCH_EXAM)) from None

    async def verify_candidate(self, reg_no: str, exam_code: str) -> CandidateSession:
        def verify(admitted: str | None = None) -> CandidateSession:
            if admitted is None:
                c = self._store.find_candidate(reg_no, exam_code or None)
            else:
                c = self._store.candidate(admitted)
            self._store.start(c.candidate_id)
            return CandidateSession(candidate_id=c.candidate_id, exam_id=c.version_id)

        try:
            return await self._run(verify)
        except AdapterRejected as e:
            if self._admit is None or e.reason is not RejectReason.NOT_REGISTERED:
                raise
        admitted = await self._admit(reg_no)
        return await self._run(lambda: verify(admitted))

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

    async def get_profile(self, candidate_id: str) -> CandidateProfile:
        def profile() -> CandidateProfile:
            c = self._store.candidate(candidate_id)
            return CandidateProfile(
                exam_number=c.profile.exam_number,
                full_name=c.profile.full_name,
                exam_title=self._store.exam(c.exam_id).title,
                seat=c.profile.seat,
                audio_mode=c.profile.audio_mode,
                has_photo=self._store.photo(candidate_id) is not None,
            )

        return await self._run(profile)

    async def get_photo(self, candidate_id: str) -> bytes | None:
        return await self._run(lambda: self._store.photo(candidate_id))

    def _time_left(self, c: CandidateRow) -> int:
        duration = self._store.exam(c.exam_id).outline.duration_s
        if c.started_at is None:
            return duration
        return max(0, int(duration - (self._clock() - c.started_at)))

    def _check_time(self, c: CandidateRow) -> None:
        if c.submitted_at is None and c.started_at is not None and self._time_left(c) == 0:
            raise StoreError("time_up")
