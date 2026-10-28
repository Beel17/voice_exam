"""Typed WebSocket messages between the exam PC client and the service (``/v1/session``).

Every text frame is validated here before any other code sees it. A frame that does not
match the contract is rejected whole, never partially applied. Binary frames carry audio
(16 kHz PCM up, 22.05 kHz PCM down) and are handled by the speech layer, not here.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError

SCHEMA_VERSION = 1

# Longer than any real spoken command; bounds the work hostile input can cause.
MAX_TEXT_CHARS = 500
# Checked before JSON parsing so an oversized frame costs nothing to reject.
MAX_FRAME_BYTES = 4096

OptionLetter = Literal["A", "B", "C", "D", "E"]
Key = Literal["1", "2", "3", "4", "5", "R", "N", "P", "Enter", "Esc"]
SayKind = Literal["question", "options", "prompt", "confirm"]

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:/-]{1,64}$")]


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Messages from the exam PC ---------------------------------------------------------


class Hello(_Message):
    """Sent on connect and on every reconnect."""

    type: Literal["hello"] = "hello"
    schema_version: int
    device_id: Identifier
    token: Annotated[str, StringConstraints(min_length=16, max_length=256)]
    resume_session_id: Identifier | None = None
    # The last ``heard.seq`` the client received, so a resumed turn is never run twice.
    last_seq: Annotated[int, Field(ge=0)] | None = None


class Login(_Message):
    """Identifies the candidate when the secure browser or invigilator supplies it.

    Saying the registration number aloud is the other route; it arrives as ``text`` (and
    later as speech) and is resolved by the session, not by the contract.
    """

    type: Literal["login"] = "login"
    reg_no: Identifier
    exam_code: Identifier


class TextInput(_Message):
    """A typed utterance. Takes the same path as recognised speech after STT."""

    type: Literal["text"] = "text"
    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_TEXT_CHARS)]


class TalkStart(_Message):
    """Talk key pressed: stops any speech playing."""

    type: Literal["talk_start"] = "talk_start"


class TalkEnd(_Message):
    """Talk key released: recognition finalises."""

    type: Literal["talk_end"] = "talk_end"


class KeyPress(_Message):
    """Keyboard fallback; every key maps to the same action as its voice command."""

    type: Literal["key"] = "key"
    key: Key


class PlaybackDone(_Message):
    type: Literal["playback_done"] = "playback_done"
    utterance_id: Identifier


class Ping(_Message):
    type: Literal["ping"] = "ping"


ClientMessage = Annotated[
    Hello | Login | TextInput | TalkStart | TalkEnd | KeyPress | PlaybackDone | Ping,
    Field(discriminator="type"),
]


# --- Messages from the service ---------------------------------------------------------


class SessionInfo(_Message):
    """Reply to ``hello``: the ID the client must send back to resume after a drop."""

    type: Literal["session"] = "session"
    session_id: Identifier
    schema_version: int = SCHEMA_VERSION
    resumed: bool


class Say(_Message):
    """Starts a spoken reply. ``text`` also feeds a braille display or captions."""

    type: Literal["say"] = "say"
    utterance_id: Identifier
    text: str
    kind: SayKind


class Stop(_Message):
    """Cut playback (barge-in, navigation)."""

    type: Literal["stop"] = "stop"
    utterance_id: Identifier


class Heard(_Message):
    """What was recognised, for the on-screen log and audit."""

    type: Literal["heard"] = "heard"
    text: str
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    seq: Annotated[int, Field(ge=0)]


class PendingConfirm(_Message):
    """What a "yes" would do right now."""

    kind: Literal["answer", "clear", "submit"]
    question: Annotated[int, Field(ge=1)] | None = None
    choice: OptionLetter | None = None


class State(_Message):
    """Sent after every change so the PC can show progress to invigilators."""

    type: Literal["state"] = "state"
    question: Annotated[int, Field(ge=1)]
    of: Annotated[int, Field(ge=1)]
    answered: Annotated[int, Field(ge=0)]
    unanswered: Annotated[int, Field(ge=0)]
    # None while the platform cannot be reached; time is never taken from the PC clock.
    time_left_s: Annotated[int, Field(ge=0)] | None
    pending_confirm: PendingConfirm | None = None
    speed: Annotated[float, Field(ge=0.8, le=2.5)]
    cue_volume: Annotated[float, Field(ge=0.0, le=1.0)]


class Pong(_Message):
    type: Literal["pong"] = "pong"


class ErrorCode(StrEnum):
    BAD_MESSAGE = "bad_message"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    UNAUTHORIZED = "unauthorized"
    SESSION_NOT_FOUND = "session_not_found"
    NOT_UNDERSTOOD = "not_understood"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    BUSY = "busy"
    RATE_LIMITED = "rate_limited"
    NOT_IDENTIFIED = "not_identified"
    # This session was taken over by a newer connection (the PC reconnected).
    SUPERSEDED = "superseded"


class Error(_Message):
    """A recoverable problem; the session carries on."""

    type: Literal["error"] = "error"
    code: ErrorCode
    message: str


ServerMessage = Annotated[
    SessionInfo | Say | Stop | Heard | State | Pong | Error,
    Field(discriminator="type"),
]


# --- Parsing ----------------------------------------------------------------------------


class BadMessage(Exception):
    """A frame that does not match the contract. Safe to show: it holds no frame content."""


_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)
_server_adapter: TypeAdapter[ServerMessage] = TypeAdapter(ServerMessage)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    size = len(raw.encode()) if isinstance(raw, str) else len(raw)
    if size > MAX_FRAME_BYTES:
        raise BadMessage(f"frame is {size} bytes; limit is {MAX_FRAME_BYTES}")
    try:
        return _client_adapter.validate_json(raw)
    except ValidationError as e:
        # Report field locations only: echoing input values back would let a client
        # reflect arbitrary text through the service's own messages.
        fields = sorted({".".join(str(p) for p in err["loc"]) or "<root>" for err in e.errors()})
        raise BadMessage(f"invalid fields: {', '.join(fields)}") from None


def parse_server_message(raw: str | bytes) -> ServerMessage:
    """Used by the client and tests; the service only ever produces these."""
    return _server_adapter.validate_json(raw)
