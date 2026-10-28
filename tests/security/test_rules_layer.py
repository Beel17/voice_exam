"""The rules never act on an attack: they refuse it or leave it to the model."""

import pytest

from tests.security.corpus import ATTACKS, NIGERIAN_ENGLISH
from voice_agent.adapters.fake_adapter import load_fixture
from voice_agent.understanding import intents as i
from voice_agent.understanding.grammar import RulesContext, match


def context(exam: str, question: int) -> RulesContext:
    q = load_fixture(exam).questions[question - 1]
    return RulesContext(
        option_texts=tuple(
            (o.text,) + ((o.spoken_text,) if o.spoken_text else ()) for o in q.options
        ),
        section_titles=tuple(s.title for s in load_fixture(exam).outline.sections),
    )


CONTEXTS = [
    RulesContext(),
    context("sample_60", 50),
    context("injection", 2),
    context("injection", 4),
]


@pytest.mark.parametrize("text", ATTACKS, ids=lambda t: repr(t[:40]))
@pytest.mark.parametrize("ctx", CONTEXTS, ids=["intro", "q50", "inj2", "inj4"])
def test_attacks_are_refused_or_left_to_the_model(text: str, ctx: RulesContext) -> None:
    assert match(text, ctx) in (None, i.NotAllowed())


@pytest.mark.parametrize(
    ("text", "expected"), NIGERIAN_ENGLISH, ids=[t for t, _ in NIGERIAN_ENGLISH]
)
def test_nigerian_english_is_understood_or_left_to_the_model(text: str, expected: i.Intent) -> None:
    assert match(text, context("sample_60", 50)) in (None, expected)


def test_most_nigerian_english_needs_no_model() -> None:
    known = sum(match(t, context("sample_60", 50)) == e for t, e in NIGERIAN_ENGLISH)
    assert known >= len(NIGERIAN_ENGLISH) * 2 // 3, f"rules know {known}/{len(NIGERIAN_ENGLISH)}"
