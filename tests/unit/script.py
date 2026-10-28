"""A scripted session: drives the pure machine the way the session manager will.

Effects are carried out at once against fixture data, and their results are fed back in
as events, depth first, so a test reads like a conversation.
"""

from collections.abc import Iterable
from typing import Literal

from voice_agent.adapters.base import RejectReason
from voice_agent.adapters.fake_adapter import FixtureExam, load_fixture
from voice_agent.contract import OptionLetter
from voice_agent.session import machine as m
from voice_agent.session.replies import needs_question, render
from voice_agent.understanding import intents as i

Outcome = Literal["saved", "queued", "rejected"] | None


class Script:
    def __init__(
        self,
        exam: str = "sample_60",
        saved: dict[int, OptionLetter] | None = None,
        unavailable: Iterable[int] = (),
    ) -> None:
        self.exam: FixtureExam = load_fixture(exam)
        self.unavailable = set(unavailable)
        # What the next write's outcome will be; None leaves it pending.
        self.next_outcome: Outcome = "saved"
        self.next_reason: RejectReason | None = None
        self.time_left: int | None = 1800
        self.events: list[m.Event] = []
        self.writes: list[m.SaveAnswer | m.SubmitExam] = []
        self.said: list[str] = []
        started = m.Started(
            session_id="s-1",
            candidate_id="cand-0001",
            exam_id=self.exam.exam_id,
            question_count=self.exam.outline.question_count,
            sections=self.exam.outline.sections,
            duration_s=self.exam.outline.duration_s,
            saved=saved or {},
        )
        self.events.append(started)
        self.state, effects = m.start(started)
        self._run(effects)

    # --- Driving ----------------------------------------------------------------------

    def feed(self, event: m.Event) -> str:
        """Feed one event; return everything said in response, as one string."""
        self.said = []
        self._feed(event)
        return " ".join(self.said)

    def say(self, intent: i.Intent) -> str:
        return self.feed(m.Understood(intent=intent, source=m.Source.RULES))

    def begin(self) -> str:
        return self.say(i.Confirm())

    def answer(self, choice: OptionLetter) -> str:
        return self.say(i.Answer(args=i.ChoiceArgs(choice=choice)))

    def go_to(self, n: int) -> str:
        return self.say(i.GoTo(args=i.QuestionArgs(question=n)))

    def yes(self) -> str:
        return self.say(i.Confirm())

    def no(self) -> str:
        return self.say(i.Cancel())

    def settle(self, operation_id: str, outcome: Literal["saved", "queued", "rejected"],
               reason: RejectReason | None = None) -> str:  # fmt: skip
        return self.feed(m.WriteOutcome(operation_id=operation_id, outcome=outcome, reason=reason))

    def answer_and_save(self, n: int, choice: OptionLetter) -> None:
        self.go_to(n)
        self.answer(choice)
        self.yes()

    # --- Inspecting -------------------------------------------------------------------

    def phase(self) -> m.Phase:
        # A method, not a property: mypy would otherwise keep a narrowed phase across
        # calls that change it.
        return self.state.phase

    def check_replay(self) -> None:
        assert m.replay(self.events) == self.state

    # --- Carrying out effects ---------------------------------------------------------

    def _feed(self, event: m.Event) -> None:
        self.events.append(event)
        self.state, effects = m.step(self.state, event)
        self._run(effects)

    def _run(self, effects: Iterable[m.Effect]) -> None:
        for effect in effects:
            match effect:
                case m.Load(question=n):
                    if n in self.unavailable:
                        self._feed(m.QuestionUnavailable(question=n))
                    else:
                        options = len(self.exam.questions[n - 1].options)
                        self._feed(m.QuestionLoaded(question=n, option_count=options))
                case m.Speak(reply=reply):
                    q = needs_question(reply)
                    question = self.exam.questions[q - 1] if q else None
                    self.said.extend(s.text for s in render(reply, question))
                case m.SaveAnswer(operation_id=op) | m.SubmitExam(operation_id=op):
                    self.writes.append(effect)
                    if self.next_outcome is not None:
                        self._feed(
                            m.WriteOutcome(
                                operation_id=op, outcome=self.next_outcome, reason=self.next_reason
                            )
                        )
                case m.FetchTimeLeft():
                    self._feed(m.TimeLeftKnown(seconds=self.time_left))
