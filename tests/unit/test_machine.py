"""The exam script, conversation by conversation.

Every Script made through the ``script`` fixture is also checked to rebuild exactly from
its event log when the test ends.
"""

from collections.abc import Callable
from dataclasses import replace

import pytest
from pydantic import TypeAdapter, ValidationError

from tests.unit.script import Script
from voice_agent.adapters.base import RejectReason
from voice_agent.session import machine as m
from voice_agent.understanding import intents as i

Make = Callable[..., Script]

Q1 = "Choose the word nearest in meaning to 'candid'"
NO_ARGS = i.NoArgs()


def started(s: Script) -> Script:
    s.begin()
    return s


# --- Introduction -----------------------------------------------------------------------


def test_welcome_describes_the_exam(script: Make) -> None:
    s = script()
    assert s.phase() is m.Phase.INTRO
    welcome = " ".join(s.said)
    assert "60 questions in 4 sections" in welcome
    assert "Use of English, Mathematics, Physics and Biology" in welcome
    assert "1 hour" in welcome


@pytest.mark.parametrize("begin", [i.Confirm(), i.Next(), i.RepeatQuestion(), i.RepeatOptions()])
def test_starting_reads_question_one(script: Make, begin: i.Intent) -> None:
    s = script()
    said = s.say(begin)
    assert said.startswith(f"Question 1 of 60. {Q1}")
    assert "Option A: angry. Option B: frank." in said
    assert said.endswith("Say a letter, A to D, to answer.")
    assert s.phase() is m.Phase.LISTEN


def test_candidate_can_start_from_any_question(script: Make) -> None:
    s = script()
    assert s.go_to(20).startswith("Question 20 of 60. The sum of the interior angles")
    assert s.state.current == 20


@pytest.mark.parametrize(
    "intent",
    [
        i.Answer(args=i.ChoiceArgs(choice="B")),
        i.ChangeAnswer(args=i.ChangeAnswerArgs(choice="B", question=None)),
        i.ClearAnswer(args=i.MaybeQuestionArgs(question=None)),
        i.RepeatOption(args=i.ChoiceArgs(choice="B")),
        i.SpellOption(args=i.ChoiceArgs(choice="B")),
        i.Submit(),
        i.Cancel(),
    ],
)
def test_nothing_can_be_answered_before_a_question_is_read(script: Make, intent: i.Intent) -> None:
    s = script()
    assert "Say start to begin" in s.say(intent)
    assert s.phase() is m.Phase.INTRO
    assert s.writes == []


def test_where_am_i_before_starting(script: Make) -> None:
    assert "You have not started yet" in script().say(i.WhereAmI())


# --- Answering --------------------------------------------------------------------------


def test_answer_is_read_back_and_saved_only_after_yes(script: Make) -> None:
    s = started(script())
    said = s.answer("B")
    assert said == "For question 1, you chose option B: frank. Say yes to save it, or no to cancel."
    assert s.phase() is m.Phase.CONFIRM_ANSWER
    assert s.writes == []
    assert s.yes().startswith("Saved. Question 2 of 60. Choose the word opposite")
    assert s.writes == [m.SaveAnswer(1, "B", s.writes[0].operation_id)]
    assert s.state.answers == {1: "B"}
    assert s.phase() is m.Phase.LISTEN


def test_saving_the_last_question_does_not_wrap(script: Make) -> None:
    s = script()
    s.answer_and_save(60, "A")
    assert s.state.current == 60
    assert s.said == [
        "Saved.",
        "This is the last question, 60. Say submit when you are ready, "
        "or ask which questions are unanswered.",
    ]


def test_answer_saved_locally_still_moves_on(script: Make) -> None:
    s = started(script())
    s.next_outcome = "queued"
    s.answer("B")
    assert "waiting to sync" in s.yes()
    assert s.state.current == 2


def test_no_cancels_the_answer(script: Make) -> None:
    s = started(script())
    s.answer("B")
    assert s.no() == "Not saved. Question 1 is not answered."
    assert s.writes == [] and s.state.answers == {}


def test_cancelling_a_change_keeps_the_old_answer(script: Make) -> None:
    s = started(script())
    s.answer_and_save(1, "B")
    s.go_to(1)
    s.answer("C")
    assert s.no() == "Not saved. Your answer for question 1 is B."


def test_option_the_question_does_not_have_is_refused(script: Make) -> None:
    s = started(script())
    assert s.answer("E") == "There is no option E. This question has options A to D."
    assert s.phase() is m.Phase.LISTEN


def test_true_false_question_offers_two_letters(script: Make) -> None:
    s = script()
    assert s.go_to(40).endswith("Say a letter, A to B, to answer.")
    assert "There is no option C" in s.answer("C")


def test_a_new_choice_replaces_the_pending_one(script: Make) -> None:
    s = started(script())
    s.answer("B")
    assert "you chose option C: secretive" in s.answer("C")
    s.yes()
    assert s.state.answers == {1: "C"}
    assert len(s.writes) == 1


def test_invalid_new_choice_drops_the_pending_one(script: Make) -> None:
    s = started(script())
    s.answer("B")
    assert "There is no option E" in s.answer("E")
    assert s.phase() is m.Phase.LISTEN
    assert "nothing to confirm" in s.yes()


def test_moving_away_abandons_the_answer_aloud(script: Make) -> None:
    s = started(script())
    s.answer("B")
    said = s.say(i.Next())
    assert said.startswith("Not saved. Question 1 is not answered. Question 2 of 60.")
    assert s.writes == []


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (i.RepeatQuestion(), "Question 1 of 60."),
        (i.WhereAmI(), "You are on question 1 of 60, in Use of English."),
        (i.Help(), "You can say"),
        (i.Unclear(), "Sorry, I did not catch that."),
        (i.NotAllowed(), "I can only read the questions and options"),
        (i.Faster(), "Speed set to 1.5 times."),
    ],
)
def test_other_requests_keep_the_answer_pending_and_ask_again(
    script: Make, intent: i.Intent, expected: str
) -> None:
    s = started(script())
    s.answer("B")
    said = s.say(intent)
    assert said.startswith(expected)
    assert said.endswith("you chose option B: frank. Say yes to save it, or no to cancel.")
    assert s.phase() is m.Phase.CONFIRM_ANSWER
    s.yes()
    assert s.state.answers == {1: "B"}


def test_time_left_while_confirming_asks_again_after_the_time(script: Make) -> None:
    s = started(script())
    s.answer("B")
    said = s.say(i.TimeLeft())
    assert said.startswith("You have 30 minutes left. For question 1, you chose option B")


def test_confirm_with_nothing_pending(script: Make) -> None:
    s = started(script())
    assert s.yes() == "There is nothing to confirm right now."
    assert s.no() == "There is nothing to confirm right now."


def test_change_answer_for_another_question_reads_it_first(script: Make) -> None:
    s = started(script())
    s.go_to(5)
    said = s.say(i.ChangeAnswer(args=i.ChangeAnswerArgs(choice="A", question=12)))
    assert said.startswith("Question 12 of 60. What does the idiom")
    assert said.endswith(
        "For question 12, you chose option A: To hide a weapon. "
        "Say yes to save it, or no to cancel."
    )
    s.yes()
    assert s.state.answers == {12: "A"}


def test_change_answer_from_the_introduction(script: Make) -> None:
    s = script()
    s.say(i.ChangeAnswer(args=i.ChangeAnswerArgs(choice="B", question=3)))
    assert s.phase() is m.Phase.CONFIRM_ANSWER
    assert s.state.pending == m.Pending(3, "B")


def test_change_answer_for_the_current_question(script: Make) -> None:
    s = started(script())
    said = s.say(i.ChangeAnswer(args=i.ChangeAnswerArgs(choice="D", question=1)))
    assert said.startswith("For question 1, you chose option D")


def test_change_answer_checks_the_option_after_loading(script: Make) -> None:
    s = started(script())
    said = s.say(i.ChangeAnswer(args=i.ChangeAnswerArgs(choice="E", question=12)))
    assert said.endswith("There is no option E. This question has options A to D.")
    assert s.phase() is m.Phase.LISTEN


def test_change_answer_for_a_question_that_does_not_exist(script: Make) -> None:
    s = started(script())
    said = s.say(i.ChangeAnswer(args=i.ChangeAnswerArgs(choice="A", question=61)))
    assert said == "There is no question 61. This exam has 60 questions."


def test_clearing_an_answer_needs_confirmation(script: Make) -> None:
    s = started(script())
    s.answer_and_save(7, "B")
    s.go_to(7)
    said = s.say(i.ClearAnswer(args=i.MaybeQuestionArgs(question=None)))
    assert said == "Remove your answer B for question 7? Say yes to remove it, or no to keep it."
    assert s.yes() == "Answer removed. Question 7 is now unanswered."
    assert s.writes[-1] == m.SaveAnswer(7, None, s.writes[-1].operation_id)
    assert s.state.answers == {}
    assert s.state.current == 7  # removing an answer is not answering: no move


def test_clear_while_confirming_reasks_the_clear(script: Make) -> None:
    s = started(script())
    s.answer_and_save(1, "B")
    s.say(i.ClearAnswer(args=i.MaybeQuestionArgs(question=1)))
    assert s.say(i.Help()).endswith(
        "Remove your answer B for question 1? Say yes to remove it, or no to keep it."
    )


def test_clearing_needs_an_answer_and_a_real_question(script: Make) -> None:
    s = started(script())
    assert s.say(i.ClearAnswer(args=i.MaybeQuestionArgs(question=9))) == (
        "Question 9 has no answer to remove."
    )
    assert "There is no question 99" in s.say(i.ClearAnswer(args=i.MaybeQuestionArgs(question=99)))


def test_operation_ids_are_unique_and_deterministic(script: Make) -> None:
    s = started(script())
    s.answer_and_save(1, "A")
    s.answer_and_save(1, "B")
    ids = [w.operation_id for w in s.writes]
    assert len(set(ids)) == 2
    assert all(op.startswith("s-1:") for op in ids)


# --- Save outcomes ----------------------------------------------------------------------


def test_queued_save_counts_and_tells_the_candidate(script: Make) -> None:
    s = started(script())
    s.next_outcome = "queued"
    s.answer("B")
    assert s.yes().startswith("Saved on this computer, waiting to sync.")
    assert s.state.answers == {1: "B"}
    op = s.writes[0].operation_id
    assert op in s.state.unsynced
    assert s.settle(op, "saved") == ""  # synced later, in the background: silent
    assert not s.state.unsynced


def test_refused_save_is_undone_and_explained(script: Make) -> None:
    s = started(script())
    s.answer_and_save(1, "A")
    s.go_to(1)
    s.next_outcome, s.next_reason = "rejected", RejectReason.INVALID_CHOICE
    s.answer("B")
    assert s.yes() == "Your answer for question 1 was not saved. Please let the invigilator know."
    assert s.state.answers == {1: "A"}
    assert s.state.current == 1


def test_refused_first_answer_leaves_the_question_unanswered(script: Make) -> None:
    s = started(script())
    s.next_outcome, s.next_reason = "rejected", RejectReason.INVALID_CHOICE
    s.answer("B")
    s.yes()
    assert s.state.answers == {}


def test_late_refusal_does_not_undo_a_newer_answer(script: Make) -> None:
    s = started(script())
    s.next_outcome = "queued"
    s.answer_and_save(1, "A")
    first = s.writes[0].operation_id
    s.next_outcome = "saved"
    s.answer_and_save(1, "B")
    said = s.settle(first, "rejected", RejectReason.INVALID_CHOICE)
    assert "was not saved" in said
    assert s.state.answers == {1: "B"}


@pytest.mark.parametrize(
    ("reason", "end_reason"),
    [(RejectReason.TIME_UP, "time_up"), (RejectReason.ALREADY_SUBMITTED, "submitted")],
)
def test_refusal_that_ends_the_exam(script: Make, reason: RejectReason, end_reason: str) -> None:
    s = started(script())
    s.next_outcome, s.next_reason = "rejected", reason
    s.answer("B")
    s.yes()
    assert s.phase() is m.Phase.ENDED
    assert s.state.end_reason == end_reason


def test_repeated_outcome_for_a_settled_write_changes_nothing(script: Make) -> None:
    s = started(script())
    s.answer_and_save(1, "B")
    before = s.state
    assert s.settle(s.writes[0].operation_id, "saved") == ""
    assert s.state.answers == before.answers


def test_write_outcome_reason_only_with_rejection() -> None:
    with pytest.raises(ValidationError):
        m.WriteOutcome(operation_id="x", outcome="rejected")
    with pytest.raises(ValidationError):
        m.WriteOutcome(operation_id="x", outcome="saved", reason=RejectReason.TIME_UP)


# --- Moving -----------------------------------------------------------------------------


def test_next_and_previous_stop_at_the_ends(script: Make) -> None:
    s = started(script())
    assert s.say(i.Previous()) == "This is the first question."
    s.say(i.Last())
    assert s.state.current == 60
    assert s.say(i.Next()).startswith("This is the last question, 60.")
    assert s.say(i.Previous()).startswith("Question 59 of 60.")
    s.say(i.First())
    assert s.state.current == 1
    assert s.say(i.Next()).startswith("Question 2 of 60.")


def test_go_to_a_question_that_does_not_exist(script: Make) -> None:
    s = started(script())
    assert s.go_to(61) == "There is no question 61. This exam has 60 questions."
    assert s.state.current == 1


def test_next_unanswered_skips_answered_and_wraps(script: Make) -> None:
    s = script(saved={2: "A", 3: "B", 59: "A", 60: "C"})
    s.begin()
    s.say(i.NextUnanswered())
    assert s.state.current == 4
    s.go_to(58)
    s.say(i.NextUnanswered())
    assert s.state.current == 1  # wrapped past the answered 59 and 60


def test_next_unanswered_when_all_answered(script: Make) -> None:
    s = script(exam="injection", saved={n: "A" for n in range(1, 7)})
    s.begin()
    assert s.say(i.NextUnanswered()) == "You have answered every question."


def test_go_to_section(script: Make) -> None:
    s = started(script())
    assert s.say(i.GoToSection(args=i.SectionArgs(section=2))).startswith("Question 16 of 60.")
    said = s.say(i.GoToSection(args=i.SectionArgs(section=5)))
    assert said == "There is no section 5. This exam has 4 sections."


def test_go_to_section_without_sections(script: Make) -> None:
    s = started(script(exam="injection"))
    said = s.say(i.GoToSection(args=i.SectionArgs(section=1)))
    assert said == "This exam has no sections. Say a question number instead."


def test_question_that_cannot_be_loaded(script: Make) -> None:
    s = started(script(unavailable=[5]))
    said = s.go_to(5)
    assert said.startswith("Sorry, question 5 cannot be loaded right now.")
    assert s.state.current == 1
    said = s.say(i.ChangeAnswer(args=i.ChangeAnswerArgs(choice="A", question=5)))
    assert s.state.answer_after_load is None
    assert s.phase() is m.Phase.LISTEN


def test_loaded_question_outside_the_exam_is_a_bug(script: Make) -> None:
    s = started(script())
    with pytest.raises(ValueError, match="outside"):
        m.step(s.state, m.QuestionLoaded(question=61, option_count=4))


# --- Hearing again ----------------------------------------------------------------------


def test_spoken_text_is_read_instead_of_symbols(script: Make) -> None:
    s = script()
    said = s.go_to(16)
    assert "Simplify 2 cubed times 2 squared." in said
    assert "Option A: 2 to the power 6." in said
    assert "2³" not in said


def test_repeat_options_and_one_option(script: Make) -> None:
    s = started(script())
    assert s.say(i.RepeatOptions()).startswith("Option A: angry.")
    assert s.say(i.RepeatOption(args=i.ChoiceArgs(choice="C"))) == "Option C: secretive."
    assert "There is no option E" in s.say(i.RepeatOption(args=i.ChoiceArgs(choice="E")))


def test_repeat_shows_the_saved_answer(script: Make) -> None:
    s = started(script())
    s.answer_and_save(1, "B")
    assert s.say(i.Previous()).endswith("Your answer is B.")
    assert s.say(i.RepeatQuestion()).endswith("Your answer is B.")


def test_spell_option(script: Make) -> None:
    s = script()
    s.go_to(4)
    said = s.say(i.SpellOption(args=i.ChoiceArgs(choice="C")))
    assert said == "Option C is spelt: a, c, c, o, m, m, o, d, a, t, i, o, n."
    assert "There is no option E" in s.say(i.SpellOption(args=i.ChoiceArgs(choice="E")))


# --- Status -----------------------------------------------------------------------------


def test_status_replies(script: Make) -> None:
    s = started(script())
    s.answer_and_save(17, "C")
    s.answer_and_save(18, "A")
    s.go_to(18)
    assert s.say(i.WhereAmI()) == (
        "You are on question 18 of 60, in Mathematics. Your answer for question 18 is A."
    )
    assert s.say(i.CountAnswered()) == "You have answered 2 of 60 questions. 58 are left."
    assert s.say(i.ListUnanswered()) == "You have not answered questions 1 to 16 and 19 to 60."
    one = i.ReviewAnswers(args=i.MaybeQuestionArgs(question=17))
    assert s.say(one) == "For question 17 you chose C."
    two = i.ReviewAnswers(args=i.MaybeQuestionArgs(question=2))
    assert s.say(two) == "Question 2 is not answered."
    far = i.ReviewAnswers(args=i.MaybeQuestionArgs(question=99))
    assert "There is no question 99" in s.say(far)


def test_where_am_i_without_sections(script: Make) -> None:
    s = started(script(exam="injection"))
    assert s.say(i.WhereAmI()) == "You are on question 1 of 6. Question 1 is not answered."


def test_review_all_groups_unanswered_runs(script: Make) -> None:
    s = script(exam="injection", saved={1: "B", 4: "A"})
    s.begin()
    said = s.say(i.ReviewAnswers(args=i.MaybeQuestionArgs(question=None)))
    assert said == (
        "Question 1, B. Questions 2, 3, not answered. Question 4, A. Questions 5, 6, not answered."
    )


def test_time_left(script: Make) -> None:
    s = started(script())
    assert s.say(i.TimeLeft()) == "You have 30 minutes left."
    s.time_left = None
    assert s.say(i.TimeLeft()).startswith("I cannot get the time left right now.")


def test_saved_answers_from_the_platform_count(script: Make) -> None:
    s = script(saved={1: "A", 70: "B"})  # 70 is outside the exam and ignored
    assert s.state.answers == {1: "A"}


# --- Listening comfort ------------------------------------------------------------------


def test_speed_steps_and_limits(script: Make) -> None:
    s = started(script())
    speeds = [s.say(i.Faster()) for _ in range(4)]
    assert speeds == [
        "Speed set to 1.5 times.",
        "Speed set to 2 times.",
        "Speed set to 2.5 times.",
        "This is already my fastest speed.",
    ]
    assert s.state.speed == 2.5
    assert s.say(i.NormalSpeed()) == "Speed set to 1 times."
    assert s.say(i.Slower()) == "Speed set to 0.8 times."
    assert s.say(i.Slower()) == "This is already my slowest speed."


def test_cue_volume_steps_and_limits(script: Make) -> None:
    s = started(script())
    assert s.say(i.LouderCues()) == "Sound volume changed."
    assert s.say(i.LouderCues()) == "The sounds are already at their loudest."
    for _ in range(3):
        s.say(i.QuieterCues())
    assert s.state.cue_volume == 0.25
    assert s.say(i.QuieterCues()) == "The sounds are already at their quietest."


# --- Not understood ---------------------------------------------------------------------


def test_keyboard_is_offered_after_two_misunderstandings(script: Make) -> None:
    s = started(script())
    assert "keyboard" not in s.say(i.Unclear())
    assert "keyboard" in s.say(i.Unclear())
    s.say(i.Help())
    assert "keyboard" not in s.say(i.Unclear())


def test_the_keyboard_offer_is_not_repeated_on_every_miss(script: Make) -> None:
    # A tester heard it on 8 misses in a row: once per run, then every fifth miss.
    s = started(script())
    offered = ["keyboard" in s.say(i.Unclear()) for _ in range(12)]
    assert [n + 1 for n, yes in enumerate(offered) if yes] == [2, 7, 12]


def test_out_of_scope_requests_get_one_neutral_reply(script: Make) -> None:
    s = started(script())
    assert s.say(i.NotAllowed()) == (
        "I can only read the questions and options, and record your answers."
    )


# --- Submitting -------------------------------------------------------------------------


def test_submission_needs_two_confirmations(script: Make) -> None:
    s = started(script())
    s.answer_and_save(1, "B")
    first = s.say(i.Submit())
    assert first == (
        "You have answered 1. 59 questions not answered. Do you want to submit your exam? "
        "Say yes or no."
    )
    assert s.yes().startswith("Submitting is final.")
    assert all(isinstance(w, m.SaveAnswer) for w in s.writes)
    assert s.yes() == "Your exam has been submitted. Thank you."
    assert isinstance(s.writes[-1], m.SubmitExam)
    assert s.phase() is m.Phase.ENDED


def test_all_answered_submission_prompt(script: Make) -> None:
    s = script(exam="injection", saved={n: "A" for n in range(1, 7)})
    s.begin()
    assert s.say(i.Submit()).startswith("You have answered 6. Do you want to submit")


@pytest.mark.parametrize("confirmations", [0, 1])
def test_no_at_either_step_does_not_submit(script: Make, confirmations: int) -> None:
    s = started(script())
    s.say(i.Submit())
    for _ in range(confirmations):
        s.yes()
    assert s.no() == "Not submitted. You can carry on."
    assert s.writes == [] and s.phase() is m.Phase.LISTEN


def test_moving_away_abandons_submission(script: Make) -> None:
    s = started(script())
    s.say(i.Submit())
    assert s.say(i.Next()).startswith("Not submitted. You can carry on. Question 2 of 60.")


@pytest.mark.parametrize("confirmations", [0, 1])
def test_other_requests_during_submission_ask_again(script: Make, confirmations: int) -> None:
    s = started(script())
    s.say(i.Submit())
    for _ in range(confirmations):
        s.yes()
    said = s.say(i.CountAnswered())
    assert said.startswith("You have answered 0 of 60 questions.")
    assert said.endswith("Say yes or no.") or said.endswith("or no to go back.")


def test_submit_while_confirming_an_answer_drops_the_answer(script: Make) -> None:
    s = started(script())
    s.answer("B")
    said = s.say(i.Submit())
    assert said.startswith("Not saved. Question 1 is not answered. You have answered 0.")


def test_submission_waits_for_unsynced_answers(script: Make) -> None:
    s = started(script())
    s.next_outcome = "queued"
    s.answer_and_save(1, "B")
    s.say(i.Submit())
    s.yes()
    said = s.yes()
    assert said.startswith("1 answer still waiting to sync")
    assert not any(isinstance(w, m.SubmitExam) for w in s.writes)
    assert s.phase() is m.Phase.LISTEN


def test_queued_submission_ends_the_exam_and_says_so(script: Make) -> None:
    s = started(script())
    s.next_outcome = "queued"
    s.say(i.Submit())
    s.yes()
    assert s.yes().startswith("Your exam is submitted on this computer and waiting to sync.")
    assert s.phase() is m.Phase.ENDED
    assert s.settle(s.writes[-1].operation_id, "saved") == ""


@pytest.mark.parametrize(
    ("reason", "said", "end_reason"),
    [
        (RejectReason.ALREADY_SUBMITTED, "Your exam has been submitted.", "submitted"),
        (RejectReason.TIME_UP, "Time is up.", "time_up"),
    ],
)
def test_refused_submission_that_still_ends(
    script: Make, reason: RejectReason, said: str, end_reason: str
) -> None:
    s = started(script())
    s.next_outcome, s.next_reason = "rejected", reason
    s.say(i.Submit())
    s.yes()
    assert s.yes().startswith(said)
    assert s.state.end_reason == end_reason


def test_refused_submission_returns_to_the_exam(script: Make) -> None:
    s = started(script())
    s.next_outcome, s.next_reason = "rejected", RejectReason.UNKNOWN_CANDIDATE
    s.say(i.Submit())
    s.yes()
    assert s.yes() == "The exam could not be submitted. Please let the invigilator know."
    assert s.phase() is m.Phase.LISTEN
    assert s.state.submit_operation is None


def test_while_submitting_nothing_else_happens(script: Make) -> None:
    s = started(script())
    s.next_outcome = None
    s.say(i.Submit())
    s.yes()
    s.yes()
    assert s.phase() is m.Phase.SUBMITTING
    assert s.say(i.Next()) == "Your exam is being submitted. Please wait."
    assert (
        s.settle(s.writes[-1].operation_id, "saved") == "Your exam has been submitted. Thank you."
    )


def test_after_submission_every_request_is_refused(script: Make) -> None:
    s = started(script())
    s.say(i.Submit())
    s.yes()
    s.yes()
    for intent in (i.Next(), i.Answer(args=i.ChoiceArgs(choice="A")), i.Submit(), i.Unclear()):
        assert s.say(intent) == "Your exam has been submitted, so nothing more can change."
    assert len(s.writes) == 1


# --- Time -------------------------------------------------------------------------------


def test_time_warning_and_time_up(script: Make) -> None:
    s = started(script())
    s.answer("B")
    assert s.feed(m.TimeWarning(minutes=5)) == "5 minutes left."
    assert s.phase() is m.Phase.CONFIRM_ANSWER
    assert s.feed(m.TimeUp()) == "Time is up. Your exam has ended."
    assert s.phase() is m.Phase.ENDED and s.state.pending is None
    assert s.say(i.Next()) == "Time is up, so your exam has ended."
    assert s.feed(m.TimeWarning(minutes=1)) == ""
    assert s.feed(m.TimeUp()) == ""
    assert s.feed(m.QuestionLoaded(question=2, option_count=4)) == ""
    assert s.writes == []


def test_one_minute_warning_is_singular(script: Make) -> None:
    assert started(script()).feed(m.TimeWarning(minutes=1)) == "1 minute left."


# --- Resume -----------------------------------------------------------------------------


def test_resume_discards_the_unconfirmed_answer_and_rereads(script: Make) -> None:
    s = started(script())
    s.answer_and_save(14, "B")  # moves on to 15
    s.answer("C")
    said = s.feed(m.Resumed())
    assert said.startswith("Welcome back. You were on question 15 of 60. Question 15 of 60.")
    assert s.state.pending is None
    assert s.state.answers == {14: "B"}


def test_resume_before_starting_welcomes_again(script: Make) -> None:
    s = script()
    assert s.feed(m.Resumed()).startswith("Welcome. This exam has 60 questions")


def test_resume_after_the_end(script: Make) -> None:
    s = started(script())
    s.feed(m.TimeUp())
    assert s.feed(m.Resumed()) == "Time is up, so your exam has ended."


def test_resume_while_submitting(script: Make) -> None:
    s = started(script())
    s.next_outcome = None
    s.say(i.Submit())
    s.yes()
    s.yes()
    assert s.feed(m.Resumed()) == "Your exam is being submitted. Please wait."
    # The outbox delivers it later; the candidate is no longer waiting on that reply.
    assert s.settle(s.writes[-1].operation_id, "saved") == ""
    assert s.phase() is m.Phase.ENDED


def test_resume_while_a_save_is_unacknowledged(script: Make) -> None:
    s = started(script())
    s.next_outcome = None
    s.answer("B")
    s.yes()
    s.feed(m.Resumed())
    assert s.state.answers == {1: "B"}
    # Reports for a write nobody is waiting on are silent and do not move the candidate.
    assert s.settle(s.writes[0].operation_id, "queued") == ""
    assert s.state.current == 1
    assert s.settle(s.writes[0].operation_id, "saved") == ""


# --- Event log --------------------------------------------------------------------------


def test_events_survive_serialisation_and_replay(script: Make) -> None:
    s = started(script())
    s.answer_and_save(3, "B")
    s.say(i.Faster())
    s.answer("C")
    adapter: TypeAdapter[list[m.Event]] = TypeAdapter(list[m.Event])
    stored = adapter.dump_json(s.events)
    assert m.replay(adapter.validate_json(stored)) == s.state


def test_replay_needs_a_start() -> None:
    with pytest.raises(ValueError, match="begin"):
        m.replay([m.Resumed()])
    with pytest.raises(ValueError, match="begin"):
        m.replay([])


def test_a_session_starts_once(script: Make) -> None:
    s = script()
    with pytest.raises(ValueError, match="once"):
        m.step(s.state, s.events[0])


# --- State message ----------------------------------------------------------------------


def test_snapshot_reports_progress_and_pending_confirmations(script: Make) -> None:
    s = started(script())
    s.answer_and_save(1, "B")
    snap = m.snapshot(s.state, 1200)
    assert (snap.question, snap.of, snap.answered, snap.unanswered) == (2, 60, 1, 59)
    assert snap.time_left_s == 1200 and snap.pending_confirm is None
    assert (snap.speed, snap.cue_volume) == (1.0, 0.75)

    s.answer("C")
    pending = m.snapshot(s.state, None).pending_confirm
    assert pending is not None and (pending.kind, pending.question, pending.choice) == (
        "answer",
        2,
        "C",
    )

    s.no()
    s.say(i.ClearAnswer(args=i.MaybeQuestionArgs(question=1)))
    pending = m.snapshot(s.state, None).pending_confirm
    assert pending is not None and (pending.kind, pending.choice) == ("clear", None)

    s.no()
    s.say(i.Submit())
    pending = m.snapshot(s.state, None).pending_confirm
    assert pending is not None and pending.kind == "submit"


def test_answer_after_load_only_applies_to_its_own_question(script: Make) -> None:
    s = started(script())
    pending = replace(s.state, answer_after_load=(12, "A"))
    state, effects = m.step(pending, m.QuestionLoaded(question=7, option_count=4))
    assert state.answer_after_load is None
    assert state.phase is m.Phase.LISTEN and state.pending is None
    assert len(effects) == 1
