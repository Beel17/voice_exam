"""The sync API's messages, shared by the centre's client and the stand-in cloud.

The cloud implements these calls, all authenticated with the centre's bearer token:

    GET  /v1/centres/{centre_id}/packages      the packages assigned to this centre
    GET  /v1/packages/{package_id}             one package file (a zip; see examstore)
    POST /v1/centres/{centre_id}/results       a batch of the centre's logged writes
    GET  /v1/candidates/{candidate}            who a candidate is, and their centre (only
                                               if the centre looks candidates up at sign-in)
    GET  /v1/candidates/{candidate_id}/package the package that candidate sits (optional:
                                               without it, their centre's are all pulled)

``{candidate}`` is what the candidate signed in with: their candidate ID, or their
examination number if the cloud can find them by it; 404 if it cannot.

Results are the centre's log of operations, oldest first: every answer saved or cleared
and every submission, against the paper's own question numbers and letters (never a
candidate's shuffled view). The cloud must accept an operation it has already received
(same ``operation_id``, same content) as a success without applying it again, so a
centre can always retry a batch whose reply it lost. spec/sync.openapi.json is the
generated description of this API.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from voice_agent.contract import Identifier, OptionLetter

MAX_BATCH = 500


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PackageInfo(_Model):
    package_id: Identifier
    exam_id: Identifier
    title: Annotated[str, Field(max_length=200)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size: Annotated[int, Field(ge=1, le=512 * 1024 * 1024)]


class PackageList(_Model):
    packages: tuple[PackageInfo, ...]


class ResultOperation(_Model):
    operation_id: Identifier
    seq: Annotated[int, Field(ge=1)]  # order at the centre
    candidate_id: Identifier
    exam_id: Identifier
    kind: Literal["save", "submit"]
    question: Annotated[int, Field(ge=1)] | None = None  # the paper's number
    choice: OptionLetter | None = None  # None clears the answer
    at: float  # Unix time at the centre


class ResultBatch(_Model):
    centre_id: Identifier
    operations: Annotated[tuple[ResultOperation, ...], Field(min_length=1, max_length=MAX_BATCH)]


class CandidatePackage(PackageInfo):
    """The package one candidate sits, and the centre it is for."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    centre_id: Identifier | None = None


class CandidateRecord(BaseModel):
    """The cloud's answer about one candidate. Only these fields are used; others the
    cloud sends (names of the centre, exam and session) are ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    candidate_id: Identifier
    exam_number: Annotated[str, Field(max_length=64)] | None = None
    # Either names the centre in its packages' and results' calls (``centre_from`` under
    # [sync] says which); the other may be absent.
    centre_id: Identifier | None = None
    centre_code: Identifier | None = None
    status: Annotated[str, Field(max_length=40)] | None = None


class BatchReceipt(_Model):
    # Every operation the cloud now holds from the batch, new or already received.
    accepted: tuple[Identifier, ...]
