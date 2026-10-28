"""Talking over the WebSocket: talk key, audio frames, barge-in, busy and keyboard mode.

Recognition is a stand-in here (phase 2 step 2 brings the real recogniser): the "audio"
is the sentence's own bytes, so each test says exactly what the candidate said.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from tests.integration.test_api import hello, login, make_settings, send, spoken, turn
from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.api.runtime import Runtime
from voice_agent.contract import AUDIO_BYTES_PER_SECOND, MAX_AUDIO_FRAME_BYTES
from voice_agent.main import create_app
from voice_agent.runtime.degrade import Level
from voice_agent.runtime.pools import PoolBusy
from voice_agent.speech.stt import Recognition

FRAME = 3200  # 100 ms of 16 kHz 16-bit audio


class SentenceRecognizer:
    """Hears the sentence encoded in the audio bytes; records what it was given."""

    def __init__(self) -> None:
        self.busy = False
        self.heard: list[bytes] = []

    async def recognize(self, pcm: bytes) -> Recognition:
        self.heard.append(pcm)
        if self.busy:
            raise PoolBusy("recognition: queue full")
        return Recognition(text=pcm.rstrip(b"\0").decode(), confidence=0.9)


def as_audio(sentence: str) -> list[bytes]:
    data = sentence.encode()
    data += b"\0" * (-len(data) % FRAME or FRAME)
    return [data[k : k + FRAME] for k in range(0, len(data), FRAME)]


@pytest.fixture
def recognizer() -> SentenceRecognizer:
    return SentenceRecognizer()


@pytest.fixture
def client(tmp_path: Path, recognizer: SentenceRecognizer) -> Iterator[TestClient]:
    app = create_app(
        make_settings(tmp_path),
        adapter=FakeAdapter.with_bundled_fixtures(),
        recognizer=recognizer,
    )
    with TestClient(app) as c:
        yield c


def say_aloud(ws: WebSocketTestSession, sentence: str) -> list[dict[str, Any]]:
    """One press of the talk key; the turn's messages, without barge-in stops."""
    send(ws, type="talk_start")
    for frame in as_audio(sentence):
        ws.send_bytes(frame)
    send(ws, type="talk_end")
    return [m for m in turn(ws) if m["type"] != "stop"]


def receive(ws: WebSocketTestSession) -> dict[str, Any]:
    """The next message that is not a barge-in stop."""
    message: dict[str, Any] = ws.receive_json()
    while message["type"] == "stop":
        message = ws.receive_json()
    return message


def runtime(client: TestClient) -> Runtime:
    rt: Runtime = client.app.state.runtime  # type: ignore[attr-defined]
    return rt


def test_speaking_drives_the_exam(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        messages = say_aloud(ws, "start from question fifty")
        assert messages[0] == {
            "type": "heard",
            "text": "start from question fifty",
            "confidence": 0.9,
            "seq": 2,
        }
        assert spoken(messages).startswith("Question 50 of 60.")
        assert "you chose option D" in spoken(say_aloud(ws, "the one that says photosynthesis"))


def test_talk_key_stops_what_is_playing(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)  # the welcome is "playing"
        messages = say_aloud(ws, "start")
        said = [m["utterance_id"] for m in messages if m["type"] == "say"]
        assert len(said) == 3  # question, options, prompt
        send(ws, type="playback_done", utterance_id=said[0])  # the question finished

        send(ws, type="talk_start")
        stopped = [ws.receive_json(), ws.receive_json()]
        assert stopped == [{"type": "stop", "utterance_id": u} for u in sorted(said[1:])]
        send(ws, type="talk_end")
        assert spoken(turn(ws)).startswith("Sorry, I did not catch that.")  # nothing said


def test_nothing_is_stopped_when_nothing_plays(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        for m in say_aloud(ws, "start"):
            if m["type"] == "say":
                send(ws, type="playback_done", utterance_id=m["utterance_id"])
        send(ws, type="talk_start")
        send(ws, type="talk_end")
        assert turn(ws)[0]["type"] == "heard"  # no stop came first


@pytest.mark.parametrize(
    ("frame", "why"),
    [
        (b"\x01\x02\x03", "wrong size"),  # odd: not 16-bit samples
        (b"\0" * (MAX_AUDIO_FRAME_BYTES + 2), "wrong size"),
    ],
)
def test_bad_audio_frames_are_refused(client: TestClient, frame: bytes, why: str) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        send(ws, type="talk_start")
        ws.send_bytes(frame)
        assert why in receive(ws)["message"]


def test_audio_needs_the_talk_key(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        ws.send_bytes(b"\0" * FRAME)
        assert "without the talk key" in ws.receive_json()["message"]


def test_one_press_holds_at_most_thirty_seconds(
    client: TestClient, recognizer: SentenceRecognizer
) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        send(ws, type="talk_start")
        for _ in range(310):  # 31 s
            ws.send_bytes(b"\0" * FRAME)
        send(ws, type="talk_end")
        turn(ws)
    assert len(recognizer.heard[-1]) == 30 * AUDIO_BYTES_PER_SECOND


def test_speech_is_not_counted_as_flooding(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        send(ws, type="talk_start")
        for _ in range(200):  # 20 s of speech, well over the message limit in frames
            ws.send_bytes(b"\0" * FRAME)
        send(ws, type="talk_end")
        assert receive(ws)["type"] == "heard"


def test_talking_before_sign_in(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        ws.receive_json()
        send(ws, type="talk_start")
        assert ws.receive_json()["code"] == "not_identified"


def test_without_a_recogniser_audio_is_not_enabled(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path), adapter=FakeAdapter.with_bundled_fixtures())
    with TestClient(app) as c, c.websocket_connect("/v1/session") as ws:
        login(ws)
        send(ws, type="talk_start")
        assert ws.receive_json()["message"] == "Audio is not enabled."


def test_busy_recognition_says_one_moment(
    client: TestClient, recognizer: SentenceRecognizer
) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        recognizer.busy = True
        send(ws, type="talk_start")
        ws.send_bytes(as_audio("next")[0])
        send(ws, type="talk_end")
        assert receive(ws)["code"] == "busy"
        assert receive(ws)["text"].startswith("One moment, please.")
        recognizer.busy = False
        assert spoken(say_aloud(ws, "start")).startswith("Question 1 of 60.")


def test_keyboard_mode_and_back(client: TestClient, recognizer: SentenceRecognizer) -> None:
    rt = runtime(client)
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        say_aloud(ws, "start")

        client.portal.call(rt.manager.set_level, Level.KEYBOARD)  # type: ignore[union-attr]
        assert ws.receive_json()["text"].startswith("Voice is not available for a moment.")
        sent_before = len(recognizer.heard)
        send(ws, type="talk_start")
        stops_or_say = ws.receive_json()
        while stops_or_say["type"] == "stop":
            stops_or_say = ws.receive_json()
        assert stops_or_say["text"].startswith("Voice is not available")
        ws.send_bytes(as_audio("submit")[0])  # dropped quietly
        send(ws, type="talk_end")
        send(ws, type="key", key="N")  # the keyboard still works
        assert spoken(turn(ws)).startswith("Question 2 of 60.")
        assert len(recognizer.heard) == sent_before

        client.portal.call(rt.manager.set_level, Level.NORMAL)  # type: ignore[union-attr]
        assert ws.receive_json()["text"] == "Voice is back. You can speak again."
        assert spoken(say_aloud(ws, "next")).startswith("Question 3 of 60.")


def test_status_reports_the_level(client: TestClient) -> None:
    rt = runtime(client)
    client.portal.call(rt.manager.set_level, Level.FIXED_PROMPTS)  # type: ignore[union-attr]
    admin = {"Authorization": "Bearer admin-token-for-tests-0001"}
    assert client.get("/v1/status", headers=admin).json()["degradation_level"] == 2
