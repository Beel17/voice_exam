"""Sessions end to end against the fake platform and a real SQLite store."""

import asyncio
import itertools
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from voice_agent.adapters.base import (
    AdapterRejected,
    AdapterUnavailable,
    CandidateSession,
    RejectReason,
)
from voice_agent.adapters.fake_adapter import FakeAdapter, load_fixture
from voice_agent.contract import Cue, Say, State
from voice_agent.runtime.degrade import Level, Load, level_for
from voice_agent.session import machine as m
from voice_agent.session.events import SessionStore
from voice_agent.session.manager import LiveSession, NotFound, SessionManager
from voice_agent.understanding import intents as i
from voice_agent.understanding.llm import LlmContext, LlmFallback

REG_NO, EXAM = "10000001AA", "UTME-SAMPLE"
CANDIDATE = "cand-0001"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Harness:
    def __init__(self, tmp_path: Path) -> None:
        self.db = tmp_path / "sessions.db"
        self.clock = Clock()
        self.platform = FakeAdapter.with_bundled_fixtures(self.clock)
        self.ids = (f"s{n}" for n in itertools.count(1))
        self.store: SessionStore
        self.manager: SessionManager

    async def start(self) -> None:
        self.store = await SessionStore.open(self.db)
        self.manager = SessionManager(
            self.store,
            self.platform,
            adapter_timeout_s=0.2,
            new_session_id=lambda: next(self.ids),
        )

    async def restart(self) -> None:
        """A forced restart: drop every in-memory session, reopen the database."""
        await self.store.close()
        await self.start()

    async def login(self) -> LiveSession:
        return await self.manager.identify(REG_NO, EXAM)

    async def say(self, s: LiveSession, intent: i.Intent) -> str:
        await self.manager.handle(s, intent, m.Source.RULES)
        return said(s)

    async def answer(
        self, s: LiveSession, n: int, choice: str, *, platform_down_at_save: bool = False
    ) -> str:
        await self.say(s, i.GoTo(args=i.QuestionArgs(question=n)))
        await self.say(s, i.Answer(args=i.ChoiceArgs(choice=choice)))  # type: ignore[arg-type]
        if platform_down_at_save:
            self.platform.unavailable = True
        return await self.say(s, i.Confirm())

    async def platform_answers(self) -> dict[int, str]:
        return dict((await self.platform.get_saved_answers(CANDIDATE)).answers)


def said(s: LiveSession) -> str:
    return " ".join(msg.text for msg in s.drain() if isinstance(msg, Say))


def last_state(s: LiveSession) -> State:
    states = [msg for msg in s.drain() if isinstance(msg, State)]
    return states[-1]


@pytest.fixture
async def h(tmp_path: Path) -> AsyncIterator[Harness]:
    harness = Harness(tmp_path)
    await harness.start()
    yield harness
    await harness.store.close()


# --- Identification ---------------------------------------------------------------------


async def test_login_starts_a_session_and_welcomes(h: Harness) -> None:
    s = await h.login()
    messages = s.drain()
    assert isinstance(messages[0], Say) and messages[0].text.startswith("Welcome. This exam")
    assert isinstance(messages[-1], State) and messages[-1].of == 60
    assert await h.store.get_session(s.session_id) is not None


async def test_unregistered_candidate_is_refused(h: Harness) -> None:
    with pytest.raises(AdapterRejected) as info:
        await h.manager.identify("99999999ZZ", EXAM)
    assert info.value.reason is RejectReason.NOT_REGISTERED


async def test_login_while_the_platform_is_down(h: Harness) -> None:
    h.platform.unavailable = True
    with pytest.raises(AdapterUnavailable):
        await h.login()


async def test_a_platform_that_hangs_counts_as_down(h: Harness) -> None:
    class Hanging(FakeAdapter):
        async def verify_candidate(self, reg_no: str, exam_code: str) -> CandidateSession:
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

    manager = SessionManager(h.store, Hanging([load_fixture("sample_60")]), adapter_timeout_s=0.05)
    with pytest.raises(AdapterUnavailable):
        await manager.identify(REG_NO, EXAM)


async def test_two_logins_at_once_share_one_session(h: Harness) -> None:
    first, second = await asyncio.gather(h.login(), h.login())
    assert first is second


async def test_logging_in_again_resumes(h: Harness) -> None:
    s = await h.login()
    await h.answer(s, 14, "B")
    again = await h.login()
    assert again is s
    assert said(s).startswith("Welcome back. You were on question 15 of 60. Question 15 of 60.")


async def test_unknown_session_id(h: Harness) -> None:
    with pytest.raises(NotFound):
        await h.manager.resume("no-such-session")


# --- Answering and the outbox -----------------------------------------------------------


async def test_confirmed_answer_reaches_the_platform_before_saved_is_said(h: Harness) -> None:
    s = await h.login()
    s.drain()
    await h.say(s, i.Confirm())
    assert "you chose option B" in await h.say(s, i.Answer(args=i.ChoiceArgs(choice="B")))
    assert await h.platform_answers() == {}
    assert (await h.say(s, i.Confirm())).startswith("Saved. Question 2 of 60.")
    assert await h.platform_answers() == {1: "B"}
    assert await h.store.pending_writes() == []


async def test_keyboard_answers_the_same_way(h: Harness) -> None:
    s = await h.login()
    await h.manager.handle_key(s, "Enter")
    await h.manager.handle_key(s, "2")
    assert "you chose option B" in said(s)
    await h.manager.handle_key(s, "Enter")
    assert await h.platform_answers() == {1: "B"}


async def test_platform_down_keeps_the_answer_and_syncs_later(h: Harness) -> None:
    s = await h.login()
    said_now = await h.answer(s, 3, "C", platform_down_at_save=True)
    assert "Saved on this computer, waiting to sync" in said_now
    assert len(await h.store.pending_writes()) == 1
    assert s.state.answers == {3: "C"}

    await h.manager.retry_outbox()  # still down: nothing changes
    assert len(await h.store.pending_writes()) == 1

    h.platform.unavailable = False
    await h.manager.retry_outbox()
    assert await h.platform_answers() == {3: "C"}
    assert await h.store.pending_writes() == []
    assert not s.state.unsynced
    assert said(s) == ""  # syncing in the background is silent


async def test_queued_writes_reach_the_platform_in_order(h: Harness) -> None:
    s = await h.login()
    await h.answer(s, 1, "A", platform_down_at_save=True)
    h.platform.unavailable = False
    # A newer answer to the same question, also queued.
    await h.answer(s, 1, "B", platform_down_at_save=True)
    assert len(await h.store.pending_writes()) == 2
    h.platform.unavailable = False
    # The next save must not overtake the queued ones.
    assert (await h.answer(s, 2, "C")).startswith("Saved.")
    assert await h.platform_answers() == {1: "B", 2: "C"}
    assert await h.store.pending_writes() == []


async def test_lost_reply_is_retried_without_saving_twice(h: Harness) -> None:
    s = await h.login()
    h.platform.lose_next_acks = 1
    assert "waiting to sync" in await h.answer(s, 1, "D")
    assert await h.platform_answers() == {1: "D"}  # the platform did save it
    await h.manager.retry_outbox()
    assert await h.store.pending_writes() == []
    assert await h.platform_answers() == {1: "D"}


async def test_save_refused_when_time_is_up(h: Harness) -> None:
    s = await h.login()
    await h.say(s, i.Confirm())
    await h.say(s, i.Answer(args=i.ChoiceArgs(choice="A")))
    h.clock.now += 3601
    said_now = await h.say(s, i.Confirm())
    assert "was not saved. Time is up." in said_now
    assert s.state.phase is m.Phase.ENDED
    assert await h.store.pending_writes() == []


async def test_submission(h: Harness) -> None:
    s = await h.login()
    await h.answer(s, 1, "A")
    await h.say(s, i.Submit())
    await h.say(s, i.Confirm())
    assert await h.say(s, i.Confirm()) == "Your exam has been submitted. Thank you."
    with pytest.raises(AdapterRejected):
        await h.platform.save_answer(CANDIDATE, 2, "A", "late")


# --- Questions --------------------------------------------------------------------------


async def test_question_already_read_survives_a_platform_outage(h: Harness) -> None:
    s = await h.login()
    await h.say(s, i.Confirm())
    h.platform.unavailable = True
    assert (await h.say(s, i.RepeatQuestion())).startswith("Question 1 of 60.")
    assert "question 2 cannot be loaded right now" in await h.say(s, i.Next())
    assert s.state.current == 1


async def test_time_left_comes_from_the_platform(h: Harness) -> None:
    s = await h.login()
    s.drain()
    h.clock.now += 1200
    assert await h.say(s, i.TimeLeft()) == "You have 40 minutes left."
    await h.manager.handle(s, i.WhereAmI(), m.Source.RULES)
    assert last_state(s).time_left_s == 2400
    h.platform.unavailable = True
    assert (await h.say(s, i.TimeLeft())).startswith("I cannot get the time left")


# --- Time warnings ----------------------------------------------------------------------


async def test_time_warnings_once_each_then_time_up(h: Harness) -> None:
    s = await h.login()
    s.drain()
    await h.manager.check_time()
    assert said(s) == ""
    h.clock.now += 3600 - 600  # 10 minutes left
    await h.manager.check_time()
    assert said(s) == "15 minutes left."
    await h.manager.check_time()
    assert said(s) == ""
    h.clock.now += 400  # 3 minutes 20 left
    await h.manager.check_time()
    assert said(s) == "5 minutes left."

    await h.restart()  # warnings already given are not repeated after a restart
    s = await h.manager.resume(s.session_id)
    s.drain()
    await h.manager.check_time()
    assert said(s) == ""

    h.clock.now += 200
    await h.manager.check_time()
    assert said(s) == "Time is up. Your exam has ended."
    assert s.state.phase is m.Phase.ENDED
    await h.manager.check_time()  # ended sessions are left alone
    assert said(s) == ""


async def test_late_start_gives_only_the_nearest_warning(h: Harness) -> None:
    s = await h.login()
    s.drain()
    h.clock.now += 3600 - 120
    await h.manager.check_time()
    assert said(s) == "5 minutes left."


async def test_time_check_skips_when_the_platform_is_down(h: Harness) -> None:
    s = await h.login()
    s.drain()
    h.platform.unavailable = True
    await h.manager.check_time()
    assert said(s) == ""


# --- Restart and resume -----------------------------------------------------------------


async def test_forced_restart_resumes_where_the_candidate_was(h: Harness) -> None:
    s = await h.login()
    await h.answer(s, 14, "B")
    await h.say(s, i.Answer(args=i.ChoiceArgs(choice="C")))  # unconfirmed on question 15
    before = s.state

    await h.restart()
    again = await h.login()
    assert again is not s
    assert said(again).startswith("Welcome back. You were on question 15 of 60.")
    assert again.state.answers == before.answers == {14: "B"}
    assert again.state.pending is None  # the unconfirmed answer was discarded


async def test_restart_with_writes_still_queued(h: Harness) -> None:
    s = await h.login()
    await h.answer(s, 5, "A", platform_down_at_save=True)
    await h.restart()
    h.platform.unavailable = False
    await h.manager.retry_outbox()  # works without the candidate reconnecting
    assert await h.platform_answers() == {5: "A"}
    assert await h.store.pending_writes() == []


async def test_reconnect_gets_a_fresh_output_queue(h: Harness) -> None:
    s = await h.login()
    old = s.output
    await h.manager.resume(s.session_id)
    assert s.output is not old


async def test_full_sixty_question_exam_with_a_restart_halfway(h: Harness) -> None:
    letters = "ABCD"
    s = await h.login()
    for n in range(1, 31):
        await h.answer(s, n, letters[n % 4])
    await h.restart()
    s = await h.login()
    for n in range(31, 61):
        options = len(load_fixture("sample_60").questions[n - 1].options)
        await h.answer(s, n, letters[n % options])
    await h.say(s, i.Submit())
    await h.say(s, i.Confirm())
    assert await h.say(s, i.Confirm()) == "Your exam has been submitted. Thank you."
    assert len(await h.platform_answers()) == 60
    # The log rebuilds exactly the state the live session reached.
    stored = await h.store.load(s.session_id)
    assert m.replay(e.event for e in stored) == s.state


async def test_the_log_holds_no_exam_content(h: Harness) -> None:
    s = await h.login()
    await h.answer(s, 1, "B")
    await h.say(s, i.ReviewAnswers(args=i.MaybeQuestionArgs(question=None)))
    raw = await h.store._run(
        lambda: " ".join(r[0] for r in h.store._conn.execute("SELECT body FROM events"))
    )
    exam = load_fixture("sample_60")
    for q in exam.questions[:5]:
        assert q.text not in raw
        assert all(o.text not in raw for o in q.options if len(o.text) > 3)


# --- Output queue -----------------------------------------------------------------------


async def test_a_client_that_stops_reading_loses_the_oldest_messages(h: Harness) -> None:
    s = await h.login()
    s.drain()
    for n in range(300):
        s.emit(Say(utterance_id=f"u{n}", text=str(n), kind="prompt"))
    messages = s.drain()
    assert len(messages) == 256
    assert isinstance(messages[0], Say) and messages[0].text == "44"


async def test_two_reconnects_at_once_rebuild_the_session_once(h: Harness) -> None:
    s = await h.login()
    await h.restart()
    first, second = await asyncio.gather(
        h.manager.resume(s.session_id), h.manager.resume(s.session_id)
    )
    assert first is second


async def test_restart_during_a_platform_outage(h: Harness) -> None:
    s = await h.login()
    await h.answer(s, 7, "A")
    await h.restart()
    h.platform.unavailable = True
    s = await h.manager.resume(s.session_id)
    assert "question 8 cannot be loaded right now" in said(s)
    # Nothing cached in the new process: the candidate is told, not left in silence.
    assert "question 8 cannot be loaded right now" in await h.say(s, i.RepeatQuestion())
    assert s.state.answers == {7: "A"}


# --- Typed sentences through the rules --------------------------------------------------


async def test_typed_sentences_drive_the_exam(h: Harness) -> None:
    s = await h.login()
    s.drain()

    async def type_(text: str) -> str:
        await h.manager.handle_text(s, text)
        return said(s)

    assert (await type_("Start from question 50")).startswith("Question 50 of 60.")
    assert "you chose option D: photosynthesis" in await type_("the one that says photosynthesis")
    assert (await type_("yes")).startswith("Saved. Question 51 of 60.")
    assert (await type_("go to physics")).startswith("Question 31 of 60.")
    assert await type_("what did I put for 50") == "For question 50 you chose D."
    assert await type_("which one is correct?") == (
        "I can only read the questions and options, and record your answers."
    )
    assert (await type_("blah blah")).startswith("Sorry, I did not catch that.")
    assert await h.platform_answers() == {50: "D"}


async def test_overlong_text_is_not_acted_on(h: Harness) -> None:
    s = await h.login()
    await h.manager.handle_text(s, "Confirm")  # starts the exam
    s.drain()
    await h.manager.handle_text(s, "next " * 100)
    assert said(s).startswith("Sorry, I did not catch that.")
    assert s.state.current == 1
    stored = await h.store.load(s.session_id)
    last = stored[-1].event
    assert isinstance(last, m.Understood) and last.text is not None and len(last.text) == 300


# --- The language model path ------------------------------------------------------------


class ScriptedModel:
    """Stands in for LlmUnderstander: scripted answers, and a record of what it was shown."""

    def __init__(self, answers: dict[str, i.Intent | LlmFallback]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, LlmContext]] = []

    async def understand(self, session_id: str, text: str, ctx: LlmContext) -> i.Intent:
        self.calls.append((text, ctx))
        answer = self.answers.get(text, i.Unclear())
        if isinstance(answer, LlmFallback):
            raise answer
        return answer


async def with_model(h: Harness, model: ScriptedModel) -> LiveSession:
    h.manager = SessionManager(h.store, h.platform, understander=model)
    s = await h.login()
    await h.manager.handle_text(s, "go to question 50")
    s.drain()
    return s


async def test_rules_first_then_the_model(h: Harness) -> None:
    model = ScriptedModel(
        {"hmm I reckon it is probably B": i.Answer(args=i.ChoiceArgs(choice="B"))}
    )
    s = await with_model(h, model)
    await h.manager.handle_text(s, "next")
    assert model.calls == []  # the rules knew that one
    await h.manager.handle_text(s, "go to question 50")
    await h.manager.handle_text(s, "hmm I reckon it is probably B")
    assert "you chose option B: digestion" in said(s)
    assert model.calls[0][1] == LlmContext(question=50, of=60, option_count=4, awaiting=None)
    stored = (await h.store.load(s.session_id))[-1].event
    assert isinstance(stored, m.Understood) and stored.source is m.Source.LLM
    assert stored.latency_ms is not None and stored.fallback is None


async def test_the_model_is_told_what_yes_would_confirm(h: Harness) -> None:
    model = ScriptedModel({"absolutely, lock it in": i.Confirm()})
    s = await with_model(h, model)
    await h.manager.handle_text(s, "B")
    await h.manager.handle_text(s, "absolutely, lock it in")
    assert model.calls[0][1].awaiting == "answer"
    # Only an explicit yes saves: the model's "confirm" is not enough on its own.
    assert "To confirm, please say yes, or press Enter." in said(s)
    assert await h.platform_answers() == {}
    await h.manager.handle_text(s, "yes")
    assert await h.platform_answers() == {50: "B"}


async def test_whatever_the_model_says_still_needs_confirmation(h: Harness) -> None:
    model = ScriptedModel({"I am through, I want to hand in": i.Submit()})
    s = await with_model(h, model)
    await h.manager.handle_text(s, "I am through, I want to hand in")
    assert "Do you want to submit your exam?" in said(s)
    assert s.state.phase is m.Phase.CONFIRM_SUBMIT


async def test_the_model_cannot_choose_an_answer_for_the_candidate(h: Harness) -> None:
    model = ScriptedModel({"maybe that one": i.Answer(args=i.ChoiceArgs(choice="C"))})
    s = await with_model(h, model)
    await h.manager.handle_text(s, "maybe that one")
    assert said(s).startswith("Sorry, I did not catch that.")
    assert s.state.pending is None
    stored = (await h.store.load(s.session_id))[-1].event
    assert isinstance(stored, m.Understood) and stored.fallback == "ungrounded"


async def test_model_refusal_is_the_neutral_reply(h: Harness) -> None:
    s = await with_model(h, ScriptedModel({"would a botanist pick D": i.NotAllowed()}))
    await h.manager.handle_text(s, "would a botanist pick D")
    assert said(s) == "I can only read the questions and options, and record your answers."


@pytest.mark.parametrize("reason", ["busy", "rate_limited", "unavailable", "timeout"])
async def test_model_fallback_is_rules_only_and_logged(h: Harness, reason: str) -> None:
    s = await with_model(h, ScriptedModel({"something odd": LlmFallback(reason)}))  # type: ignore[arg-type]
    await h.manager.handle_text(s, "something odd")
    assert said(s).startswith("Sorry, I did not catch that.")
    stored = (await h.store.load(s.session_id))[-1].event
    assert isinstance(stored, m.Understood) and stored.fallback == reason
    assert stored.source is m.Source.RULES


async def test_without_a_model_unknown_sentences_are_not_understood(h: Harness) -> None:
    s = await h.login()
    await h.manager.handle_text(s, "hmm I reckon it is probably B")
    stored = (await h.store.load(s.session_id))[-1].event
    assert isinstance(stored, m.Understood) and stored.fallback == "disabled"


async def test_overlong_sentences_never_reach_the_model(h: Harness) -> None:
    model = ScriptedModel({})
    s = await with_model(h, model)
    await h.manager.handle_text(s, "word " * 100)
    assert model.calls == []


# --- Degradation ------------------------------------------------------------------------


async def test_at_rules_only_the_model_is_not_asked(h: Harness) -> None:
    model = ScriptedModel(
        {"hmm I reckon it is probably B": i.Answer(args=i.ChoiceArgs(choice="B"))}
    )
    s = await with_model(h, model)
    h.manager.set_level(Level.RULES_ONLY)
    await h.manager.handle_text(s, "hmm I reckon it is probably B")
    assert model.calls == []
    assert said(s).startswith("Sorry, I did not catch that.")
    stored = (await h.store.load(s.session_id))[-1].event
    assert isinstance(stored, m.Understood) and stored.fallback == "degraded"
    await h.manager.handle_text(s, "B")  # the rules still work
    assert "you chose option B" in said(s)


@pytest.mark.parametrize(
    ("reason", "counted"),
    [("busy", 1), ("timeout", 1), ("rate_limited", 0), ("unavailable", 0)],
)
async def test_only_overload_counts_against_the_model(
    h: Harness, reason: str, counted: int
) -> None:
    s = await with_model(h, ScriptedModel({"odd": LlmFallback(reason)}))  # type: ignore[arg-type]
    await h.manager.handle_text(s, "odd")
    assert h.manager.recent_model_refusals() == counted


async def test_keyboard_mode_is_announced_once_each_way(h: Harness) -> None:
    s = await h.login()
    s.drain()
    h.manager.set_level(Level.RULES_ONLY)
    h.manager.set_level(Level.FIXED_PROMPTS)
    assert said(s) == ""  # lower levels change behaviour silently
    h.manager.set_level(Level.KEYBOARD)
    assert said(s).startswith("Voice is not available for a moment.")
    h.manager.set_level(Level.KEYBOARD)
    assert said(s) == ""
    h.manager.set_level(Level.RULES_ONLY)
    assert said(s) == "Voice is back. You can speak again."


async def test_finished_exams_hear_no_announcements(h: Harness) -> None:
    s = await h.login()
    await h.say(s, i.Confirm())
    await h.say(s, i.Submit())
    await h.say(s, i.Confirm())
    await h.say(s, i.Confirm())
    s.drain()
    h.manager.set_level(Level.KEYBOARD)
    assert said(s) == ""


# --- Sound cues -------------------------------------------------------------------------


def cues(s: LiveSession) -> list[Cue]:
    return [m for m in s.drain() if isinstance(m, Cue)]


async def test_saves_sound_a_cue_and_stop_being_spoken_after_twice(h: Harness) -> None:
    s = await h.login()
    heard_saved = []
    for n in (1, 2, 3, 4):
        await h.say(s, i.GoTo(args=i.QuestionArgs(question=n)))
        await h.say(s, i.Answer(args=i.ChoiceArgs(choice="A")))
        await h.manager.handle(s, i.Confirm(), m.Source.RULES)
        messages = s.drain()
        assert Cue(name="saved", volume=0.75) in messages
        heard_saved.append(any(isinstance(x, Say) and x.text == "Saved." for x in messages))
    assert heard_saved == [True, True, False, False]


async def test_not_understood_and_time_warnings_sound_a_cue_and_still_speak(h: Harness) -> None:
    s = await h.login()
    await h.manager.handle_text(s, "blah blah")
    messages = s.drain()
    assert Cue(name="not_understood", volume=0.75) in messages
    assert any(isinstance(x, Say) and x.text.startswith("Sorry") for x in messages)
    h.clock.now += 3600 - 600
    await h.manager.check_time()
    messages = s.drain()
    assert Cue(name="time_warning", volume=0.75) in messages
    assert any(isinstance(x, Say) and x.text == "15 minutes left." for x in messages)


async def test_cues_follow_the_candidates_volume(h: Harness) -> None:
    s = await h.login()
    await h.say(s, i.QuieterCues())
    await h.say(s, i.QuieterCues())
    await h.manager.handle_text(s, "blah blah")
    assert cues(s) == [Cue(name="not_understood", volume=0.25)]


class SlowModel(ScriptedModel):
    def __init__(self, answers: dict[str, i.Intent | LlmFallback], seconds: float) -> None:
        super().__init__(answers)
        self.seconds = seconds

    async def understand(self, session_id: str, text: str, ctx: LlmContext) -> i.Intent:
        await asyncio.sleep(self.seconds)
        return await super().understand(session_id, text, ctx)


async def test_a_slow_model_is_covered_by_one_moment(h: Harness) -> None:
    s = await with_model(h, SlowModel({"read it once more": i.RepeatQuestion()}, 1.3))
    await h.manager.handle_text(s, "read it once more")
    messages = [x.text for x in s.drain() if isinstance(x, Say)]
    assert messages[0] == "One moment."  # said while the model was still working
    assert messages[1].startswith("Question 50 of 60.")


async def test_a_quick_model_needs_no_one_moment(h: Harness) -> None:
    s = await with_model(h, SlowModel({"read it once more": i.RepeatQuestion()}, 0.0))
    await h.manager.handle_text(s, "read it once more")
    assert "One moment." not in said(s)


async def test_nothing_heard_is_said_so_and_is_not_a_misunderstanding(h: Harness) -> None:
    s = await h.login()
    s.drain()
    for _ in range(3):
        await h.manager.handle_text(s, "", confidence=0.0)
        assert said(s) == "I did not hear anything. Hold Space while you speak, then let go."
    assert s.state.unclear_streak == 0
    assert all(not isinstance(e.event, m.Understood) for e in await h.store.load(s.session_id))


async def test_one_timeout_does_not_switch_the_model_off(h: Harness) -> None:
    model = ScriptedModel({"read it once more": LlmFallback("timeout")})
    s = await with_model(h, model)
    await h.manager.handle_text(s, "read it once more")
    assert h.manager.recent_model_refusals() == 1
    assert level_for(Load(True, h.manager.recent_model_refusals())) is Level.NORMAL
