"""Typed WebSocket messages between the exam PC client and the service (``/v1/session``).

Every text frame is validated here before any other code sees it. A frame that does not
match the contract is rejected whole, never partially applied. Binary frames carry audio
(16 kHz PCM up, 22.05 kHz PCM down) and are handled by the speech layer, not here.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

SCHEMA_VERSION = 1

# Longer than any real spoken command; bounds the work hostile input can cause.
MAX_TEXT_CHARS = 500
# Checked before JSON parsing so an oversized frame costs nothing to reject.
MAX_FRAME_BYTES = 4096

# Audio up: 16 kHz mono 16-bit little-endian PCM, sent in 20-100 ms frames while the talk
# key is held. A frame may be up to 200 ms (a busy client may batch two); one press of the
# talk key may hold up to 30 s, far longer than any command.
AUDIO_SAMPLE_RATE = 16_000
AUDIO_BYTES_PER_SECOND = AUDIO_SAMPLE_RATE * 2
MAX_AUDIO_FRAME_BYTES = AUDIO_BYTES_PER_SECOND // 5
MAX_UTTERANCE_SECONDS = 30

# Audio down: each ``say`` is followed by its speech as 22.05 kHz mono 16-bit PCM frames of
# 200 ms, then ``audio_end``; the PC reports ``playback_done`` once it has played them.
AUDIO_OUT_SAMPLE_RATE = 22_050
AUDIO_OUT_FRAME_BYTES = AUDIO_OUT_SAMPLE_RATE * 2 // 5

OptionLetter = Literal["A", "B", "C", "D", "E"]
# S submits (still asked twice), F flags to revisit, T says the time left.
Key = Literal["1", "2", "3", "4", "5", "R", "N", "P", "S", "F", "T", "Enter", "Esc"]
SayKind = Literal["question", "options", "prompt", "confirm"]
CueName = Literal["listening", "saved", "not_understood", "time_warning"]

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:/-]{1,64}$")]
QuestionNo = Annotated[int, Field(ge=1, le=999)]


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
    # False for a screen that reads the exam itself (standard mode): replies still come
    # as ``say`` text, but no speech audio is made for them.
    audio: bool = True


class Login(_Message):
    """Identifies the candidate: their examination number, typed on the sign-in screen
    or supplied by the invigilator. ``exam_code`` is needed only where the platform asks
    for it; the centre's exam store finds the paper among those open for sign-in.
    """

    type: Literal["login"] = "login"
    reg_no: Identifier
    exam_code: Identifier | None = None


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


ScreenAction = Literal["select", "go_to", "next", "previous", "flag", "unflag", "submit",
                       "confirm", "cancel"]  # fmt: skip


class Act(_Message):
    """A click on the exam screen (standard mode). Each is the same action as its voice
    command, with one difference: ``select`` saves the clicked option (or, with no
    ``choice``, clears the answer) at once, because clicking it is the confirmation.
    ``select`` names the question on screen; it is refused for any other question.
    ``flag`` and ``unflag`` mark a question to revisit (the current one if none given).
    """

    type: Literal["act"] = "act"
    action: ScreenAction
    question: QuestionNo | None = None
    choice: OptionLetter | None = None

    @model_validator(mode="after")
    def _arguments_fit(self) -> Self:
        if self.action in ("select", "go_to") and self.question is None:
            raise ValueError(f"{self.action} needs a question")
        if self.choice is not None and self.action != "select":
            raise ValueError("only select takes a choice")
        if self.question is not None and self.action not in ("select", "go_to", "flag", "unflag"):
            raise ValueError(f"{self.action} takes no question")
        return self


GuideStep = Literal["welcome", "left", "right", "sound_check", "mic_check", "sign_in"]


class Guide(_Message):
    """Audio mode, before sign-in: speak one step of the welcome, the headphone and
    microphone checks, or the voice sign-in. ``mic_check`` makes the next thing heard be
    read back; ``sign_in`` makes it be taken as the examination number, read back, and
    signed in with on "yes" (or Enter). Every step's words are the service's own."""

    type: Literal["guide"] = "guide"
    step: GuideStep


class PlaybackDone(_Message):
    type: Literal["playback_done"] = "playback_done"
    utterance_id: Identifier


class Ping(_Message):
    type: Literal["ping"] = "ping"


ClientMessage = Annotated[
    Hello | Login | TextInput | TalkStart | TalkEnd | KeyPress | Act | Guide | PlaybackDone | Ping,
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


class AudioEnd(_Message):
    """All of a ``say``'s audio frames have been sent."""

    type: Literal["audio_end"] = "audio_end"
    utterance_id: Identifier


class Cue(_Message):
    """Play a sound cue (fetched once from ``/v1/cues/{name}.wav``) at this volume.
    "listening" is never sent: the PC plays it itself the moment the talk key goes down."""

    type: Literal["cue"] = "cue"
    name: CueName
    volume: Annotated[float, Field(ge=0.0, le=1.0)]


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
    # Submitting asks twice; this is the second, after which the exam is submitted.
    final: bool = False


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
    # For the screen: False until the first question has been read (the introduction).
    started: bool = False
    current_answer: OptionLetter | None = None
    # The question navigator: which are answered, and which are marked to revisit.
    answered_questions: tuple[int, ...] = ()
    flagged: tuple[int, ...] = ()
    ended: Literal["submitted", "time_up"] | None = None


class ShownOption(_Message):
    letter: OptionLetter
    text: str


class QuestionView(_Message):
    """The question now current, for a screen to show (sent before it is read aloud).
    Text is the paper's own, verbatim; ``state`` carries the answer and the flags."""

    type: Literal["question"] = "question"
    number: QuestionNo
    of: QuestionNo
    section: Annotated[int, Field(ge=1)] | None
    section_title: str | None
    text: str
    options: tuple[ShownOption, ...]


class SectionView(_Message):
    number: Annotated[int, Field(ge=1)]
    title: str
    first_question: QuestionNo
    last_question: QuestionNo


class Details(_Message):
    """Sent once after ``session``: the exam and, where the platform has them, the
    candidate's own details for the identity check before the exam starts. The photo,
    if ``photo`` is true, is at ``/v1/sessions/{session_id}/photo`` (device token)."""

    type: Literal["details"] = "details"
    question_count: QuestionNo
    duration_s: Annotated[int, Field(ge=1)]
    sections: tuple[SectionView, ...]
    exam_title: str | None = None
    exam_number: str | None = None
    full_name: str | None = None
    seat: str | None = None
    audio_mode: bool | None = None
    photo: bool = False


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
    SessionInfo
    | Details
    | QuestionView
    | Say
    | Stop
    | AudioEnd
    | Cue
    | Heard
    | State
    | Pong
    | Error,
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
