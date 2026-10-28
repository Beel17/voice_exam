"""The Exam Data Adapter: everything the voice service may know about the exam platform.

Nothing outside ``adapters/`` touches the platform; everything else depends only on this
protocol and these models. The models are closed (``extra="forbid"``) so a platform that
sends anything more, an answer key above all, is rejected at the boundary instead of
being carried into the process.
"""

from enum import StrEnum
from typing import Annotated, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from voice_agent.contract import Identifier, OptionLetter

MAX_QUESTION_CHARS = 4000
MAX_OPTION_CHARS = 1000
OPTION_LETTERS: tuple[OptionLetter, ...] = ("A", "B", "C", "D", "E")

QuestionNumber = Annotated[int, Field(ge=1)]
QuestionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
]
OptionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_OPTION_CHARS)
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CandidateSession(_Model):
    """Returned by ``verify_candidate``. These IDs are the only ones the service ever uses
    for a candidate; nothing the candidate says can change them."""

    candidate_id: Identifier
    exam_id: Identifier


class Section(_Model):
    number: Annotated[int, Field(ge=1)]
    title: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    first_question: QuestionNumber
    last_question: QuestionNumber


class ExamOutline(_Model):
    exam_id: Identifier
    question_count: Annotated[int, Field(ge=1, le=999)]
    # Empty when the exam has no sections.
    sections: tuple[Section, ...] = ()
    duration_s: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _sections_tile_the_exam(self) -> Self:
        if not self.sections:
            return self
        expected_first = 1
        for i, s in enumerate(self.sections, start=1):
            if (
                s.number != i
                or s.first_question != expected_first
                or s.last_question < s.first_question
            ):
                raise ValueError("sections must be numbered 1.. and cover questions in order")
            expected_first = s.last_question + 1
        if expected_first != self.question_count + 1:
            raise ValueError("sections must cover every question exactly once")
        return self


class Option(_Model):
    letter: OptionLetter
    text: OptionText
    # What the voice reads instead of ``text`` when symbols or spelling need words.
    spoken_text: OptionText | None = None


class Question(_Model):
    """One question as the candidate may hear it. There is deliberately no field for the
    correct answer, and the closed model means a platform cannot add one."""

    number: QuestionNumber
    text: QuestionText
    spoken_text: QuestionText | None = None
    options: Annotated[tuple[Option, ...], Field(min_length=2, max_length=5)]

    @model_validator(mode="after")
    def _options_lettered_from_a(self) -> Self:
        if tuple(o.letter for o in self.options) != OPTION_LETTERS[: len(self.options)]:
            raise ValueError("options must be lettered A, B, C... in order")
        return self


class SavedAnswers(_Model):
    """The candidate's own saved answers; unanswered questions are absent."""

    answers: dict[QuestionNumber, OptionLetter]


class RejectReason(StrEnum):
    NOT_REGISTERED = "not_registered"
    UNKNOWN_CANDIDATE = "unknown_candidate"
    NO_SUCH_EXAM = "no_such_exam"
    NO_SUCH_QUESTION = "no_such_question"
    INVALID_CHOICE = "invalid_choice"
    ALREADY_SUBMITTED = "already_submitted"
    TIME_UP = "time_up"
    # The same operation ID was sent before with a different payload.
    OPERATION_CONFLICT = "operation_conflict"


class AdapterError(Exception):
    pass


class AdapterUnavailable(AdapterError):
    """The platform could not be reached or did not answer. The call may or may not have
    taken effect, so writes are retried with the same operation ID."""


class AdapterRejected(AdapterError):
    """The platform answered and refused. Retrying the same call will not help."""

    def __init__(self, reason: RejectReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class ExamDataAdapter(Protocol):
    """The platform operations the service needs, and nothing else.

    Every method raises ``AdapterUnavailable`` or ``AdapterRejected`` on failure. Writes
    carry an ``operation_id``: repeating an operation the platform has already applied
    must succeed without applying it again, even if newer operations came in between.
    """

    async def verify_candidate(self, reg_no: str, exam_code: str) -> CandidateSession: ...

    async def get_exam_outline(self, exam_id: str) -> ExamOutline: ...

    async def get_question(self, exam_id: str, n: int) -> Question: ...

    async def get_saved_answers(self, candidate_id: str) -> SavedAnswers: ...

    async def save_answer(
        self, candidate_id: str, n: int, choice: OptionLetter | None, operation_id: str
    ) -> None:
        """Save ``choice`` for question ``n``; ``None`` clears the answer."""
        ...

    async def get_time_left(self, candidate_id: str) -> int:
        """Seconds left, as the platform's timing rules decide."""
        ...

    async def submit(self, candidate_id: str, operation_id: str) -> None: ...
