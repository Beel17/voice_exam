"""Behaviour every Exam Data Adapter implementation must show.

Tests assume nothing about the platform's data beyond the conformance profile, and never
assume a candidate starts with no answers, so they can run against a shared test instance.
"""

import uuid
from collections.abc import Awaitable

import pytest

from spec.conformance.conftest import ConformanceProfile
from voice_agent.adapters.base import (
    OPTION_LETTERS,
    AdapterRejected,
    CandidateSession,
    ExamDataAdapter,
    RejectReason,
)


def op_id() -> str:
    return f"conf-{uuid.uuid4()}"


async def verify(
    adapter: ExamDataAdapter, profile: ConformanceProfile, reg_no: str
) -> CandidateSession:
    return await adapter.verify_candidate(reg_no, profile.exam_code)


async def rejected(reason: RejectReason, awaitable: Awaitable[object]) -> None:
    with pytest.raises(AdapterRejected) as info:
        await awaitable
    assert info.value.reason == reason


# --- Candidates and exam content --------------------------------------------------------


async def test_verify_returns_the_same_ids_every_time(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    first = await verify(adapter, profile, profile.reg_no)
    again = await verify(adapter, profile, profile.reg_no)
    assert first == again
    other = await verify(adapter, profile, profile.other_reg_no)
    assert other.candidate_id != first.candidate_id
    assert other.exam_id == first.exam_id


async def test_verify_refuses_unregistered_candidates(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    await rejected(
        RejectReason.NOT_REGISTERED,
        adapter.verify_candidate(profile.unregistered_reg_no, profile.exam_code),
    )
    await rejected(
        RejectReason.NOT_REGISTERED, adapter.verify_candidate(profile.reg_no, "NO-SUCH-EXAM")
    )


async def test_outline_belongs_to_the_exam(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.reg_no)
    outline = await adapter.get_exam_outline(session.exam_id)
    assert outline.exam_id == session.exam_id


async def test_unknown_exam_is_refused(adapter: ExamDataAdapter) -> None:
    await rejected(RejectReason.NO_SUCH_EXAM, adapter.get_exam_outline("no-such-exam"))
    await rejected(RejectReason.NO_SUCH_EXAM, adapter.get_question("no-such-exam", 1))


async def test_every_question_is_readable(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    # Shape (lettering, lengths, no extra fields such as an answer key) is enforced by
    # the Question model at the boundary; here we check numbering and reachability.
    session = await verify(adapter, profile, profile.reg_no)
    outline = await adapter.get_exam_outline(session.exam_id)
    for n in range(1, outline.question_count + 1):
        question = await adapter.get_question(session.exam_id, n)
        assert question.number == n


async def test_questions_outside_the_exam_are_refused(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.reg_no)
    outline = await adapter.get_exam_outline(session.exam_id)
    await rejected(
        RejectReason.NO_SUCH_QUESTION,
        adapter.get_question(session.exam_id, outline.question_count + 1),
    )


# --- Saving answers ---------------------------------------------------------------------


async def test_saved_answer_is_read_back(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.reg_no)
    await adapter.save_answer(session.candidate_id, 1, "B", op_id())
    assert (await adapter.get_saved_answers(session.candidate_id)).answers[1] == "B"


async def test_clearing_removes_the_answer(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.reg_no)
    await adapter.save_answer(session.candidate_id, 2, "A", op_id())
    await adapter.save_answer(session.candidate_id, 2, None, op_id())
    assert 2 not in (await adapter.get_saved_answers(session.candidate_id)).answers


async def test_a_repeated_operation_is_applied_once(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.reg_no)
    first, second = op_id(), op_id()
    await adapter.save_answer(session.candidate_id, 3, "A", first)
    await adapter.save_answer(session.candidate_id, 3, "B", second)
    # A late retry of the first save (its reply was lost) must not undo the second.
    await adapter.save_answer(session.candidate_id, 3, "A", first)
    assert (await adapter.get_saved_answers(session.candidate_id)).answers[3] == "B"


async def test_reusing_an_operation_id_for_another_write_conflicts(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.reg_no)
    op = op_id()
    await adapter.save_answer(session.candidate_id, 4, "A", op)
    await rejected(
        RejectReason.OPERATION_CONFLICT, adapter.save_answer(session.candidate_id, 4, "C", op)
    )
    await rejected(
        RejectReason.OPERATION_CONFLICT, adapter.save_answer(session.candidate_id, 5, "A", op)
    )
    assert (await adapter.get_saved_answers(session.candidate_id)).answers[4] == "A"


async def test_a_choice_the_question_does_not_have_is_refused(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.reg_no)
    outline = await adapter.get_exam_outline(session.exam_id)
    for n in range(1, outline.question_count + 1):
        question = await adapter.get_question(session.exam_id, n)
        if len(question.options) < len(OPTION_LETTERS):
            missing = OPTION_LETTERS[len(question.options)]
            await rejected(
                RejectReason.INVALID_CHOICE,
                adapter.save_answer(session.candidate_id, n, missing, op_id()),
            )
            return
    pytest.skip("every question has five options")


async def test_saving_outside_the_exam_is_refused(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.reg_no)
    outline = await adapter.get_exam_outline(session.exam_id)
    await rejected(
        RejectReason.NO_SUCH_QUESTION,
        adapter.save_answer(session.candidate_id, outline.question_count + 1, "A", op_id()),
    )


async def test_candidates_never_see_each_others_answers(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    me = await verify(adapter, profile, profile.reg_no)
    other = await verify(adapter, profile, profile.other_reg_no)
    before = await adapter.get_saved_answers(other.candidate_id)
    await adapter.save_answer(me.candidate_id, 6, "D", op_id())
    assert await adapter.get_saved_answers(other.candidate_id) == before


async def test_unknown_candidate_is_refused(adapter: ExamDataAdapter) -> None:
    await rejected(RejectReason.UNKNOWN_CANDIDATE, adapter.get_saved_answers("no-such-candidate"))
    await rejected(RejectReason.UNKNOWN_CANDIDATE, adapter.get_time_left("no-such-candidate"))
    await rejected(
        RejectReason.UNKNOWN_CANDIDATE, adapter.save_answer("no-such-candidate", 1, "A", op_id())
    )
    await rejected(RejectReason.UNKNOWN_CANDIDATE, adapter.submit("no-such-candidate", op_id()))


# --- Timing and submission --------------------------------------------------------------


async def test_time_left_is_within_the_duration_and_never_grows(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.reg_no)
    outline = await adapter.get_exam_outline(session.exam_id)
    first = await adapter.get_time_left(session.candidate_id)
    second = await adapter.get_time_left(session.candidate_id)
    assert 0 <= second <= first <= outline.duration_s


async def test_submission_is_idempotent_and_final(
    adapter: ExamDataAdapter, profile: ConformanceProfile
) -> None:
    session = await verify(adapter, profile, profile.submit_reg_no)
    op = op_id()
    await adapter.submit(session.candidate_id, op)
    await adapter.submit(session.candidate_id, op)  # a retry of the same submission
    await rejected(RejectReason.ALREADY_SUBMITTED, adapter.submit(session.candidate_id, op_id()))
    await rejected(
        RejectReason.ALREADY_SUBMITTED,
        adapter.save_answer(session.candidate_id, 1, "A", op_id()),
    )
