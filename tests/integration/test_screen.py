"""The exam screen over the real WebSocket and HTTP endpoints, against the exam store:
sign in by examination number, check identity, answer by clicking, revisit, submit."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from tests.integration.test_api import TOKEN, hello, make_settings, next_message, send, turn
from tests.integration.test_api_speech import StandInVoice
from voice_agent.examstore.__main__ import sample_package
from voice_agent.examstore.adapter import LocalAdapter
from voice_agent.examstore.package import read_package, write_package
from voice_agent.examstore.store import ExamStore
from voice_agent.main import create_app

EXAM_NUMBER = "20260001PR"
CANDIDATE = "practice-01"
PHOTO = b"\xff\xd8\xff\xe0" + b"a stand-in jpeg" + b"\xff\xd9"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ExamStore]:
    store = ExamStore(tmp_path / "exams.db")
    package = write_package(sample_package(), photos={CANDIDATE: PHOTO})
    store.import_package(read_package(package))
    store.set_open("exam-sample-60", True)
    yield store
    store.close()


@pytest.fixture
def voice() -> StandInVoice:
    return StandInVoice()


@pytest.fixture
def client(tmp_path: Path, store: ExamStore, voice: StandInVoice) -> Iterator[TestClient]:
    app = create_app(make_settings(tmp_path), adapter=LocalAdapter(store), synthesizer=voice)
    with TestClient(app) as c:
        yield c


def sign_in(ws: WebSocketTestSession) -> tuple[str, dict[str, Any]]:
    """Standard mode: the screen reads the exam itself, so no speech audio is made."""
    ws.send_text(hello(audio=False))
    assert next_message(ws)["type"] == "say"
    send(ws, type="login", reg_no=EXAM_NUMBER)  # no exam code: the open papers are searched
    session = next_message(ws)
    assert session["type"] == "session"
    details = next_message(ws)
    assert details["type"] == "details"
    turn(ws)  # the welcome
    return session["session_id"], details


def act(ws: WebSocketTestSession, **fields: Any) -> list[dict[str, Any]]:
    send(ws, type="act", **fields)
    return turn(ws)


def of_type(messages: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [m for m in messages if m["type"] == kind]


def test_an_exam_on_the_screen(client: TestClient, store: ExamStore, voice: StandInVoice) -> None:
    with client.websocket_connect("/v1/session") as ws:
        session_id, details = sign_in(ws)
        # The identity check: the candidate's own details, and their photo by HTTP.
        assert details["exam_number"] == EXAM_NUMBER
        assert details["full_name"] and details["photo"] is True
        assert details["audio_mode"] is False
        assert details["question_count"] == 60 and len(details["sections"]) == 4

        started = act(ws, action="go_to", question=1)
        assert [m["type"] for m in started][:2] == ["question", "say"]  # shown, then read
        view = started[0]
        version = store.candidate(CANDIDATE).version
        paper = store.paper_question("exam-sample-60", version.paper_number(1))
        assert view["text"] == paper.text  # verbatim, the candidate's own question 1
        assert view["section_title"] == details["sections"][0]["title"]
        assert len(view["options"]) == len(paper.options)
        assert started[-1]["started"] is True

        state = act(ws, action="select", question=1, choice="B")[-1]
        assert state["current_answer"] == "B" and state["answered_questions"] == [1]
        assert state["question"] == 1  # a click does not move on
        assert act(ws, action="flag")[-1]["flagged"] == [1]

        moved = act(ws, action="next")
        assert of_type(moved, "question")[0]["number"] == 2
        assert moved[-1]["current_answer"] is None

        assert act(ws, action="submit")[-1]["pending_confirm"]["final"] is False
        assert act(ws, action="confirm")[-1]["pending_confirm"]["final"] is True
        assert act(ws, action="confirm")[-1]["ended"] == "submitted"

    # Saved against the paper's own numbering, as every answer is.
    assert store.answers(CANDIDATE) == {version.paper_number(1): version.paper_choice(1, "B")}
    assert voice.calls == []  # replies came as text only

    auth = {"Authorization": f"Bearer {TOKEN}"}
    photo = client.get(f"/v1/sessions/{session_id}/photo", headers=auth)
    assert photo.status_code == 200 and photo.content == PHOTO
    assert photo.headers["content-type"] == "image/jpeg"
    assert photo.headers["cache-control"] == "no-store"


def test_the_photo_needs_the_device_token(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        session_id, _ = sign_in(ws)
    url = f"/v1/sessions/{session_id}/photo"
    assert client.get(url).status_code == 401
    wrong = {"Authorization": "Bearer wrong-token-000000"}
    assert client.get(url, headers=wrong).status_code == 401
    unknown = client.get("/v1/sessions/nope/photo", headers={"Authorization": f"Bearer {TOKEN}"})
    assert unknown.status_code == 404


def test_a_resumed_screen_is_shown_the_question_again(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        session_id, _ = sign_in(ws)
        act(ws, action="go_to", question=5)
        act(ws, action="flag", question=9)
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello(audio=False, resume_session_id=session_id))
        assert next_message(ws)["type"] == "session"
        assert next_message(ws)["type"] == "details"
        messages = turn(ws)
        assert of_type(messages, "question")[0]["number"] == 5
        assert messages[-1]["flagged"] == [9]


def test_a_click_before_sign_in_is_refused(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello(audio=False))
        next_message(ws)
        send(ws, type="act", action="next")
        error = next_message(ws)
        assert error["type"] == "error" and error["code"] == "not_identified"


def test_spoken_mode_still_gets_the_question_view(client: TestClient, voice: StandInVoice) -> None:
    """A partially sighted candidate in audio mode may still glance at the screen."""
    with client.websocket_connect("/v1/session") as ws:
        ws.send_text(hello())
        send(ws, type="login", reg_no="20260007PR")
        seen: list[dict[str, Any]] = []
        while not any(m.get("type") == "state" for m in seen):
            frame = ws.receive()
            if frame.get("text"):
                seen.append(json.loads(frame["text"]))
        details = of_type(seen, "details")[0]
        assert details["audio_mode"] is True and details["photo"] is False
        send(ws, type="text", text="start")
        while (message := next_message(ws))["type"] != "question":
            pass
        assert message["number"] == 1
    assert voice.calls  # this one is spoken


def test_the_built_exam_app_is_served(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from voice_agent.main import _mount_exam_app

    (tmp_path / "index.html").write_text("<title>ExamSolution</title>", encoding="utf-8")
    app = FastAPI()
    _mount_exam_app(app, tmp_path)
    page = TestClient(app).get("/exam/")
    assert page.status_code == 200 and "ExamSolution" in page.text


def test_an_unbuilt_exam_app_says_how_to_build_it(tmp_path: Path) -> None:
    from fastapi import FastAPI

    from voice_agent.main import _mount_exam_app

    app = FastAPI()
    _mount_exam_app(app, tmp_path / "missing")
    page = TestClient(app).get("/exam/")
    assert page.status_code == 503 and "npm run build" in page.text
