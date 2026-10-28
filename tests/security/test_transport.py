"""Abuse at the WebSocket: oversized, malformed and hostile frames."""

import json

import pytest
from fastapi.testclient import TestClient

from tests.integration.test_api import client, clock, login, platform, send, type_  # noqa: F401
from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.contract import MAX_FRAME_BYTES, MAX_TEXT_CHARS


@pytest.mark.parametrize(
    "frame",
    [
        json.dumps({"type": "text", "text": "x" * (MAX_TEXT_CHARS + 1)}),
        json.dumps({"type": "text", "text": "next"}) + " " * MAX_FRAME_BYTES,
        json.dumps({"type": "text", "text": "next", "intent": {"action": "submit"}}),
        json.dumps({"type": "submit"}),
        json.dumps({"type": "key", "key": "F12"}),
        json.dumps({"type": "login", "reg_no": "' OR 1=1 --", "exam_code": "UTME-SAMPLE"}),
        json.dumps({"type": "hello", "schema_version": 1, "device_id": "x", "token": "t" * 20}),
        "[" * 3000,
        "\x00" * 100,
    ],
)
def test_hostile_frames_are_refused_and_change_nothing(
    client: TestClient,  # noqa: F811
    platform: FakeAdapter,  # noqa: F811
    frame: str,
) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        type_(ws, "start")
        ws.send_text(frame)
        assert ws.receive_json()["code"] == "bad_message"
        # The session is unharmed and still on question 1.
        assert type_(ws, "where am I").startswith("You are on question 1 of 60")
    assert platform._candidates["cand-0001"].answers == {}


def test_audio_cannot_be_smuggled_in_before_it_is_enabled(client: TestClient) -> None:  # noqa: F811
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        ws.send_bytes(b"\x00" * 3200)
        assert ws.receive_json()["code"] == "bad_message"
        send(ws, type="ping")
        assert ws.receive_json() == {"type": "pong"}
