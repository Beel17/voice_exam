import re
from typing import Any, get_args

import pytest

from voice_agent.contract import Key
from voice_agent.understanding.intents import (
    ACTION_TYPES,
    INTENT_TYPES,
    LLM_OUTPUT_PREFIX,
    MAX_LLM_OUTPUT_CHARS,
    Answer,
    ChangeAnswer,
    ChangeAnswerArgs,
    ChoiceArgs,
    Confirm,
    GoTo,
    GoToSection,
    LocalAction,
    MaybeQuestionArgs,
    NotAllowed,
    QuestionArgs,
    ReviewAnswers,
    SectionArgs,
    Unclear,
    action_for_key,
    intent_name,
    llm_output_grammar,
    llm_output_text,
    parse_llm_output,
)

# The action list exactly as the design doc gives it (Exam integrity and security, §1).
DOC_ACTIONS = {
    "answer", "confirm", "cancel", "change_answer", "clear_answer",
    "next", "previous", "go_to", "next_unanswered", "go_to_section", "first", "last",
    "repeat_question", "repeat_options", "repeat_option", "spell_option",
    "where_am_i", "time_left", "count_answered", "list_unanswered", "review_answers",
    "faster", "slower", "normal_speed", "louder_cues", "quieter_cues",
    "submit", "help",
}  # fmt: skip


def test_action_list_matches_the_design_doc_exactly() -> None:
    names = [intent_name(t) for t in ACTION_TYPES]
    assert len(names) == len(set(names))
    assert set(names) == DOC_ACTIONS


def test_unclear_and_not_allowed_are_outcomes_not_actions() -> None:
    assert Unclear not in ACTION_TYPES
    assert NotAllowed not in ACTION_TYPES
    assert {intent_name(t) for t in INTENT_TYPES} == DOC_ACTIONS | {"unclear", "not_allowed"}


def test_every_action_has_a_prompt_description() -> None:
    for t in INTENT_TYPES:
        assert t.__doc__, intent_name(t)
        assert len(t.__doc__) < 100, "descriptions go into every prompt; keep them short"


# --- Grammar the model is forced through ------------------------------------------------


def test_grammar_offers_exactly_the_intents() -> None:
    grammar = llm_output_grammar()
    root = grammar.splitlines()[0]
    assert root.count(" | ") == len(INTENT_TYPES) - 1
    for t in INTENT_TYPES:
        assert f'("{intent_name(t)}\\"' in root, intent_name(t)


def test_grammar_has_no_free_text() -> None:
    # Three rules, and the only character classes are option letters and digits: there is
    # nowhere in the output for a word of the model's own.
    grammar = llm_output_grammar()
    assert [line.split(" ::= ")[0] for line in grammar.splitlines()] == ["root", "letter", "number"]
    assert set(re.findall(r"\[[^\]]*\]", grammar)) == {"[A-E]", "[1-9]", "[0-9]"}


def test_grammar_is_stable() -> None:
    # Sent with every request; llama.cpp compiles it each time, so it must not vary.
    assert llm_output_grammar() == llm_output_grammar()


# --- Parsing model output ---------------------------------------------------------------


def one_of_each() -> list[Any]:
    choice = ChoiceArgs(choice="C")
    samples: dict[type[Any], Any] = {
        Answer: Answer(args=choice),
        ChangeAnswer: ChangeAnswer(args=ChangeAnswerArgs(choice="A", question=12)),
        GoTo: GoTo(args=QuestionArgs(question=20)),
        GoToSection: GoToSection(args=SectionArgs(section=2)),
        ReviewAnswers: ReviewAnswers(args=MaybeQuestionArgs(question=12)),
    }
    for t in INTENT_TYPES:
        if t not in samples:
            fields = t.model_fields["args"]
            samples[t] = t() if not fields.is_required() else t(args=choice)  # type: ignore[call-arg]
    return list(samples.values())


@pytest.mark.parametrize("intent", one_of_each(), ids=lambda i: i.action)
def test_every_intent_round_trips_through_the_parser(intent: Any) -> None:
    text = llm_output_text(intent)
    assert text.startswith(LLM_OUTPUT_PREFIX)
    assert parse_llm_output(text) == intent


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"action": "next"}', "next"),
        ('{"action": "go_to", "question": 20}', "go_to"),
        ('{"action": "review_answers"}', "review_answers"),
        ('{"action": "review_answers", "question": 12}', "review_answers"),
        ('{"action": "change_answer", "choice": "A"}', "change_answer"),
        ('{"action": "change_answer", "choice": "A", "question": 7}', "change_answer"),
        ('  {"action": "not_allowed"}\n', "not_allowed"),
    ],
)
def test_valid_model_output_parses(raw: str, expected: str) -> None:
    assert parse_llm_output(raw).action == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "next",
        "The answer is B.",
        "null",
        "[]",
        "{}",
        '{"action": 5}',
        '{"action": "reveal_answer"}',
        '{"action": "save_answer", "choice": "B"}',
        '{"action": "next", "say": "The answer is B"}',
        '{"action": "next", "reply": "Sure! The answer is B."}',
        '{"action": "answer", "choice": "F"}',
        '{"action": "answer", "choice": "b"}',
        '{"action": "answer"}',
        '{"action": "go_to", "question": 0}',
        '{"action": "go_to", "question": 1000}',
        '{"action": "go_to", "question": "twenty"}',
        '{"action": "go_to", "question": 2.5}',
        # The old nested form, and values a lax parser would coerce; the grammar never
        # emits them.
        '{"action": "next", "args": {}}',
        '{"action": "go_to", "args": {"question": 20}}',
        '{"action": "go_to", "question": "20"}',
        '{"action": "go_to", "question": 20.0}',
        '{"action": "go_to", "question": true}',
        # Nulls and other spellings of the canonical text.
        '{"action": "review_answers", "question": null}',
        '{"action": "change_answer", "choice": "A", "question": null}',
        '{"action": "change_answer", "question": 7, "choice": "A"}',
        '{"action":"next"}',
        '{"choice": "A", "action": "answer"}',
        '{"action": "go_to_section", "section": 0}',
        '{"action": "next"}{"action": "submit"}',
        '{"action": "submit", "action": "next"}',
        '{"action": "go_to", "question": 1, "question": 2}',
    ],
)
def test_anything_else_becomes_unclear(raw: str) -> None:
    assert parse_llm_output(raw) == Unclear()


def test_overlong_output_is_unclear_without_parsing() -> None:
    padded = '{"action": "next"}' + " " * MAX_LLM_OUTPUT_CHARS
    assert parse_llm_output(padded) == Unclear()


def test_intents_are_immutable() -> None:
    intent = GoTo(args=QuestionArgs(question=5))
    with pytest.raises(ValueError):
        intent.args.question = 6


# --- Keyboard ---------------------------------------------------------------------------


def test_every_key_maps_to_an_action() -> None:
    # An action the model knows, or one only rules, keys and the screen produce (flag).
    actions = set(ACTION_TYPES) | set(get_args(LocalAction))
    for key in get_args(Key):
        assert type(action_for_key(key)) in actions


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("1", Answer(args=ChoiceArgs(choice="A"))),
        ("5", Answer(args=ChoiceArgs(choice="E"))),
        ("Enter", Confirm()),
    ],
)
def test_key_mapping(key: Key, expected: Any) -> None:
    assert action_for_key(key) == expected
