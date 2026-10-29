"""The service over its real WebSocket and HTTP endpoints, against the fake platform."""

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession
from starlette.websockets import WebSocketDisconnect

from voice_agent.adapters.fake_adapter import FakeAdapter, load_fixture
from voice_agent.api.runtime import Runtime
from voice_agent.config import LimitSettings, LlmSettings, Settings, SpeechSettings
from voice_agent.contract import SCHEMA_VERSION
from voice_agent.main import create_app

TOKEN = "device-token-for-tests-0001"
ADMIN = "admin-token-for-tests-0001"
REG_NO, EXAM = "10000001AA", "UTME-SAMPLE"
CANDIDATE = "cand-0001"


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def platform(clock: Clock) -> FakeAdapter:
    return FakeAdapter.with_bundled_fixtures(clock)


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "data_dir": tmp_path,
        "device_token_sha256": (digest(TOKEN),),
        "admin_token_sha256": (digest(ADMIN),),
        "llm": LlmSettings(enabled=False),
        # Tests inject a stand-in recogniser where they need one; none loads the real model.
        "speech": SpeechSettings(enabled=False),
    }
    return Settings(**(values | overrides))


@pytest.fixture
def client(tmp_path: Path, platform: FakeAdapter) -> Iterator[TestClient]:
    with TestClient(create_app(make_settings(tmp_path), adapter=platform)) as c:
        yield c


def hello(**fields: Any) -> str:
    base = {"type": "hello", "schema_version": SCHEMA_VERSION, "device_id": "pc-07", "token": TOKEN}
    return json.dumps(base | fields)


def send(ws: WebSocketTestSession, **message: Any) -> None:
    ws.send_text(json.dumps(message))


def turn(ws: WebSocketTestSession) -> list[dict[str, Any]]:
    """Messages up to and including the next ``state``: one turn's worth. Audio frames
    (when a voice is in use) are left out; tests about audio look at them directly."""
    messages = []
    while True:
        message = next_message(ws)
        messages.append(message)
        if message["type"] == "state":
            return messages


def spoken(messages: list[dict[str, Any]]) -> str:
    return " ".join(m["text"] for m in messages if m["type"] == "say")


def next_message(ws: WebSocketTestSession) -> dict[str, Any]:
    """The next JSON message, past any speech (audio frames and ``audio_end``)."""
    while True:
        frame = ws.receive()
        if frame.get("text") is not None:
            message: dict[str, Any] = json.loads(frame["text"])
            if message["type"] != "audio_end":
                return message


def login(ws: WebSocketTestSession) -> tuple[str, str]:
    ws.send_text(hello())
    assert next_message(ws)["type"] == "say"  # sign-in prompt
    send(ws, type="login", reg_no=REG_NO, exam_code=EXAM)
    info = next_message(ws)
    assert info["type"] == "session"
    return info["session_id"], spoken(turn(ws))


def type_(ws: WebSocketTestSession, text: str) -> str:
    send(ws, type="text", text=text)
    return spoken(turn(ws))


def runtime(client: TestClient) -> Runtime:
    rt: Runtime = client.app.state.runtime  # type: ignore[attr-defined]
    return rt


# --- Health -----------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {
        "status": "ok",
        "language_model": "disabled",
        "speech": "disabled",
        "voice": "disabled",
    }


def test_missing_model_files_mean_rules_only(tmp_path: Path, platform: FakeAdapter) -> None:
    settings = make_settings(tmp_path, llm=LlmSettings(model_file=tmp_path / "missing.gguf"))
    with TestClient(create_app(settings, adapter=platform)) as c:
        assert c.get("/readyz").json() == {
            "status": "degraded",
            "language_model": "down",
            "speech": "disabled",
            "voice": "disabled",
        }
        with c.websocket_connect("/v1/session") as ws:
            login(ws)
            assert type_(ws, "start").startswith("Question 1 of 60.")


def test_cue_sounds_are_served_for_the_pc_to_keep(client: TestClient) -> None:
    for name in ("listening", "saved", "not_understood", "time_warning"):
        response = client.get(f"/v1/cues/{name}.wav")
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.content[:4] == b"RIFF"
    assert client.get("/v1/cues/../config.wav").status_code == 404
    assert client.get("/v1/cues/applause.wav").status_code == 404


def test_no_interactive_docs_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


# --- Connecting -------------------------------------------------------------------------


def closed_with(ws: WebSocketTestSession) -> tuple[str, int]:
    error = ws.receive_json()
    with pytest.raises(WebSocketDisconnect) as info:
        ws.receive_json()
    return error["code"], info.value.code


@pytest.mark.parametrize(
    ("first", "code", "close"),
    [
        (json.dumps({"type": "ping"}), "bad_message", 4400),
        ("not json", "bad_message", 4400),
        (hello(schema_version=99), "unsupported_schema", 4400),
        (hello(token="wrong-token-0000000000"), "unauthorized", 4401),
    ],
)
def test_bad_openings_are_refused(client: TestClient, first: str, code: str, close: int) -> None:
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(first)
        assert closed_with(ws) == (code, close)


def test_with_no_device_tokens_configured_nobody_gets_in(
    tmp_path: Path, platform: FakeAdapter
) -> None:
    settings = make_settings(tmp_path, device_token_sha256=())
    with (
        TestClient(create_app(settings, adapter=platform)) as c,
        c.websocket_connect("/v1/session") as ws,
    ):
        ws.send_text(hello())
        assert closed_with(ws) == ("unauthorized", 4401)


def test_sign_in(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        assert "invigilator signs you in" in ws.receive_json()["text"]
        send(ws, type="text", text="next")
        assert ws.receive_json()["code"] == "not_identified"
        send(ws, type="login", reg_no="99999999ZZ", exam_code=EXAM)
        assert "not registered" in ws.receive_json()["text"]
        send(ws, type="login", reg_no=REG_NO, exam_code=EXAM)
        info = ws.receive_json()
        assert info == {
            "type": "session",
            "session_id": info["session_id"],
            "schema_version": 1,
            "resumed": False,
        }
        messages = turn(ws)
        assert spoken(messages).startswith("Welcome. This exam has 60 questions")
        assert messages[-1]["of"] == 60


def test_sign_in_while_the_platform_is_down(client: TestClient, platform: FakeAdapter) -> None:
    platform.unavailable = True
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        ws.receive_json()
        send(ws, type="login", reg_no=REG_NO, exam_code=EXAM)
        assert ws.receive_json()["code"] == "adapter_unavailable"
        assert "cannot be reached" in ws.receive_json()["text"]


# --- Taking the exam --------------------------------------------------------------------


def test_typed_exam_turns(client: TestClient, platform: FakeAdapter) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        send(ws, type="text", text="Start from question 50")
        messages = turn(ws)
        assert messages[0] == {
            "type": "heard",
            "text": "Start from question 50",
            "confidence": 1.0,
            "seq": 2,
        }
        assert spoken(messages).startswith("Question 50 of 60.")
        assert "you chose option D" in type_(ws, "the one that says photosynthesis")
        assert type_(ws, "yes").startswith("Saved. Question 51 of 60.")
        send(ws, type="key", key="1")
        assert "you chose option A" in spoken(turn(ws))
        send(ws, type="key", key="Esc")
        assert spoken(turn(ws)).startswith("Not saved.")
        send(ws, type="ping")
        assert ws.receive_json() == {"type": "pong"}
    assert platform._candidates[CANDIDATE].answers == {50: "D"}


def test_bad_frames_are_reported_and_the_session_carries_on(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        ws.send_text("{not json")
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_json()["code"] == "bad_message"
        send(ws, type="login", reg_no=REG_NO, exam_code=EXAM)  # already signed in
        assert ws.receive_json()["code"] == "bad_message"
        send(ws, type="talk_start")  # accepted, nothing to do without audio
        assert type_(ws, "start").startswith("Question 1 of 60.")


def test_flooding_is_rate_limited(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        login(ws)  # the login message counts too
        for _ in range(65):
            send(ws, type="ping")
        replies = [ws.receive_json() for _ in range(65)]
        assert [r["type"] for r in replies].count("pong") == 59
        assert {r.get("code") for r in replies if r["type"] == "error"} == {"rate_limited"}


def test_speech_from_background_work_reaches_the_pc(
    client: TestClient, platform: FakeAdapter, clock: Clock
) -> None:
    rt = runtime(client)
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        type_(ws, "start")
        clock.now += 3600 - 600
        client.portal.call(rt.manager.check_time)  # type: ignore[union-attr]
        assert spoken(turn(ws)) == "15 minutes left."


# --- Reconnecting -----------------------------------------------------------------------


def test_reconnect_takes_over_the_session(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as old:
        session_id, _ = login(old)
        type_(old, "go to question 14")
        with client.websocket_connect("/v1/session") as new:
            new.send_text(hello(resume_session_id=session_id, last_seq=3))
            assert new.receive_json() == {
                "type": "session",
                "session_id": session_id,
                "schema_version": 1,
                "resumed": True,
            }
            assert spoken(turn(new)).startswith("Welcome back. You were on question 14 of 60.")
            assert closed_with(old) == ("superseded", 4409)
            assert type_(new, "next").startswith("Question 15 of 60.")


def test_resume_of_an_unknown_session(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello(resume_session_id="no-such-session"))
        assert ws.receive_json()["code"] == "session_not_found"
        assert "signs you in" in ws.receive_json()["text"]


# --- Admin endpoints --------------------------------------------------------------------


def test_status_and_audit_need_the_admin_token(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        session_id, _ = login(ws)
        type_(ws, "start")
        type_(ws, "B")
        type_(ws, "yes")
        assert client.get("/v1/status").status_code == 401
        device = {"Authorization": f"Bearer {TOKEN}"}
        assert client.get("/v1/status", headers=device).status_code == 401  # devices are not admins
        admin = {"Authorization": f"Bearer {ADMIN}"}
        status = client.get("/v1/status", headers=admin).json()
        assert status.pop("process_id") == os.getpid()
        assert status == {
            "mode": "pc",
            "live_sessions": 1,
            "connected": 1,
            "unsynced_writes": 0,
            "language_model": "disabled",
            "language_model_restarts": 0,
            "speech": "disabled",
            "voice": "disabled",
            "degradation_level": 1,
        }
        audit = client.get(f"/v1/sessions/{session_id}/audit", headers=admin).json()
    kinds = [e["kind"] for e in audit]
    assert kinds[0] == "started" and "write_outcome" in kinds
    heard = [e["event"]["text"] for e in audit if e["kind"] == "understood"]
    assert heard == ["start", "B", "yes"]
    exam = load_fixture("sample_60")
    assert exam.questions[0].text not in json.dumps(audit)
    assert client.get("/v1/sessions/nope/audit", headers=admin).status_code == 404


# --- Phase 1 exit: a full exam by typed sentences, with a forced restart ----------------

SAY_ANSWER = [
    "{}",
    "option {}",
    "I'll go with {}",
    "my answer is {}",
    "I think it's {}",
]


def test_full_exam_by_typed_sentences_with_a_forced_restart(
    tmp_path: Path, platform: FakeAdapter
) -> None:
    exam = load_fixture("sample_60")
    # A script types far faster than a person; the flood limit is tested separately.
    settings = make_settings(tmp_path, limits=LimitSettings(max_messages_per_10s=10_000))

    def answer_up_to(ws: WebSocketTestSession, first: int, last: int) -> None:
        type_(ws, f"take me to question {first}")
        for n in range(first, last + 1):
            letter = "ABCDE"[n % len(exam.questions[n - 1].options)]
            confirm = type_(ws, SAY_ANSWER[n % len(SAY_ANSWER)].format(letter))
            assert f"you chose option {letter}" in confirm, (n, confirm)
            send(ws, type="text", text="yes")
            saved = turn(ws)
            # Every save sounds the cue; "Saved." is spoken only the first few times.
            assert {"type": "cue", "name": "saved", "volume": 0.75} in saved, n

    with (
        TestClient(create_app(settings, adapter=platform)) as first,
        first.websocket_connect("/v1/session") as ws,
    ):
        session_id, _ = login(ws)
        answer_up_to(ws, 1, 30)
        type_(ws, "C")  # chosen but never confirmed when the service goes down

    # The whole service restarts: new process state, same database, same platform.
    with (
        TestClient(create_app(settings, adapter=platform)) as second,
        second.websocket_connect("/v1/session") as ws,
    ):
        ws.send_text(hello(resume_session_id=session_id))
        assert ws.receive_json()["resumed"] is True
        assert spoken(turn(ws)).startswith("Welcome back. You were on question 31 of 60.")
        answer_up_to(ws, 31, 60)
        assert type_(ws, "which questions have I not answered") == (
            "You have answered every question."
        )
        assert type_(ws, "I'm done").startswith("You have answered 60.")
        assert type_(ws, "yes").startswith("Submitting is final.")
        assert type_(ws, "yes") == "Your exam has been submitted. Thank you."

    answers = platform._candidates[CANDIDATE].answers
    assert len(answers) == 60
    assert platform._candidates[CANDIDATE].submitted
