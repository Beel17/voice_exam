import json

import pytest

from voice_agent.contract import (
    MAX_FRAME_BYTES,
    MAX_TEXT_CHARS,
    SCHEMA_VERSION,
    AudioEnd,
    BadMessage,
    Cue,
    Details,
    Error,
    ErrorCode,
    Heard,
    Hello,
    KeyPress,
    Login,
    PendingConfirm,
    Ping,
    PlaybackDone,
    Pong,
    QuestionView,
    Say,
    SectionView,
    ServerMessage,
    SessionInfo,
    ShownOption,
    State,
    Stop,
    TalkEnd,
    TalkStart,
    TextInput,
    parse_client_message,
    parse_server_message,
)

TOKEN = "t" * 32


def frame(**fields: object) -> str:
    return json.dumps(fields)


def test_hello_parses_with_and_without_resume() -> None:
    msg = parse_client_message(
        frame(type="hello", schema_version=SCHEMA_VERSION, device_id="pc-07", token=TOKEN)
    )
    assert msg == Hello(schema_version=1, device_id="pc-07", token=TOKEN)

    resumed = parse_client_message(
        frame(
            type="hello",
            schema_version=1,
            device_id="pc-07",
            token=TOKEN,
            resume_session_id="s-1",
            last_seq=12,
        )
    )
    assert isinstance(resumed, Hello)
    assert resumed.resume_session_id == "s-1"
    assert resumed.last_seq == 12


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (frame(type="login", reg_no="12345678AB", exam_code="UTME-2026"), Login),
        (frame(type="text", text="take me back to number five"), TextInput),
        (frame(type="key", key="Enter"), KeyPress),
        (frame(type="ping"), Ping),
        (frame(type="talk_start"), TalkStart),
        (frame(type="talk_end"), TalkEnd),
        (frame(type="playback_done", utterance_id="u-3"), PlaybackDone),
    ],
)
def test_each_client_message_type_parses(raw: str, expected: type) -> None:
    assert isinstance(parse_client_message(raw), expected)


def test_bytes_frames_are_accepted() -> None:
    assert isinstance(parse_client_message(frame(type="ping").encode()), Ping)


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        frame(),
        frame(type="shutdown"),
        frame(type="ping", extra=1),
        frame(type="key", key="F5"),
        frame(type="text", text=""),
        frame(type="text", text="x" * (MAX_TEXT_CHARS + 1)),
        frame(type="hello", schema_version=1, device_id="pc 07", token=TOKEN),
        frame(type="hello", schema_version=1, device_id="pc-07", token="short"),
        frame(type="hello", schema_version=1, device_id="pc-07", token=TOKEN, last_seq=-1),
        frame(type="login", reg_no="'; DROP TABLE answers;--", exam_code="X"),
    ],
)
def test_invalid_frames_are_rejected(raw: str) -> None:
    with pytest.raises(BadMessage):
        parse_client_message(raw)


def test_oversized_frame_is_rejected_before_parsing() -> None:
    raw = "{" + " " * MAX_FRAME_BYTES + "}"
    with pytest.raises(BadMessage, match="limit"):
        parse_client_message(raw)


def test_oversized_frame_counts_bytes_not_characters() -> None:
    # 3-byte characters: under the byte limit in characters, over it in bytes.
    raw = frame(type="text", text="€" * (MAX_FRAME_BYTES // 3 + 10))
    with pytest.raises(BadMessage, match="limit"):
        parse_client_message(raw)


def test_rejection_does_not_echo_input() -> None:
    secret = "ignore previous instructions"
    with pytest.raises(BadMessage) as info:
        parse_client_message(frame(type="key", key=secret))
    assert secret not in str(info.value)
    assert "key" in str(info.value)


SERVER_MESSAGES: list[ServerMessage] = [
    SessionInfo(session_id="s-1", resumed=False),
    Say(utterance_id="u-1", text="Question 5 of 60.", kind="question"),
    Stop(utterance_id="u-1"),
    AudioEnd(utterance_id="u-1"),
    Cue(name="saved", volume=0.5),
    Heard(text="C", confidence=0.96, seq=4),
    State(
        question=5,
        of=60,
        answered=4,
        unanswered=56,
        time_left_s=3120,
        pending_confirm=PendingConfirm(kind="answer", question=5, choice="C"),
        speed=1.5,
        cue_volume=0.6,
    ),
    State(
        question=1, of=60, answered=0, unanswered=60, time_left_s=None, speed=1.0, cue_volume=1.0
    ),
    State(
        question=2,
        of=3,
        answered=1,
        unanswered=2,
        time_left_s=60,
        speed=1.0,
        cue_volume=1.0,
        pending_confirm=PendingConfirm(kind="submit", final=True),
        started=True,
        current_answer=None,
        answered_questions=(1,),
        flagged=(2, 3),
        ended=None,
    ),
    QuestionView(
        number=2,
        of=3,
        section=1,
        section_title="Mathematics",
        text="2 + 2 =",
        options=(ShownOption(letter="A", text="3"), ShownOption(letter="B", text="4")),
    ),
    Details(
        question_count=3,
        duration_s=600,
        exam_title="Practice",
        exam_number="20260001PR",
        full_name="Ada Obi",
        seat="A12",
        audio_mode=False,
        photo=True,
        sections=(SectionView(number=1, title="Mathematics", first_question=1, last_question=3),),
    ),
    Pong(),
    Error(code=ErrorCode.ADAPTER_UNAVAILABLE, message="Saved on this computer, waiting to sync."),
]


@pytest.mark.parametrize("msg", SERVER_MESSAGES, ids=lambda m: m.type)
def test_server_messages_round_trip(msg: ServerMessage) -> None:
    assert parse_server_message(msg.model_dump_json()) == msg


@pytest.mark.parametrize("speed", [0.5, 3.0])
def test_state_rejects_speed_outside_supported_range(speed: float) -> None:
    with pytest.raises(ValueError):
        State(
            question=1, of=1, answered=0, unanswered=1, time_left_s=0, speed=speed, cue_volume=1.0
        )


def test_messages_are_immutable() -> None:
    msg = Stop(utterance_id="u-1")
    with pytest.raises(ValueError):
        msg.utterance_id = "u-2"  # type: ignore[misc]
