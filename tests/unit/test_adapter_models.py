from typing import Any

import pytest
from pydantic import ValidationError

from voice_agent.adapters.base import ExamOutline, Question

OPTIONS = [{"letter": "A", "text": "Lagos"}, {"letter": "B", "text": "Abuja"}]


def question(**overrides: Any) -> dict[str, Any]:
    return {"number": 1, "text": "What is the capital of Nigeria?", "options": OPTIONS} | overrides


def outline(sections: list[tuple[int, int, int]], count: int = 10) -> dict[str, Any]:
    return {
        "exam_id": "e-1",
        "question_count": count,
        "duration_s": 600,
        "sections": [
            {"number": n, "title": f"Section {n}", "first_question": a, "last_question": b}
            for n, a, b in sections
        ],
    }


def test_question_parses() -> None:
    q = Question.model_validate(question(spoken_text="What is the capital city of Nigeria?"))
    assert [o.letter for o in q.options] == ["A", "B"]


@pytest.mark.parametrize(
    "field", ["answer", "correct_option", "answer_key", "is_correct", "explanation"]
)
def test_question_with_any_extra_field_is_refused(field: str) -> None:
    # The boundary closes the door on answer keys whatever the platform calls them.
    with pytest.raises(ValidationError):
        Question.model_validate(question(**{field: "B"}))


def test_option_with_extra_field_is_refused() -> None:
    options = [OPTIONS[0], {"letter": "B", "text": "Abuja", "correct": True}]
    with pytest.raises(ValidationError):
        Question.model_validate(question(options=options))


@pytest.mark.parametrize(
    "options",
    [
        [{"letter": "A", "text": "only one"}],
        [{"letter": "B", "text": "x"}, {"letter": "A", "text": "y"}],
        [{"letter": "A", "text": "x"}, {"letter": "C", "text": "y"}],
        [{"letter": "A", "text": "x"}, {"letter": "A", "text": "y"}],
        [{"letter": "A", "text": "   "}, {"letter": "B", "text": "y"}],
    ],
)
def test_badly_formed_options_are_refused(options: list[dict[str, str]]) -> None:
    with pytest.raises(ValidationError):
        Question.model_validate(question(options=options))


def test_question_text_has_a_size_limit() -> None:
    with pytest.raises(ValidationError):
        Question.model_validate(question(text="x" * 4001))


def test_outline_without_sections_is_valid() -> None:
    assert ExamOutline.model_validate(outline([])).sections == ()


def test_outline_sections_tiling_the_exam_are_valid() -> None:
    assert len(ExamOutline.model_validate(outline([(1, 1, 4), (2, 5, 10)])).sections) == 2


@pytest.mark.parametrize(
    "sections",
    [
        [(1, 2, 10)],  # does not start at question 1
        [(1, 1, 4), (2, 6, 10)],  # gap
        [(1, 1, 5), (2, 5, 10)],  # overlap
        [(1, 1, 4), (2, 5, 9)],  # stops short
        [(1, 1, 4), (3, 5, 10)],  # numbering skips
        [(1, 1, 4), (2, 5, 11)],  # runs past the end
        [(1, 5, 4), (2, 5, 10)],  # inverted range
    ],
)
def test_outline_sections_must_tile_the_exam(sections: list[tuple[int, int, int]]) -> None:
    with pytest.raises(ValidationError):
        ExamOutline.model_validate(outline(sections))
