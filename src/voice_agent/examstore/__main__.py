"""Set up the centre's exam store and load exams into it.

    python -m voice_agent.examstore init                 # create the database
    python -m voice_agent.examstore import paper.zip     # load an exam package
    python -m voice_agent.examstore list                 # what is loaded, what is open
    python -m voice_agent.examstore open EXAM            # candidates may sign in to it
    python -m voice_agent.examstore close EXAM
    python -m voice_agent.examstore sample out.zip       # a practice package to try it

The database is <data_dir>/exams.db from the centre's configuration, or --db. Point the
service at it with ``[adapter] kind = "local"``.
"""

import argparse
from pathlib import Path

from voice_agent.adapters.fake_adapter import load_fixture
from voice_agent.config import load_settings
from voice_agent.examstore.package import (
    ExamPackage,
    PackageCandidate,
    read_package,
    write_package,
)
from voice_agent.examstore.store import ExamStore, StoreError

# Plainly fictional people for the practice package.
_SAMPLE_NAMES = ("Ada Okafor", "Bayo Adeyemi", "Chidinma Eze", "Daniel Musa", "Efe Ighodaro",
                 "Fatima Bello", "Gbenga Ojo", "Halima Sani")  # fmt: skip


def sample_package() -> ExamPackage:
    """The bundled 60-question sample exam as a package, with eight practice candidates;
    the last two sit in audio mode."""
    fixture = load_fixture("sample_60")
    candidates = tuple(
        PackageCandidate(
            candidate_id=f"practice-{k:02d}",
            exam_number=f"2026{k:04d}PR",
            full_name=name,
            audio_mode=k > 6,
            seat=str(k),
        )
        for k, name in enumerate(_SAMPLE_NAMES, start=1)
    )
    return ExamPackage(
        package_id="practice-utme-sample-1",
        exam_id=fixture.exam_id,
        exam_code=fixture.exam_code,
        title="Practice UTME - Use of English, Mathematics, Physics, Biology",
        instructions=(
            "This paper has 60 questions in 4 subjects. You have 1 hour.",
            "Answer every question. Each has one correct option.",
            "An answer is saved only when you confirm it.",
        ),
        outline=fixture.outline,
        questions=fixture.questions,
        candidates=candidates,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db", type=Path, help="default: <data_dir>/exams.db")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the database")
    imp = sub.add_parser("import", help="load an exam package (.zip)")
    imp.add_argument("package", type=Path)
    sub.add_parser("list", help="the exams loaded, and which are open")
    for name in ("open", "close"):
        sub.add_parser(name, help=f"{name} an exam for signing in").add_argument("exam_id")
    sample = sub.add_parser("sample", help="write a practice package")
    sample.add_argument("out", type=Path)
    args = parser.parse_args()

    if args.command == "sample":
        args.out.write_bytes(write_package(sample_package()))
        print(f"written: {args.out}")
        return
    path = args.db or load_settings().data_dir / "exams.db"
    try:
        store = ExamStore(path)
    except StoreError as e:
        raise SystemExit(str(e)) from None
    try:
        match args.command:
            case "init":
                print(f"exam store ready: {path}")
            case "import":
                loaded = read_package(args.package.read_bytes())
                result = store.import_package(loaded)
                p = loaded.package
                print(f"{result}: {p.exam_id} ({p.title}), {len(p.candidates)} candidates")
            case "list":
                exams = store.exams()
                for exam in exams:
                    state = "OPEN" if exam.is_open else "closed"
                    print(
                        f"{state:7} {exam.exam_id}  [{exam.exam_code}]  {exam.title}  "
                        f"({exam.candidates} candidates)"
                    )
                if not exams:
                    print("no exams loaded")
            case "open" | "close":
                store.set_open(args.exam_id, args.command == "open")
                print(f"{args.exam_id}: {args.command}")
    except (StoreError, ValueError) as e:
        raise SystemExit(f"refused: {e}") from None
    finally:
        store.close()


if __name__ == "__main__":
    main()
