"""The proposed HTTP contract for the platform team (``spec/adapter.openapi.json``).

Built from the same models the service validates with, so the spec cannot drift from the
code; a test fails if the committed file is stale. Regenerate with:

    uv run python -m voice_agent.adapters.spec spec/adapter.openapi.json
"""

import json
import sys
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict
from pydantic.json_schema import models_json_schema

from voice_agent.adapters.base import (
    CandidateSession,
    ExamOutline,
    Question,
    RejectReason,
    SavedAnswers,
)
from voice_agent.contract import Identifier, OptionLetter

SPEC_VERSION: Final = "0.1.0"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerifyRequest(_Body):
    reg_no: Identifier
    exam_code: Identifier


class SaveAnswerRequest(_Body):
    choice: OptionLetter | None
    operation_id: Identifier


class SubmitRequest(_Body):
    operation_id: Identifier


class TimeLeftResponse(_Body):
    seconds: int


class ErrorBody(_Body):
    code: RejectReason
    message: str


# Which refusals each status carries; the service acts on ``code``, never the status.
REJECT_STATUS: Final[dict[RejectReason, int]] = {
    RejectReason.NOT_REGISTERED: 404,
    RejectReason.UNKNOWN_CANDIDATE: 404,
    RejectReason.NO_SUCH_EXAM: 404,
    RejectReason.NO_SUCH_QUESTION: 404,
    RejectReason.INVALID_CHOICE: 422,
    RejectReason.ALREADY_SUBMITTED: 409,
    RejectReason.TIME_UP: 409,
    RejectReason.OPERATION_CONFLICT: 409,
}

_MODELS: Final[tuple[type[BaseModel], ...]] = (
    CandidateSession,
    ExamOutline,
    Question,
    SavedAnswers,
    VerifyRequest,
    SaveAnswerRequest,
    SubmitRequest,
    TimeLeftResponse,
    ErrorBody,
)

_DESCRIPTION: Final = """\
The operations the Voice Exam Agent needs from an exam platform, and nothing more.

Rules every implementation must follow (checked by spec/conformance):

1. **No answer key, ever.** No response may contain the correct option or anything
   derived from it. Responses are closed objects: unknown fields are rejected by the
   service, which then treats the platform as misconfigured.
2. **Writes are idempotent by `operation_id`.** Repeating an operation that was already
   applied returns success and changes nothing, even if newer operations were applied in
   between. Reusing an `operation_id` with a different payload is `operation_conflict`.
3. **Candidates are isolated.** A `candidate_id` only ever reads and writes its own
   answers.
4. **Time comes from the platform.** `time-left` follows the platform's own timing rules
   (extra time, pauses); the service never uses a client clock.
5. **Errors** use the ErrorBody shape. The service retries on timeouts and 5xx responses
   (same `operation_id`) and never retries a 4xx.

Served on the centre network or loopback only; requests carry the bearer token issued
when the centre is set up.
"""


def _json(ref: str) -> dict[str, Any]:
    return {"content": {"application/json": {"schema": {"$ref": ref}}}}


def _op(
    summary: str,
    ok: str | None,
    rejects: tuple[RejectReason, ...],
    body: str | None = None,
    params: tuple[str, ...] = (),
) -> dict[str, Any]:
    responses: dict[str, Any] = (
        {"200": {"description": "OK", **_json(ok)}} if ok else {"204": {"description": "Done"}}
    )
    for status in sorted({REJECT_STATUS[r] for r in rejects}):
        codes = ", ".join(r.value for r in rejects if REJECT_STATUS[r] == status)
        responses[str(status)] = {
            "description": f"Refused: {codes}",
            **_json("#/components/schemas/ErrorBody"),
        }
    op: dict[str, Any] = {"summary": summary, "responses": responses}
    if params:
        op["parameters"] = [
            {
                "name": p,
                "in": "path",
                "required": True,
                "schema": {"type": "integer", "minimum": 1} if p == "n" else {"type": "string"},
            }
            for p in params
        ]
    if body:
        op["requestBody"] = {"required": True, **_json(body)}
    return op


def build_openapi() -> dict[str, Any]:
    _, defs = models_json_schema(
        [(m, "validation") for m in _MODELS], ref_template="#/components/schemas/{model}"
    )
    s = "#/components/schemas/"
    R = RejectReason
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Voice Exam Agent: Exam Data Adapter",
            "version": SPEC_VERSION,
            "description": _DESCRIPTION,
        },
        "servers": [{"url": "http://127.0.0.1:8080"}],
        "security": [{"centreToken": []}],
        "paths": {
            "/v1/candidates/verify": {
                "post": _op(
                    "Check registration and start (or resume) the candidate's exam",
                    s + "CandidateSession",
                    (R.NOT_REGISTERED,),
                    body=s + "VerifyRequest",
                )
            },
            "/v1/exams/{exam_id}/outline": {
                "get": _op(
                    "Number of questions, sections, duration",
                    s + "ExamOutline",
                    (R.NO_SUCH_EXAM,),
                    params=("exam_id",),
                )
            },
            "/v1/exams/{exam_id}/questions/{n}": {
                "get": _op(
                    "Question text and options; never the answer key",
                    s + "Question",
                    (R.NO_SUCH_EXAM, R.NO_SUCH_QUESTION),
                    params=("exam_id", "n"),
                )
            },
            "/v1/candidates/{candidate_id}/answers": {
                "get": _op(
                    "The candidate's own saved answers",
                    s + "SavedAnswers",
                    (R.UNKNOWN_CANDIDATE,),
                    params=("candidate_id",),
                )
            },
            "/v1/candidates/{candidate_id}/answers/{n}": {
                "put": _op(
                    "Save a confirmed answer (choice null clears it); idempotent",
                    None,
                    (
                        R.UNKNOWN_CANDIDATE,
                        R.NO_SUCH_QUESTION,
                        R.INVALID_CHOICE,
                        R.ALREADY_SUBMITTED,
                        R.TIME_UP,
                        R.OPERATION_CONFLICT,
                    ),
                    body=s + "SaveAnswerRequest",
                    params=("candidate_id", "n"),
                )
            },
            "/v1/candidates/{candidate_id}/time-left": {
                "get": _op(
                    "Seconds left under the platform's timing rules",
                    s + "TimeLeftResponse",
                    (R.UNKNOWN_CANDIDATE,),
                    params=("candidate_id",),
                )
            },
            "/v1/candidates/{candidate_id}/submit": {
                "post": _op(
                    "Final submission; idempotent",
                    None,
                    (R.UNKNOWN_CANDIDATE, R.ALREADY_SUBMITTED, R.OPERATION_CONFLICT),
                    body=s + "SubmitRequest",
                    params=("candidate_id",),
                )
            },
        },
        "components": {
            "securitySchemes": {"centreToken": {"type": "http", "scheme": "bearer"}},
            "schemas": defs["$defs"],
        },
    }


def render() -> str:
    return json.dumps(build_openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(render(), encoding="utf-8", newline="\n")
