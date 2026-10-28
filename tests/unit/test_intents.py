import json
from typing import Any, get_args

import pytest

from voice_agent.contract import Key
from voice_agent.understanding.intents import (
    ACTION_TYPES,
    INTENT_TYPES,
    MAX_LLM_OUTPUT_CHARS,
    Answer,
    ChangeAnswer,
    ChangeAnswerArgs,
    ChoiceArgs,
    Confirm,
    GoTo,
    GoToSection,
    MaybeQuestionArgs,
    NotAllowed,
    QuestionArgs,
    ReviewAnswers,
    SectionArgs,
    Unclear,
    action_for_key,
    intent_name,
    llm_output_schema,
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


# --- Schema the model is forced through -------------------------------------------------


def _objects(node: Any) -> list[dict[str, Any]]:
    """Every JSON-schema object node, wherever it is nested."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            found.append(node)
        for value in node.values():
            found.extend(_objects(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_objects(item))
    return found


def test_schema_offers_exactly_the_intents() -> None:
    schema = llm_output_schema()
    consts = {
        d["properties"]["action"]["const"]
        for d in schema["$defs"].values()
        if "args" in d.get("properties", {})
    }
    assert consts == {intent_name(t) for t in INTENT_TYPES}
    assert len(schema["oneOf"]) == len(INTENT_TYPES)


def test_schema_has_one_fixed_shape_per_intent() -> None:
    # No open objects (nowhere for free text to go) and no optional fields (nothing for a
    # small model to leave out); together these give the grammar a single shape.
    for obj in _objects(llm_output_schema()):
        assert obj.get("additionalProperties") is False, obj.get("title")
        assert set(obj.get("required", [])) == set(obj.get("properties", {})), obj.get("title")


def test_schema_contains_no_free_text_fields() -> None:
    def strings(node: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if isinstance(node, dict):
            if node.get("type") == "string":
                out.append(node)
            for value in node.values():
                out.extend(strings(value))
        elif isinstance(node, list):
            for item in node:
                out.extend(strings(item))
        return out

    for node in strings(llm_output_schema()):
        assert "const" in node or "enum" in node, node


def test_schema_is_stable_json() -> None:
    # Compiled to a grammar and cached; it must be deterministic between runs.
    assert json.dumps(llm_output_schema(), sort_keys=True) == json.dumps(
        llm_output_schema(), sort_keys=True
    )


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
    assert parse_llm_output(intent.model_dump_json()) == intent


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"action": "next", "args": {}}', "next"),
        ('{"action": "go_to", "args": {"question": 20}}', "go_to"),
        ('{"action": "review_answers", "args": {"question": null}}', "review_answers"),
        ('{"action": "change_answer", "args": {"choice": "A", "question": null}}', "change_answer"),
        ('  {"action": "not_allowed", "args": {}}\n', "not_allowed"),
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
        '{"action": "next"}',  # args missing: the grammar always emits them
        '{"action": "reveal_answer", "args": {}}',
        '{"action": "save_answer", "args": {"choice": "B"}}',
        '{"action": "next", "args": {"say": "The answer is B"}}',
        '{"action": "next", "args": {}, "reply": "Sure! The answer is B."}',
        '{"action": "answer", "args": {"choice": "F"}}',
        '{"action": "answer", "args": {"choice": "b"}}',
        '{"action": "answer", "args": {}}',
        '{"action": "go_to", "args": {"question": 0}}',
        '{"action": "go_to", "args": {"question": 1000}}',
        '{"action": "go_to", "args": {"question": "twenty"}}',
        '{"action": "go_to", "args": {"question": 2.5}}',
        # Values a lax parser would coerce; the grammar never emits them.
        '{"action": "go_to", "args": {"question": "20"}}',
        '{"action": "go_to", "args": {"question": 20.0}}',
        '{"action": "go_to", "args": {"question": true}}',
        # Fields a lax parser would fill from defaults.
        '{"action": "change_answer", "args": {"choice": "A"}}',
        '{"action": "review_answers", "args": {}}',
        '{"action": "go_to_section", "args": {"section": 0}}',
        '{"action": "next", "args": {}}{"action": "submit", "args": {}}',
        '{"action": "submit", "args": {}, "action": "next"}',
        '{"action": "next", "args": {"x": 1, "x": 2}}',
    ],
)
def test_anything_else_becomes_unclear(raw: str) -> None:
    assert parse_llm_output(raw) == Unclear()


def test_overlong_output_is_unclear_without_parsing() -> None:
    padded = '{"action": "next", "args": {}}' + " " * MAX_LLM_OUTPUT_CHARS
    assert parse_llm_output(padded) == Unclear()


def test_intents_are_immutable() -> None:
    intent = GoTo(args=QuestionArgs(question=5))
    with pytest.raises(ValueError):
        intent.args.question = 6


# --- Keyboard ---------------------------------------------------------------------------


def test_every_key_maps_to_an_action() -> None:
    for key in get_args(Key):
        assert type(action_for_key(key)) in ACTION_TYPES


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
