"""Speech out over the WebSocket: audio after each say, priorities, speed, barge-in, busy.

The voice is a stand-in that makes a fixed amount of "audio" per character, and can be
held mid-sentence to test barge-in; test_recognition.py uses the real one.
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from tests.integration.test_api import hello, make_settings, send
from tests.integration.test_api_audio import SentenceRecognizer, as_audio
from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.contract import AUDIO_OUT_FRAME_BYTES
from voice_agent.main import create_app
from voice_agent.runtime.pools import PoolBusy, Priority


class StandInVoice:
    def __init__(self) -> None:
        self.calls: list[tuple[str, float, Priority]] = []
        self.busy = False
        self.hold: asyncio.Event | None = None  # set to hold every sentence until released

    async def synthesize(self, sentence: str, speed: float, priority: Priority) -> bytes:
        self.calls.append((sentence, speed, priority))
        if self.busy:
            raise PoolBusy("synthesis: queue full")
        if self.hold is not None:
            await self.hold.wait()
        return b"\x01\x00" * 1000 * len(sentence)


@pytest.fixture
def voice() -> StandInVoice:
    return StandInVoice()


@pytest.fixture
def client(tmp_path: Path, voice: StandInVoice) -> Iterator[TestClient]:
    app = create_app(
        make_settings(tmp_path),
        adapter=FakeAdapter.with_bundled_fixtures(),
        recognizer=SentenceRecognizer(),
        synthesizer=voice,
    )
    with TestClient(app) as c:
        yield c


def frames_until(ws: WebSocketTestSession, kind: str) -> list[dict[str, Any] | int]:
    """Everything up to and including the next message of ``kind``: JSON as dicts, audio
    frames as their length in bytes."""
    got: list[dict[str, Any] | int] = []
    while True:
        frame = ws.receive()
        if frame.get("bytes") is not None:
            got.append(len(frame["bytes"]))
            continue
        message = json.loads(frame["text"])
        got.append(message)
        if message["type"] == kind:
            return got


def signed_in(ws: WebSocketTestSession) -> None:
    ws.send_text(hello())
    frames_until(ws, "audio_end")  # the sign-in prompt, spoken
    send(ws, type="login", reg_no="10000001AA", exam_code="UTME-SAMPLE")
    frames_until(ws, "state")  # replies are spoken in order, so the welcome's audio came first


def test_each_reply_is_followed_by_its_audio(client: TestClient, voice: StandInVoice) -> None:
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        prompt = frames_until(ws, "audio_end")
        assert prompt[0]["type"] == "say"  # type: ignore[index]
        assert prompt[-1] == {"type": "audio_end", "utterance_id": prompt[0]["utterance_id"]}  # type: ignore[index]
        assert all(isinstance(f, int) and 0 < f <= AUDIO_OUT_FRAME_BYTES for f in prompt[1:-1])
        assert sum(prompt[1:-1]) == 2000 * sum(len(s) for s, _, _ in voice.calls)  # type: ignore[arg-type]


def test_first_sentence_first_and_at_the_candidates_speed(
    client: TestClient, voice: StandInVoice
) -> None:
    with client.websocket_connect("/v1/session") as ws:
        signed_in(ws)
        voice.calls.clear()
        send(ws, type="text", text="faster")
        frames_until(ws, "audio_end")
        send(ws, type="text", text="start")
        for _ in range(3):  # question, options, prompt
            frames_until(ws, "audio_end")
    speeds = {speed for _, speed, _ in voice.calls}
    assert speeds == {1.5}  # "faster" applied to everything after it
    question = next(c for c in voice.calls if c[0].startswith("Question 1 of 60."))
    assert question[2] is Priority.FIRST_SENTENCE
    assert any(p is Priority.REST_OF_REPLY for _, _, p in voice.calls)


def test_talk_key_cuts_speech_and_drops_queued_replies(
    client: TestClient, voice: StandInVoice
) -> None:
    with client.websocket_connect("/v1/session") as ws:
        signed_in(ws)
        voice.hold = asyncio.Event()  # never released: speech is "in progress"
        send(ws, type="text", text="start")
        frames_until(ws, "say")  # the question's say; its audio is held
        voice.calls.clear()

        send(ws, type="talk_start")
        stopped = frames_until(ws, "stop")
        assert stopped[-1]["type"] == "stop"  # type: ignore[index]
        voice.hold = None
        for frame in as_audio("where am I"):
            ws.send_bytes(frame)
        send(ws, type="talk_end")
        after = frames_until(ws, "audio_end")
        says = [f["text"] for f in after if isinstance(f, dict) and f["type"] == "say"]
        # The rest of the question (options, prompt) was dropped; only the new answer plays.
        assert says == [
            "You are on question 1 of 60, in Use of English. Question 1 is not answered."
        ]
        assert any(isinstance(f, dict) and f["type"] == "state" for f in after)


def test_busy_voice_still_sends_the_text(client: TestClient, voice: StandInVoice) -> None:
    with client.websocket_connect("/v1/session") as ws:
        signed_in(ws)
        voice.busy = True
        send(ws, type="text", text="where am I")
        got = frames_until(ws, "audio_end")
        kinds = [f["type"] for f in got if isinstance(f, dict)]
        assert kinds[:3] == ["heard", "say", "error"]
        assert not any(isinstance(f, int) for f in got)
