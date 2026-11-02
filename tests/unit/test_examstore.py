"""The centre's exam store: packages, versions, and answers kept against the paper."""

import io
import json
import zipfile
from pathlib import Path

import pytest

from voice_agent.adapters.base import AdapterRejected, RejectReason
from voice_agent.examstore.__main__ import sample_package
from voice_agent.examstore.adapter import LocalAdapter
from voice_agent.examstore.package import (
    ExamPackage,
    PackageCandidate,
    Shuffle,
    read_package,
    write_package,
)
from voice_agent.examstore.store import ExamStore, StoreError
from voice_agent.examstore.versions import make_version

PAPER = sample_package()


def store_with(tmp_path: Path, package: ExamPackage = PAPER, *, open_: bool = True) -> ExamStore:
    store = ExamStore(tmp_path / "exams.db")
    store.import_package(read_package(write_package(package)))
    if open_:
        store.set_open(package.exam_id, True)
    return store


# --- Versions ---------------------------------------------------------------------------


def version(candidate: str, **shuffle: object) -> object:
    s = PAPER.shuffle.model_copy(update=shuffle)
    return make_version(
        PAPER.exam_id, candidate, PAPER.outline, PAPER.questions,
        shuffle_questions=s.questions, shuffle_options=s.options,
        keep_option_order=frozenset(s.keep_option_order),
    )  # fmt: skip


def test_a_version_is_reproducible_and_differs_between_candidates() -> None:
    assert version("a") == version("a")
    assert version("a") != version("b")


def test_questions_move_only_within_their_subject() -> None:
    v = version("a")
    for section in PAPER.outline.sections:
        shown = range(section.first_question, section.last_question + 1)
        assert sorted(v.paper_number(n) for n in shown) == list(shown)  # type: ignore[attr-defined]


def test_without_shuffling_a_version_is_the_paper() -> None:
    v = version("a", questions=False, options=False)
    assert v.order == tuple(range(1, 61))  # type: ignore[attr-defined]
    for q in PAPER.questions:
        assert v.options[q.number] == tuple(o.letter for o in q.options)  # type: ignore[attr-defined]


def test_some_questions_keep_their_option_order() -> None:
    v = version("a", keep_option_order=(1, 2))
    assert v.options[1] == ("A", "B", "C", "D") and v.options[2] == ("A", "B", "C", "D")  # type: ignore[attr-defined]


def test_shown_and_paper_letters_are_inverse() -> None:
    v = version("a")
    for shown in (1, 17, 60):
        q = v.paper_number(shown)  # type: ignore[attr-defined]
        for letter in v.options[q]:  # type: ignore[attr-defined]
            assert v.paper_choice(shown, v.shown_choice(q, letter)) == letter  # type: ignore[attr-defined]


# --- Packages -----------------------------------------------------------------------------


def test_a_package_with_an_answer_key_is_refused() -> None:
    data = json.loads(PAPER.model_dump_json())
    data["questions"][0]["answer"] = "B"
    with pytest.raises(ValueError, match="answer"):
        ExamPackage.model_validate(data)


def test_a_package_with_stray_files_is_refused() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("package.json", PAPER.model_dump_json())
        z.writestr("photos/practice-01.jpg", b"x")
        z.writestr("answers.csv", b"1,B")
    with pytest.raises(ValueError, match="unexpected file"):
        read_package(out.getvalue())
    with pytest.raises(ValueError, match="not a package"):
        read_package(b"not a zip")


def test_duplicate_exam_numbers_are_refused() -> None:
    twin = PAPER.candidates[0].model_copy(update={"candidate_id": "twin"})
    with pytest.raises(ValueError, match="exam_number"):
        PAPER.model_copy(update={"candidates": (*PAPER.candidates, twin)}).model_validate(
            json.loads(
                PAPER.model_copy(update={"candidates": (*PAPER.candidates, twin)}).model_dump_json()
            )
        )


# --- The store ----------------------------------------------------------------------------


async def test_answers_are_kept_against_the_paper(tmp_path: Path) -> None:
    store = store_with(tmp_path)
    platform = LocalAdapter(store)
    session = await platform.verify_candidate("20260001PR", "")
    shown = await platform.get_question(session.exam_id, 1)
    candidate = store.candidate(session.candidate_id)
    paper_number = candidate.version.paper_number(1)
    paper = store.paper_question(PAPER.exam_id, paper_number)
    # The candidate sees the paper's question, its options possibly in another order.
    assert shown.text == paper.text
    assert {o.text for o in shown.options} == {o.text for o in paper.options}
    await platform.save_answer(session.candidate_id, 1, "B", "s1:5")
    picked = next(o.text for o in shown.options if o.letter == "B")
    stored = store.answers(session.candidate_id)
    assert {o.letter: o.text for o in paper.options}[stored[paper_number]] == picked
    # Read back as the candidate sees it.
    assert (await platform.get_saved_answers(session.candidate_id)).answers == {1: "B"}


async def test_writes_are_applied_once(tmp_path: Path) -> None:
    store = store_with(tmp_path)
    platform = LocalAdapter(store)
    s = await platform.verify_candidate("20260001PR", "UTME-SAMPLE")
    await platform.save_answer(s.candidate_id, 1, "A", "op-1")
    await platform.save_answer(s.candidate_id, 1, "A", "op-1")  # a retry
    with pytest.raises(AdapterRejected) as conflict:
        await platform.save_answer(s.candidate_id, 1, "C", "op-1")
    assert conflict.value.reason is RejectReason.OPERATION_CONFLICT
    await platform.submit(s.candidate_id, "op-2")
    with pytest.raises(AdapterRejected) as late:
        await platform.save_answer(s.candidate_id, 2, "A", "op-3")
    assert late.value.reason is RejectReason.ALREADY_SUBMITTED


async def test_only_open_papers_can_be_signed_in_to(tmp_path: Path) -> None:
    store = store_with(tmp_path, open_=False)
    platform = LocalAdapter(store)
    with pytest.raises(AdapterRejected):
        await platform.verify_candidate("20260001PR", "")
    store.set_open(PAPER.exam_id, True)
    await platform.verify_candidate("20260001PR", "")


async def test_one_number_in_two_open_papers_is_not_guessed(tmp_path: Path) -> None:
    store = store_with(tmp_path)
    maths = PAPER.model_copy(
        update={
            "package_id": "maths-1", "exam_id": "maths-2026", "exam_code": "MATHS-2026",
            "outline": PAPER.outline.model_copy(update={"exam_id": "maths-2026"}),
            "candidates": tuple(
                c.model_copy(update={"candidate_id": f"m-{c.candidate_id}"})
                for c in PAPER.candidates
            ),
        }
    )  # fmt: skip
    store.import_package(read_package(write_package(maths)))
    store.set_open("maths-2026", True)
    platform = LocalAdapter(store)
    with pytest.raises(AdapterRejected):
        await platform.verify_candidate("20260001PR", "")  # which paper? not guessed
    assert (await platform.verify_candidate("20260001PR", "MATHS-2026")).candidate_id == (
        "m-practice-01"
    )


async def test_a_paper_with_answers_cannot_be_replaced(tmp_path: Path) -> None:
    store = store_with(tmp_path)
    changed = PAPER.model_copy(update={"title": "A different printing"})
    store.import_package(read_package(write_package(changed)))  # no answers yet: fine
    platform = LocalAdapter(store)
    s = await platform.verify_candidate("20260001PR", "")
    await platform.save_answer(s.candidate_id, 1, "A", "op-1")
    with pytest.raises(StoreError, match="already has answers"):
        store.import_package(read_package(write_package(PAPER)))


def test_photos_travel_with_the_package(tmp_path: Path) -> None:
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"
    store = ExamStore(tmp_path / "exams.db")
    store.import_package(read_package(write_package(PAPER, {"practice-01": jpeg})))
    assert store.photo("practice-01") == jpeg
    assert store.photo("practice-02") is None


def test_audio_mode_candidates_are_marked(tmp_path: Path) -> None:
    assert [c.audio_mode for c in PAPER.candidates].count(True) == 2
    store = store_with(tmp_path)
    assert store.candidate("practice-07").profile.audio_mode


def test_the_shuffle_settings_default_to_on() -> None:
    assert Shuffle() == Shuffle(questions=True, options=True, keep_option_order=())
    assert isinstance(PAPER.candidates[0], PackageCandidate)
