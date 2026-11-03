"""Audio mode before sign-in: the guided checks, and signing in by spelling the
examination number aloud, against the exam store."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from tests.integration.test_api import hello, make_settings, send
from tests.integration.test_api import next_message as next_any
from tests.integration.test_api_audio import SentenceRecognizer, as_audio
from voice_agent.examstore.__main__ import sample_package
from voice_agent.examstore.adapter import LocalAdapter
from voice_agent.examstore.package import read_package, write_package
from voice_agent.examstore.store import ExamStore
from voice_agent.main import create_app
from voice_agent.understanding.spelling import read_back, spoken_code


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ExamStore(tmp_path / "exams.db")
    store.import_package(read_package(write_package(sample_package())))
    store.set_open("exam-sample-60", True)
    app = create_app(
        make_settings(tmp_path), adapter=LocalAdapter(store), recognizer=SentenceRecognizer()
    )
    with TestClient(app) as c:
        yield c
    store.close()


def next_message(ws: WebSocketTestSession) -> dict[str, Any]:
    """The next message, past barge-in stops (pressing the talk key silences replies)."""
    while (message := next_any(ws))["type"] == "stop":
        pass
    return message


def talk(ws: WebSocketTestSession, sentence: str) -> tuple[str, str]:
    """Say something before sign-in: what was heard, and what the service said back."""
    send(ws, type="talk_start")
    for frame in as_audio(sentence):
        ws.send_bytes(frame)
    send(ws, type="talk_end")
    heard = next_message(ws)
    assert heard["type"] == "heard"
    return heard["text"], next_message(ws)["text"]


def opened(ws: WebSocketTestSession) -> None:
    ws.send_text(hello())
    assert next_message(ws)["type"] == "say"  # the sign-in prompt


def guide(ws: WebSocketTestSession, step: str) -> dict[str, Any]:
    send(ws, type="guide", step=step)
    return next_message(ws)


def test_the_guided_checks_are_spoken(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        opened(ws)
        assert "hold the Space bar" in guide(ws, "welcome")["text"]
        assert "left ear" in guide(ws, "left")["text"]
        assert "right ear" in guide(ws, "right")["text"]
        assert guide(ws, "mic_check")["text"].startswith("Now your microphone")
        assert talk(ws, "testing one two three")[1].startswith("I heard: testing one two three.")
        assert "did not hear anything" in talk(ws, "")[1]


def test_signing_in_by_spelling_the_number(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        opened(ws)
        assert "say your examination number" in guide(ws, "sign_in")["text"]
        _, read = talk(ws, "two zero two six zero zero zero one papa romeo")
        assert read.startswith("I heard: 2, 0, 2, 6, 0, 0, 0, 1, P for Papa, R for Romeo.")
        send(ws, type="talk_start")  # nothing is signed in until "yes"
        for frame in as_audio("yes"):
            ws.send_bytes(frame)
        send(ws, type="talk_end")
        assert next_message(ws)["type"] == "heard"
        signed = next_message(ws)
        assert signed["text"].startswith("You are signed in as Ada Okafor.")
        assert next_message(ws)["type"] == "session"
        details = next_message(ws)
        assert details["exam_number"] == "20260001PR"


def test_a_misheard_number_is_corrected_before_anything_happens(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        opened(ws)
        guide(ws, "sign_in")
        assert "did not catch a number" in talk(ws, "good morning")[1]
        assert "9, 9, 9, 9" in talk(ws, "nine nine nine nine")[1]
        assert "say your examination number" in talk(ws, "no")[1]
        # A number never registered is refused, and the candidate can try again.
        talk(ws, "nine nine nine nine")
        send(ws, type="key", key="Enter")
        assert "not registered" in next_message(ws)["text"]
        talk(ws, "2026 0002 PR")
        send(ws, type="key", key="Enter")
        assert next_message(ws)["text"].startswith("You are signed in as")
        assert next_message(ws)["type"] == "session"


def test_talking_before_sign_in_needs_a_guide_step(client: TestClient) -> None:
    with client.websocket_connect("/v1/session") as ws:
        opened(ws)
        send(ws, type="talk_start")
        assert next_message(ws)["code"] == "not_identified"


@pytest.mark.parametrize(
    ("said", "code"),
    [
        ("TWO ZERO TWO SIX ZERO ZERO ZERO ONE PAPA ROMEO", "20260001PR"),
        ("2026 0001 P for Papa, R for Romeo", "20260001PR"),
        ("my number is twenty twenty six double oh oh one pee are", "20260001PR"),
        ("two oh two six triple zero one p r", "20260001PR"),
        ("2 0 2 6 0 0 0 1 PR", "20260001PR"),
        ("X-ray yankee one two three", "XY123"),
    ],
)
def test_spelled_numbers(said: str, code: str) -> None:
    assert spoken_code(said) == code


@pytest.mark.parametrize("said", ["", "hello there", "one two", "one hundred and five", "yes"])
def test_what_is_not_a_number(said: str) -> None:
    assert spoken_code(said) is None


def test_read_back_spells_letters_as_words() -> None:
    assert read_back("26PR") == "2, 6, P for Papa, R for Romeo"
