"""The exam script as a pure state machine: no I/O, no clock, no randomness.

``step(state, event)`` returns the next state and the effects the caller must carry out
(load a question, speak, save, submit). Every input, including the result of each effect,
comes back in as an event, so a session's state is exactly ``replay(its event log)``.

Policy lives here too: every action is checked against the current state (question
exists, option exists, something is pending, exam not ended) before anything changes.
Answers change only after an explicit confirmation; submission needs two.

Identification (verifying the candidate) happens before the first event and is not
part of this machine; ``Started`` carries its result.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from voice_agent.adapters.base import OPTION_LETTERS, RejectReason, Section
from voice_agent.contract import Identifier, OptionLetter, PendingConfirm
from voice_agent.contract import State as StateMessage
from voice_agent.session import replies as r
from voice_agent.understanding import intents as i

SPEEDS: Final = (0.8, 1.0, 1.5, 2.0, 2.5)
NORMAL_SPEED: Final = 1
CUE_VOLUMES: Final = (0.25, 0.5, 0.75, 1.0)
DEFAULT_CUES: Final = 2
# Consecutive misunderstandings before the keyboard is offered.
UNCLEAR_BEFORE_KEYBOARD: Final = 2


class Phase(StrEnum):
    INTRO = "intro"
    LISTEN = "listen"
    CONFIRM_ANSWER = "confirm_answer"  # an answer or a clear is waiting for yes/no
    CONFIRM_SUBMIT = "confirm_submit"
    CONFIRM_SUBMIT_FINAL = "confirm_submit_final"
    SUBMITTING = "submitting"
    ENDED = "ended"


class Source(StrEnum):
    RULES = "rules"
    LLM = "llm"
    KEY = "key"


# --- Events (inputs; also what the event log stores) ------------------------------------


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Started(_Event):
    kind: Literal["started"] = "started"
    session_id: Identifier
    candidate_id: Identifier
    exam_id: Identifier
    question_count: Annotated[int, Field(ge=1)]
    sections: tuple[Section, ...]
    duration_s: Annotated[int, Field(ge=1)]
    # Answers the platform already holds (the candidate may be resuming elsewhere).
    saved: dict[int, OptionLetter] = {}


class Resumed(_Event):
    """The service restarted or the client reconnected."""

    kind: Literal["resumed"] = "resumed"


# busy..degraded: the model could not or should not be asked (degraded: the service is
# on rules only). ungrounded: it proposed an answer the sentence did not name.
Fallback = Literal[
    "busy", "rate_limited", "unavailable", "timeout", "disabled", "degraded", "ungrounded"
]


class Understood(_Event):
    kind: Literal["understood"] = "understood"
    intent: i.Intent
    source: Source
    # For the audit trail and analytics; the machine never reads these.
    text: str | None = None
    latency_ms: Annotated[int, Field(ge=0)] | None = None
    # Why the model's understanding was not used for a sentence the rules did not know.
    fallback: Fallback | None = None


class QuestionLoaded(_Event):
    kind: Literal["question_loaded"] = "question_loaded"
    question: Annotated[int, Field(ge=1)]
    option_count: Annotated[int, Field(ge=2, le=5)]


class QuestionUnavailable(_Event):
    kind: Literal["question_unavailable"] = "question_unavailable"
    question: Annotated[int, Field(ge=1)]


class WriteOutcome(_Event):
    """The result of a save or submit, now (the candidate is waiting) or later (outbox)."""

    kind: Literal["write_outcome"] = "write_outcome"
    operation_id: Identifier
    outcome: Literal["saved", "queued", "rejected"]
    reason: RejectReason | None = None

    @model_validator(mode="after")
    def _reason_iff_rejected(self) -> Self:
        if (self.outcome == "rejected") != (self.reason is not None):
            raise ValueError("a reason is given exactly when the write was rejected")
        return self


class TimeLeftKnown(_Event):
    kind: Literal["time_left"] = "time_left"
    seconds: Annotated[int, Field(ge=0)] | None


class TimeWarning(_Event):
    kind: Literal["time_warning"] = "time_warning"
    minutes: Annotated[int, Field(ge=1)]


class TimeUp(_Event):
    kind: Literal["time_up"] = "time_up"


Event = Annotated[
    Started
    | Resumed
    | Understood
    | QuestionLoaded
    | QuestionUnavailable
    | WriteOutcome
    | TimeLeftKnown
    | TimeWarning
    | TimeUp,
    Field(discriminator="kind"),
]


# --- Effects (what the caller must do) --------------------------------------------------


@dataclass(frozen=True, slots=True)
class Load:
    """Fetch question ``question``; report ``QuestionLoaded`` or ``QuestionUnavailable``."""

    question: int


@dataclass(frozen=True, slots=True)
class Speak:
    reply: r.Reply


@dataclass(frozen=True, slots=True)
class SaveAnswer:
    question: int
    choice: OptionLetter | None
    operation_id: str


@dataclass(frozen=True, slots=True)
class SubmitExam:
    operation_id: str


@dataclass(frozen=True, slots=True)
class FetchTimeLeft:
    pass


Effect = Load | Speak | SaveAnswer | SubmitExam | FetchTimeLeft


# --- State ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Pending:
    """An answer change waiting for yes/no; ``choice`` None means clear."""

    question: int
    choice: OptionLetter | None


@dataclass(frozen=True, slots=True)
class Unsynced:
    """A confirmed write the platform has not acknowledged yet."""

    question: int
    previous: OptionLetter | None
    written: OptionLetter | None


@dataclass(frozen=True, slots=True)
class SessionState:
    session_id: str
    candidate_id: str
    exam_id: str
    question_count: int
    sections: tuple[Section, ...]
    duration_s: int
    phase: Phase = Phase.INTRO
    current: int = 1
    # Option count of the current question; None until the first question is loaded.
    option_count: int | None = None
    answers: Mapping[int, OptionLetter] = field(default_factory=dict)
    pending: Pending | None = None
    # "Change 12 to A" while on question 5: answer once question 12 has loaded.
    answer_after_load: tuple[int, OptionLetter] | None = None
    unsynced: Mapping[str, Unsynced] = field(default_factory=dict)
    # The write whose outcome the candidate is waiting to hear.
    awaiting: str | None = None
    submit_operation: str | None = None
    speed_step: int = NORMAL_SPEED
    cue_step: int = DEFAULT_CUES
    unclear_streak: int = 0
    seq: int = 0
    end_reason: Literal["submitted", "time_up"] | None = None

    @property
    def speed(self) -> float:
        return SPEEDS[self.speed_step]

    @property
    def cue_volume(self) -> float:
        return CUE_VOLUMES[self.cue_step]

    @property
    def unanswered(self) -> tuple[int, ...]:
        return tuple(n for n in range(1, self.question_count + 1) if n not in self.answers)

    def section_of(self, n: int) -> Section | None:
        return next((s for s in self.sections if s.first_question <= n <= s.last_question), None)


Step = tuple[SessionState, tuple[Effect, ...]]

_CONFIRMING: Final = frozenset(
    {Phase.CONFIRM_ANSWER, Phase.CONFIRM_SUBMIT, Phase.CONFIRM_SUBMIT_FINAL}
)

# Intents that neither move nor start a confirmation; while a confirmation is pending
# they are answered and the confirmation question is asked again.
_PASSIVE: Final = (
    i.RepeatQuestion,
    i.RepeatOptions,
    i.RepeatOption,
    i.SpellOption,
    i.WhereAmI,
    i.TimeLeft,
    i.CountAnswered,
    i.ListUnanswered,
    i.ReviewAnswers,
    i.Faster,
    i.Slower,
    i.NormalSpeed,
    i.LouderCues,
    i.QuieterCues,
    i.Help,
    i.Unclear,
    i.NotAllowed,
)


def _say(*replies: r.Reply) -> tuple[Effect, ...]:
    return tuple(Speak(x) for x in replies)


# --- Entry points -----------------------------------------------------------------------


def start(event: Started) -> Step:
    state = SessionState(
        session_id=event.session_id,
        candidate_id=event.candidate_id,
        exam_id=event.exam_id,
        question_count=event.question_count,
        sections=event.sections,
        duration_s=event.duration_s,
        answers={n: c for n, c in event.saved.items() if 1 <= n <= event.question_count},
        seq=1,
    )
    titles = tuple(s.title for s in event.sections)
    return state, _say(r.Welcome(event.question_count, titles, event.duration_s))


def step(state: SessionState, event: Event) -> Step:
    state = replace(state, seq=state.seq + 1)
    match event:
        case Understood(intent=intent, source=source):
            return _on_intent(state, intent, source)
        case QuestionLoaded():
            return _on_loaded(state, event)
        case QuestionUnavailable(question=n):
            return replace(state, answer_after_load=None), _say(r.QuestionNotAvailable(n))
        case WriteOutcome():
            return _on_write_outcome(state, event)
        case TimeLeftKnown(seconds=seconds):
            return state, _say(r.TimeLeft(seconds)) + _reprompt(state)
        case TimeWarning(minutes=minutes):
            if state.phase is Phase.ENDED:
                return state, ()
            return state, _say(r.TimeWarning(minutes))
        case TimeUp():
            if state.phase is Phase.ENDED:
                return state, ()
            return _end(state, "time_up"), _say(r.TimeIsUp())
        case Resumed():
            return _on_resumed(state)
        case Started():
            raise ValueError("a session starts only once")


def replay(events: Iterable[Event]) -> SessionState:
    """Rebuild a session's state from its event log; effects are not repeated."""
    it = iter(events)
    first = next(it, None)
    if not isinstance(first, Started):
        raise ValueError("an event log must begin with Started")
    state, _ = start(first)
    for event in it:
        state, _ = step(state, event)
    return state


def snapshot(state: SessionState, time_left_s: int | None) -> StateMessage:
    """The ``state`` message the exam PC shows to invigilators."""
    pending: PendingConfirm | None = None
    if state.phase is Phase.CONFIRM_ANSWER and state.pending is not None:
        pending = PendingConfirm(
            kind="answer" if state.pending.choice else "clear",
            question=state.pending.question,
            choice=state.pending.choice,
        )
    elif state.phase in (Phase.CONFIRM_SUBMIT, Phase.CONFIRM_SUBMIT_FINAL):
        pending = PendingConfirm(kind="submit")
    return StateMessage(
        question=state.current,
        of=state.question_count,
        answered=len(state.answers),
        unanswered=state.question_count - len(state.answers),
        time_left_s=time_left_s,
        pending_confirm=pending,
        speed=state.speed,
        cue_volume=state.cue_volume,
    )


# --- Intents ----------------------------------------------------------------------------


def _on_intent(state: SessionState, intent: i.Intent, source: Source) -> Step:
    if isinstance(intent, i.Unclear):
        state = replace(state, unclear_streak=state.unclear_streak + 1)
    else:
        state = replace(state, unclear_streak=0)

    if state.phase is Phase.ENDED:
        assert state.end_reason is not None
        return state, _say(r.ExamEnded(state.end_reason))
    if state.phase is Phase.SUBMITTING:
        return state, _say(r.Submitting())

    if state.phase in _CONFIRMING:
        match intent:
            case i.Confirm() if source is Source.LLM:
                # Saving and submitting need an explicit yes, recognised by the rules or
                # pressed as Enter. A sentence only the model understood never confirms,
                # so a fooled model cannot save or submit anything.
                return state, _say(r.SayYesToConfirm())
            case i.Confirm():
                return _on_confirm(state)
            case i.Cancel():
                return _drop_confirmation(state)
            case i.Answer() | i.ChangeAnswer() if state.phase is Phase.CONFIRM_ANSWER:
                # "Actually, C": the new choice replaces the pending one.
                return _listen(state, intent)
            case _ if isinstance(intent, _PASSIVE):
                next_state, effects = _listen(state, intent)
                if isinstance(intent, i.TimeLeft):  # re-asked when the time arrives
                    return next_state, effects
                return next_state, effects + _reprompt(next_state)
            case _:
                # Moving or starting something else abandons the confirmation, aloud.
                dropped, notice = _drop_confirmation(state)
                next_state, effects = _listen(dropped, intent)
                return next_state, notice + effects

    return _listen(state, intent)


def _listen(state: SessionState, intent: i.Intent) -> Step:
    n, of = state.current, state.question_count
    started = state.option_count is not None
    match intent:
        # Starting from the introduction.
        case i.Confirm() | i.Next() | i.RepeatQuestion() | i.RepeatOptions() if not started:
            return state, (Load(n),)
        # Confirmations only ever happen on a loaded question, so nothing before the first
        # question can start one.
        case (
            i.Answer()
            | i.ClearAnswer()
            | i.Submit()
            | i.RepeatOption()
            | i.SpellOption()
            | i.Cancel()
        ) if not started:
            return state, _say(r.IntroPrompt())
        case i.ChangeAnswer(args=i.ChangeAnswerArgs(question=None)) if not started:
            return state, _say(r.IntroPrompt())

        # Answering.
        case i.Answer(args=i.ChoiceArgs(choice=choice)):
            return _propose_answer(state, n, choice)
        case i.ChangeAnswer(args=i.ChangeAnswerArgs(choice=choice, question=q)):
            target = q if q is not None else n
            if not 1 <= target <= of:
                return state, _say(r.NoSuchQuestion(target, of))
            if target != n or not started:
                return replace(state, answer_after_load=(target, choice)), (Load(target),)
            return _propose_answer(state, n, choice)
        case i.ClearAnswer(args=i.MaybeQuestionArgs(question=q)):
            target = q if q is not None else n
            if not 1 <= target <= of:
                return state, _say(r.NoSuchQuestion(target, of))
            previous = state.answers.get(target)
            if previous is None:
                return state, _say(r.NotAnswered(target))
            pending = Pending(target, None)
            return replace(state, phase=Phase.CONFIRM_ANSWER, pending=pending), _say(
                r.ConfirmClear(target, previous)
            )
        case i.Confirm():
            return state, _say(r.NothingToConfirm())
        case i.Cancel():
            return state, _say(r.NothingToConfirm())

        # Moving.
        case i.Next():
            return (state, _say(r.AtLast(of))) if n >= of else (state, (Load(n + 1),))
        case i.Previous():
            return (state, _say(r.AtFirst())) if n <= 1 else (state, (Load(n - 1),))
        case i.GoTo(args=i.QuestionArgs(question=target)):
            if target > of:
                return state, _say(r.NoSuchQuestion(target, of))
            return state, (Load(target),)
        case i.First():
            return state, (Load(1),)
        case i.Last():
            return state, (Load(of),)
        case i.NextUnanswered():
            unanswered = state.unanswered
            if not unanswered:
                return state, _say(r.AllAnswered())
            after = [q for q in unanswered if q > n] or list(unanswered)
            return state, (Load(after[0]),)
        case i.GoToSection(args=i.SectionArgs(section=s)):
            if not state.sections:
                return state, _say(r.NoSections())
            if s > len(state.sections):
                return state, _say(r.NoSuchSection(s, len(state.sections)))
            return state, (Load(state.sections[s - 1].first_question),)

        # Hearing again.
        case i.RepeatQuestion():
            return state, _say(r.ReadQuestion(n, of, "full", state.answers.get(n)))
        case i.RepeatOptions():
            return state, _say(r.ReadQuestion(n, of, "options", state.answers.get(n)))
        case i.RepeatOption(args=i.ChoiceArgs(choice=choice)):
            if (problem := _check_option(state, choice)) is not None:
                return state, _say(problem)
            return state, _say(r.ReadOption(n, choice))
        case i.SpellOption(args=i.ChoiceArgs(choice=choice)):
            if (problem := _check_option(state, choice)) is not None:
                return state, _say(problem)
            return state, _say(r.SpellOption(n, choice))

        # Status.
        case i.WhereAmI():
            section = state.section_of(n)
            title = section.title if section else None
            return state, _say(r.WhereAmI(n, of, title, state.answers.get(n), started))
        case i.TimeLeft():
            return state, (FetchTimeLeft(),)
        case i.CountAnswered():
            return state, _say(r.CountAnswered(len(state.answers), of))
        case i.ListUnanswered():
            return state, _say(r.Unanswered(state.unanswered))
        case i.ReviewAnswers(args=i.MaybeQuestionArgs(question=None)):
            all_answers = tuple((q, state.answers.get(q)) for q in range(1, of + 1))
            return state, _say(r.ReviewAll(all_answers))
        case i.ReviewAnswers(args=i.MaybeQuestionArgs(question=q)) if q is not None:
            if q > of:
                return state, _say(r.NoSuchQuestion(q, of))
            return state, _say(r.ReviewOne(q, state.answers.get(q)))

        # Listening comfort.
        case i.Faster():
            return _set_speed(state, state.speed_step + 1)
        case i.Slower():
            return _set_speed(state, state.speed_step - 1)
        case i.NormalSpeed():
            return _set_speed(state, NORMAL_SPEED)
        case i.LouderCues():
            return _set_cues(state, state.cue_step + 1)
        case i.QuieterCues():
            return _set_cues(state, state.cue_step - 1)

        # Finishing.
        case i.Submit():
            answered = len(state.answers)
            reply = r.ConfirmSubmit(answered, state.question_count - answered)
            return replace(state, phase=Phase.CONFIRM_SUBMIT), _say(reply)
        case i.Help():
            return state, _say(r.Help())
        case i.Unclear():
            offer = state.unclear_streak >= UNCLEAR_BEFORE_KEYBOARD
            return state, _say(r.NotUnderstood(offer))
        case i.NotAllowed():
            return state, _say(r.OutOfScope())
    raise AssertionError(f"unhandled intent {intent!r}")  # pragma: no cover


def _check_option(state: SessionState, choice: OptionLetter) -> r.Reply | None:
    assert state.option_count is not None
    if OPTION_LETTERS.index(choice) >= state.option_count:
        return r.NoSuchOption(choice, state.option_count)
    return None


def _propose_answer(state: SessionState, n: int, choice: OptionLetter) -> Step:
    if (problem := _check_option(state, choice)) is not None:
        return replace(state, phase=Phase.LISTEN, pending=None), _say(problem)
    pending = Pending(n, choice)
    return replace(state, phase=Phase.CONFIRM_ANSWER, pending=pending), _say(
        r.ConfirmAnswer(n, choice)
    )


def _on_confirm(state: SessionState) -> Step:
    match state.phase:
        case Phase.CONFIRM_ANSWER:
            assert state.pending is not None
            n, choice = state.pending.question, state.pending.choice
            op = _operation_id(state)
            answers = dict(state.answers)
            previous = answers.pop(n, None)
            if choice is not None:
                answers[n] = choice
            unsynced = {**state.unsynced, op: Unsynced(n, previous, choice)}
            # The answer counts from here: it is in the event log and the outbox. The
            # candidate hears "Saved" only once the platform's outcome comes back.
            next_state = replace(
                state,
                phase=Phase.LISTEN,
                pending=None,
                answers=answers,
                unsynced=unsynced,
                awaiting=op,
            )
            return next_state, (SaveAnswer(n, choice, op),)
        case Phase.CONFIRM_SUBMIT:
            unanswered = state.question_count - len(state.answers)
            return replace(state, phase=Phase.CONFIRM_SUBMIT_FINAL), _say(
                r.ConfirmSubmitFinal(unanswered)
            )
        case _:
            if state.unsynced:
                return replace(state, phase=Phase.LISTEN), _say(
                    r.SubmitBlocked(len(state.unsynced))
                )
            op = _operation_id(state)
            next_state = replace(state, phase=Phase.SUBMITTING, submit_operation=op, awaiting=op)
            return next_state, (SubmitExam(op),)


def _drop_confirmation(state: SessionState) -> Step:
    if state.phase is Phase.CONFIRM_ANSWER:
        assert state.pending is not None
        n = state.pending.question
        reply: r.Reply = r.NotSaved(n, state.answers.get(n))
    else:
        reply = r.NotSubmitted()
    return replace(state, phase=Phase.LISTEN, pending=None), _say(reply)


def _reprompt(state: SessionState) -> tuple[Effect, ...]:
    """The pending confirmation question again, after answering something else."""
    match state.phase:
        case Phase.CONFIRM_ANSWER:
            assert state.pending is not None
            n, choice = state.pending.question, state.pending.choice
            if choice is None:
                return _say(r.ConfirmClear(n, state.answers[n]))
            return _say(r.ConfirmAnswer(n, choice))
        case Phase.CONFIRM_SUBMIT:
            answered = len(state.answers)
            return _say(r.ConfirmSubmit(answered, state.question_count - answered))
        case Phase.CONFIRM_SUBMIT_FINAL:
            return _say(r.ConfirmSubmitFinal(state.question_count - len(state.answers)))
        case _:
            return ()


def _set_speed(state: SessionState, wanted: int) -> Step:
    if wanted >= len(SPEEDS):
        return state, _say(r.SpeedChanged(state.speed, "fastest"))
    if wanted < 0:
        return state, _say(r.SpeedChanged(state.speed, "slowest"))
    state = replace(state, speed_step=wanted)
    return state, _say(r.SpeedChanged(state.speed, None))


def _set_cues(state: SessionState, wanted: int) -> Step:
    if wanted >= len(CUE_VOLUMES):
        return state, _say(r.CuesChanged("loudest"))
    if wanted < 0:
        return state, _say(r.CuesChanged("quietest"))
    return replace(state, cue_step=wanted), _say(r.CuesChanged(None))


# --- Results of effects -----------------------------------------------------------------


def _on_loaded(state: SessionState, event: QuestionLoaded) -> Step:
    if state.phase in (Phase.ENDED, Phase.SUBMITTING):
        return state, ()
    n = event.question
    if not 1 <= n <= state.question_count:
        raise ValueError(f"loaded question {n} is outside the exam")
    state = replace(
        state, phase=Phase.LISTEN, pending=None, current=n, option_count=event.option_count
    )
    effects = _say(r.ReadQuestion(n, state.question_count, "full", state.answers.get(n)))
    if state.answer_after_load is not None:
        target, choice = state.answer_after_load
        state = replace(state, answer_after_load=None)
        if target == n:
            state, proposal = _propose_answer(state, n, choice)
            effects += proposal
    return state, effects


def _on_write_outcome(state: SessionState, event: WriteOutcome) -> Step:
    op = event.operation_id
    waiting = state.awaiting == op
    if waiting:
        state = replace(state, awaiting=None)

    if op == state.submit_operation:
        return _on_submit_outcome(state, event, waiting)
    write = state.unsynced.get(op)
    if write is None:
        return state, ()  # already settled; a repeat report changes nothing

    if event.outcome == "queued":
        if not waiting:
            return state, ()
        return state, _say(r.SavedLocally(write.question)) + _after_save(state, write)

    unsynced = {k: v for k, v in state.unsynced.items() if k != op}
    state = replace(state, unsynced=unsynced)
    if event.outcome == "saved":
        if not waiting:
            return state, ()
        return state, _say(r.Saved(write.question, write.written)) + _after_save(state, write)

    # Refused: undo the write unless a later one has already replaced it.
    assert event.reason is not None
    if state.answers.get(write.question) == write.written:
        answers = dict(state.answers)
        if write.previous is None:
            answers.pop(write.question, None)
        else:
            answers[write.question] = write.previous
        state = replace(state, answers=answers)
    effects = _say(r.WriteRefused(write.question, event.reason))
    if event.reason is RejectReason.TIME_UP:
        return _end(state, "time_up"), effects
    if event.reason is RejectReason.ALREADY_SUBMITTED:
        return _end(state, "submitted"), effects
    return state, effects


def _after_save(state: SessionState, write: Unsynced) -> tuple[Effect, ...]:
    """Save answer, then read the next question (design doc, conversation flow)."""
    if write.written is None:  # clearing an answer is not answering; stay put
        return ()
    if write.question >= state.question_count:
        return _say(r.AtLast(state.question_count))
    return (Load(write.question + 1),)


def _on_submit_outcome(state: SessionState, event: WriteOutcome, waiting: bool) -> Step:
    if state.phase is Phase.ENDED:
        return state, ()
    match event.outcome, event.reason:
        case (("saved" | "queued"), _):
            reply: r.Reply = r.Submitted(queued=event.outcome == "queued")
            return _end(state, "submitted"), _say(reply) if waiting else ()
        case "rejected", RejectReason.ALREADY_SUBMITTED:
            return _end(state, "submitted"), _say(r.Submitted(queued=False))
        case "rejected", RejectReason.TIME_UP:
            return _end(state, "time_up"), _say(r.TimeIsUp())
        case _:
            assert event.reason is not None
            state = replace(state, phase=Phase.LISTEN, submit_operation=None)
            return state, _say(r.SubmitRefused(event.reason))


def _on_resumed(state: SessionState) -> Step:
    if state.phase is Phase.ENDED:
        assert state.end_reason is not None
        return state, _say(r.ExamEnded(state.end_reason))
    if state.phase is Phase.SUBMITTING:
        return replace(state, awaiting=None), _say(r.Submitting())
    if state.option_count is None:
        titles = tuple(s.title for s in state.sections)
        return replace(state, answer_after_load=None), _say(
            r.Welcome(state.question_count, titles, state.duration_s)
        )
    # An unconfirmed answer is discarded and the question re-read (design doc).
    state = replace(state, phase=Phase.LISTEN, pending=None, answer_after_load=None, awaiting=None)
    return state, (*_say(r.WelcomeBack(state.current, state.question_count)), Load(state.current))


def _end(state: SessionState, reason: Literal["submitted", "time_up"]) -> SessionState:
    return replace(
        state, phase=Phase.ENDED, pending=None, answer_after_load=None, end_reason=reason
    )


def _operation_id(state: SessionState) -> str:
    # Deterministic, so replaying the log reproduces the same IDs the outbox holds.
    return f"{state.session_id}:{state.seq}"
