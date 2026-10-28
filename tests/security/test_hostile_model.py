"""Assume the language model is completely compromised: for any sentence, in any state, it
can return any action at all. Nothing may be saved or submitted because of it.

These run on the pure state machine, so they can be exhaustive: every action the model
could name, two turns in a row, from every state a candidate can be in.
"""

import random
from collections.abc import Callable
from typing import Any

import pytest

from tests.unit.script import Script
from tests.unit.test_intents import one_of_each
from voice_agent.adapters.fake_adapter import load_fixture
from voice_agent.session import machine as m
from voice_agent.session.replies import SayYesToConfirm
from voice_agent.understanding import intents as i

EXAM = load_fixture("sample_60")
INTENTS: list[Any] = one_of_each()
Write = m.SaveAnswer | m.SubmitExam


def run(state: m.SessionState, event: m.Event, writes: list[Write]) -> m.SessionState:
    """Step the machine and carry out its effects, like the manager, recording writes."""
    state, effects = m.step(state, event)
    for effect in effects:
        match effect:
            case m.Load(question=n):
                count = len(EXAM.questions[n - 1].options)
                state = run(state, m.QuestionLoaded(question=n, option_count=count), writes)
            case m.SaveAnswer() | m.SubmitExam():
                writes.append(effect)
            case m.FetchTimeLeft():
                state = run(state, m.TimeLeftKnown(seconds=600), writes)
            case m.Speak():
                pass
    return state


def scenario(setup: Callable[[Script], object]) -> m.SessionState:
    s = Script()
    setup(s)
    return s.state


def _confirming_clear(s: Script) -> None:
    s.answer_and_save(3, "B")
    s.say(i.ClearAnswer(args=i.MaybeQuestionArgs(question=3)))


def _confirming_submit(s: Script, confirmations: int) -> None:
    s.begin()
    s.say(i.Submit())
    for _ in range(confirmations):
        s.yes()


SCENARIOS = {
    "intro": scenario(lambda s: None),
    "listening": scenario(lambda s: s.begin()),
    "confirming an answer": scenario(lambda s: (s.begin(), s.answer("B"))),
    "confirming a clear": scenario(_confirming_clear),
    "confirming submit": scenario(lambda s: _confirming_submit(s, 0)),
    "confirming submit, final": scenario(lambda s: _confirming_submit(s, 1)),
}


def from_model(intent: Any) -> m.Understood:
    return m.Understood(intent=intent, source=m.Source.LLM, text="<anything at all>")


@pytest.mark.parametrize("name", SCENARIOS)
def test_no_two_model_turns_can_save_or_submit(name: str) -> None:
    start = SCENARIOS[name]
    for first in INTENTS:
        writes: list[Write] = []
        after_first = run(start, from_model(first), writes)
        for second in INTENTS:
            run(after_first, from_model(second), writes)
        assert writes == [], (name, first.action)


@pytest.mark.parametrize("name", SCENARIOS)
def test_the_model_cannot_end_the_exam(name: str) -> None:
    for first in INTENTS:
        writes: list[Write] = []
        state = run(SCENARIOS[name], from_model(first), writes)
        assert state.phase not in (m.Phase.SUBMITTING, m.Phase.ENDED)


def test_a_model_confirm_asks_for_an_explicit_yes() -> None:
    state, effects = m.step(SCENARIOS["confirming an answer"], from_model(i.Confirm()))
    assert state.phase is m.Phase.CONFIRM_ANSWER
    assert effects == (m.Speak(SayYesToConfirm()),)


# --- Random conversations ---------------------------------------------------------------

_INERT = (*m._PASSIVE, i.Cancel)


def _counts(intent: Any, source: m.Source) -> bool:
    """Turns that can move a confirmation forward: not passive, not a model's "yes"."""
    if isinstance(intent, i.Confirm):
        return source is not m.Source.LLM
    return not isinstance(intent, _INERT)


SEEDS = range(40)


@pytest.mark.parametrize("seed", SEEDS)
def test_random_conversations_only_write_after_explicit_confirmation(seed: int) -> None:
    walk(seed)


def test_random_conversations_do_reach_saves_and_submissions() -> None:
    # Otherwise the property above could pass by never being exercised.
    saves = submits = 0
    for seed in SEEDS:
        writes = walk(seed)
        saves += sum(isinstance(w, m.SaveAnswer) for w in writes)
        submits += sum(isinstance(w, m.SubmitExam) for w in writes)
    assert saves >= 100 and submits >= 5, (saves, submits)


def walk(seed: int) -> list[Write]:
    """A random conversation; asserts the confirmation rule at every write."""
    rng = random.Random(seed)
    all_writes: list[Write] = []
    state = SCENARIOS["listening"]
    history: list[tuple[Any, m.Source]] = []
    sources = [m.Source.RULES, m.Source.KEY, m.Source.LLM]
    for _ in range(300):
        if state.phase is m.Phase.ENDED:
            break
        # Confirmations are common in real use; weight them so long chains happen.
        intent = i.Confirm() if rng.random() < 0.3 else rng.choice(INTENTS)
        source = rng.choice(sources)
        writes: list[Write] = []
        state = run(state, m.Understood(intent=intent, source=source), writes)
        if _counts(intent, source):
            history.append((intent, source))
        all_writes.extend(writes)
        for write in writes:
            # Settle every write at once, as a healthy platform would.
            state = run(state, m.WriteOutcome(operation_id=write.operation_id, outcome="saved"), [])
            assert isinstance(intent, i.Confirm) and source is not m.Source.LLM
            if isinstance(write, m.SubmitExam):
                last = [type(x) for x, _ in history[-3:]]
                assert last == [i.Submit, i.Confirm, i.Confirm], history[-5:]
            else:
                proposal, _ = history[-2]
                assert isinstance(proposal, i.Answer | i.ChangeAnswer | i.ClearAnswer)
    return all_writes
