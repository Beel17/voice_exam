"""The exam screen (standard mode): clicks, the question navigator, and revisit flags.

Flags are spoken as well as clicked, so they are tested here for both.
"""

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from tests.unit.script import Script
from voice_agent.contract import Act, Login, parse_client_message
from voice_agent.session import machine as m
from voice_agent.understanding import intents as i
from voice_agent.understanding.grammar import match

Make = Callable[..., Script]


def select(question: int, choice: str | None) -> i.Select:
    return i.Select(args=i.SelectArgs(question=question, choice=choice))  # type: ignore[arg-type]


def flag(question: int | None = None) -> i.Flag:
    return i.Flag(args=i.MaybeQuestionArgs(question=question))


def unflag(question: int | None = None) -> i.Unflag:
    return i.Unflag(args=i.MaybeQuestionArgs(question=question))


def started(s: Script) -> Script:
    s.begin()
    return s


# --- Selecting an option ----------------------------------------------------------------


def test_a_click_saves_at_once_and_stays_on_the_question(script: Make) -> None:
    s = started(script())
    assert s.click(select(1, "B")) == "Saved."
    assert s.writes == [m.SaveAnswer(1, "B", s.writes[0].operation_id)]
    assert s.state.answers == {1: "B"}
    assert s.state.current == 1 and s.phase() is m.Phase.LISTEN


def test_clicking_another_option_changes_the_answer(script: Make) -> None:
    s = started(script())
    s.click(select(1, "B"))
    s.click(select(1, "D"))
    assert s.state.answers == {1: "D"} and len(s.writes) == 2


def test_clicking_the_chosen_option_again_writes_nothing(script: Make) -> None:
    s = started(script())
    s.click(select(1, "B"))
    assert s.click(select(1, "B")) == ""
    assert len(s.writes) == 1


def test_clearing_by_click_needs_no_yes(script: Make) -> None:
    s = started(script())
    s.click(select(1, "B"))
    assert "Question 1 is now unanswered" in s.click(select(1, None))
    assert s.state.answers == {}
    assert s.click(select(1, None)) == ""  # already unanswered: nothing to write
    assert len(s.writes) == 2


def test_a_click_for_a_question_not_on_screen_changes_nothing(script: Make) -> None:
    s = started(script())
    assert "Question 7 is not the current question" in s.click(select(7, "A"))
    assert s.writes == [] and s.state.answers == {}


def test_a_click_before_the_exam_starts_changes_nothing(script: Make) -> None:
    s = script()
    assert "not the current question" in s.click(select(1, "A"))
    assert s.writes == [] and s.phase() is m.Phase.INTRO


def test_a_click_for_an_option_the_question_lacks(script: Make) -> None:
    s = started(script())
    assert "There is no option E" in s.click(select(1, "E"))
    assert s.writes == []


@pytest.mark.parametrize("source", [m.Source.RULES, m.Source.LLM, m.Source.KEY])
def test_only_a_click_saves_without_a_yes(script: Make, source: m.Source) -> None:
    s = started(script())
    said = s.feed(m.Understood(intent=select(1, "B"), source=source))
    assert "I can only read the questions" in said
    assert s.writes == [] and s.state.answers == {}


def test_a_click_while_submit_is_pending_drops_the_submission(script: Make) -> None:
    s = started(script())
    s.click(i.Submit())
    said = s.click(select(1, "C"))
    assert said.startswith("Not submitted.") and said.endswith("Saved.")
    assert s.phase() is m.Phase.LISTEN and s.state.answers == {1: "C"}


def test_submitting_by_click_still_asks_twice(script: Make) -> None:
    s = started(script())
    s.click(i.Submit())
    assert m.snapshot(s.state, None).pending_confirm.final is False  # type: ignore[union-attr]
    assert "yes, submit" in s.click(i.Confirm())
    assert m.snapshot(s.state, None).pending_confirm.final is True  # type: ignore[union-attr]
    s.click(i.Confirm())
    assert s.state.end_reason == "submitted"


def test_a_click_saved_only_on_this_computer_still_stays_put(script: Make) -> None:
    s = started(script())
    s.next_outcome = "queued"
    assert "waiting to sync" in s.click(select(1, "A"))
    assert s.state.current == 1


# --- Revisit flags ----------------------------------------------------------------------


def test_flagging_the_current_and_a_numbered_question(script: Make) -> None:
    s = started(script())
    assert s.say(flag()) == "Question 1 flagged to revisit."
    assert s.say(flag(12)) == "Question 12 flagged to revisit."
    assert s.say(flag(12)) == "Question 12 is already flagged to revisit."
    assert s.say(i.ListFlagged()) == "Questions 1 and 12 flagged to revisit."
    assert s.state.flagged == {1, 12}
    assert s.writes == []  # the candidate's own reminder; the platform never sees it


def test_unflagging(script: Make) -> None:
    s = started(script())
    s.say(flag(3))
    assert s.say(unflag(3)) == "Flag removed from question 3."
    assert s.say(unflag(3)) == "Question 3 is not flagged."
    assert s.say(i.ListFlagged()) == "No questions are flagged to revisit."


def test_flagging_a_question_that_does_not_exist(script: Make) -> None:
    s = started(script())
    assert "There is no question 99" in s.say(flag(99))
    assert s.state.flagged == frozenset()


def test_flags_before_the_exam_starts_need_a_number(script: Make) -> None:
    s = script()
    assert "Say start to begin" in s.say(flag())
    assert s.say(flag(5)) == "Question 5 flagged to revisit."


def test_flagging_while_confirming_asks_the_confirmation_again(script: Make) -> None:
    s = started(script())
    s.answer("B")
    said = s.say(flag())
    assert said.startswith("Question 1 flagged to revisit. For question 1, you chose option B")
    assert s.phase() is m.Phase.CONFIRM_ANSWER


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("flag", flag()),
        ("flag this question", flag()),
        ("flag question 12", flag(12)),
        ("mark this for review", flag()),
        ("mark question 3 to revisit", flag(3)),
        ("I'll come back to this later", flag()),
        ("unflag", unflag()),
        ("remove the flag from question 7", unflag(7)),
        ("which questions are flagged", i.ListFlagged()),
        ("what did I flag", i.ListFlagged()),
        # "mark" with a letter is still an answer.
        ("mark B", i.Answer(args=i.ChoiceArgs(choice="B"))),
    ],
)
def test_flags_by_voice(text: str, expected: i.Intent) -> None:
    assert match(text) == expected


def test_the_model_is_never_offered_screen_or_flag_actions() -> None:
    offered = {i.intent_name(t) for t in i.INTENT_TYPES}
    assert offered.isdisjoint({"select", "flag", "unflag", "list_flagged"})
    assert i.parse_llm_output('{"action": "select", "question": 1, "choice": "A"}') == i.Unclear()
    assert i.parse_llm_output('{"action": "flag"}') == i.Unclear()


# --- What the screen is told ------------------------------------------------------------


def test_the_snapshot_carries_the_navigator(script: Make) -> None:
    s = script()
    before = m.snapshot(s.state, 3600)
    assert not before.started and before.answered_questions == () and before.ended is None
    s.begin()
    s.click(select(1, "B"))
    s.say(flag(4))
    s.go_to(3)
    s.click(select(3, "A"))
    s.go_to(1)
    now = m.snapshot(s.state, 3600)
    assert now.started and now.current_answer == "B"
    assert now.answered_questions == (1, 3) and now.flagged == (4,)
    s.go_to(2)
    assert m.snapshot(s.state, 3600).current_answer is None


def test_the_snapshot_says_how_the_exam_ended(script: Make) -> None:
    s = started(script())
    s.feed(m.TimeUp())
    assert m.snapshot(s.state, 0).ended == "time_up"


# --- The messages -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("act", "expected"),
    [
        (Act(action="select", question=4, choice="C"), select(4, "C")),
        (Act(action="select", question=4), select(4, None)),
        (Act(action="go_to", question=9), i.GoTo(args=i.QuestionArgs(question=9))),
        (Act(action="next"), i.Next()),
        (Act(action="previous"), i.Previous()),
        (Act(action="flag"), flag()),
        (Act(action="unflag", question=2), unflag(2)),
        (Act(action="submit"), i.Submit()),
        (Act(action="confirm"), i.Confirm()),
        (Act(action="cancel"), i.Cancel()),
    ],
)
def test_each_click_is_its_voice_command(act: Act, expected: i.Intent) -> None:
    assert i.intent_for_act(act) == expected


@pytest.mark.parametrize(
    "fields",
    [
        {"action": "select"},  # which question?
        {"action": "go_to"},
        {"action": "next", "choice": "A"},
        {"action": "submit", "question": 3},
        {"action": "select", "question": 0, "choice": "A"},
        {"action": "explain", "question": 1},
    ],
)
def test_malformed_clicks_are_refused(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Act.model_validate(fields)


def test_sign_in_by_examination_number_alone() -> None:
    login = parse_client_message('{"type": "login", "reg_no": "20260001PR"}')
    assert login == Login(reg_no="20260001PR", exam_code=None)
