"""The exam package: one exam paper and its candidates, as the cloud sends it.

A package is a zip holding ``package.json`` and, optionally, ``photos/<candidate_id>.jpg``
(shown on screen so the invigilator can check the candidate is who they say). There is
no answer key in it, by design: the models are closed, so a package that carries one is
rejected rather than stored.
"""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from voice_agent.adapters.base import ExamOutline, Question, QuestionNumber
from voice_agent.contract import Identifier

FORMAT: Final = 1
MAX_PHOTO_BYTES: Final = 512 * 1024
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackageCandidate(_Model):
    candidate_id: Identifier
    # What the candidate types or says to sign in: their examination number.
    exam_number: Identifier
    full_name: Name
    email: Annotated[str, StringConstraints(max_length=200)] | None = None
    phone: Annotated[str, StringConstraints(max_length=40)] | None = None
    # Blind or visually impaired: the exam starts in audio mode.
    audio_mode: bool = False
    seat: Annotated[str, StringConstraints(max_length=20)] | None = None


class Shuffle(_Model):
    """How each candidate's version differs from the paper. Questions move only within
    their section (subject), so subjects stay together."""

    questions: bool = True
    options: bool = True
    # Questions whose options must keep their order ("All of the above", "Both A and B").
    keep_option_order: tuple[QuestionNumber, ...] = ()


class ExamPackage(_Model):
    format: Literal[1] = FORMAT
    package_id: Identifier
    exam_id: Identifier
    # Tells papers apart where a candidate sits several; the screen shows the title.
    exam_code: Identifier
    title: Name  # e.g. "JAMB 2026 - English Language Paper 1 (Objective)"
    instructions: tuple[Text, ...] = ()
    outline: ExamOutline
    questions: tuple[Question, ...]
    candidates: tuple[PackageCandidate, ...]
    shuffle: Shuffle = Shuffle()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.outline.exam_id != self.exam_id:
            raise ValueError("outline.exam_id must match exam_id")
        if [q.number for q in self.questions] != list(range(1, self.outline.question_count + 1)):
            raise ValueError("questions must be numbered 1..question_count in order")
        for field in ("candidate_id", "exam_number"):
            values = [getattr(c, field) for c in self.candidates]
            if len(values) != len(set(values)):
                raise ValueError(f"duplicate {field}")
        return self


class LoadedPackage(_Model):
    package: ExamPackage
    photos: dict[str, bytes]  # candidate_id -> JPEG
    sha256: str  # of the package file, as received


def read_package(data: bytes) -> LoadedPackage:
    """Check and open a package file. Raises ValueError with a plain reason."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("not a package (not a zip file)") from None
    with archive:
        names = set(archive.namelist())
        if "package.json" not in names:
            raise ValueError("not a package (no package.json)")
        package = ExamPackage.model_validate_json(archive.read("package.json"))
        known = {c.candidate_id for c in package.candidates}
        photos: dict[str, bytes] = {}
        for name in names - {"package.json"}:
            folder, _, file = name.partition("/")
            stem = Path(file).stem
            if folder != "photos" or Path(file).suffix.lower() != ".jpg" or stem not in known:
                raise ValueError(f"unexpected file in package: {name}")
            info = archive.getinfo(name)
            if info.file_size > MAX_PHOTO_BYTES:
                raise ValueError(f"photo too large: {name}")
            photos[stem] = archive.read(name)
    return LoadedPackage(package=package, photos=photos, sha256=hashlib.sha256(data).hexdigest())


def write_package(package: ExamPackage, photos: dict[str, bytes] | None = None) -> bytes:
    """A package file, as the cloud builds one (and tests, and the sample tool)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("package.json", json.dumps(package.model_dump(mode="json"), indent=1))
        for candidate_id, jpeg in sorted((photos or {}).items()):
            archive.writestr(f"photos/{candidate_id}.jpg", jpeg)
    return out.getvalue()
