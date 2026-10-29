"""The real recogniser (Kroko via sherpa-onnx). Skipped where the models are not present.

Prepare them with ``uv run python -m tools.fetch_models``.
"""

import wave
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from tests.integration.test_api import login, make_settings, send, spoken, turn
from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.config import SpeechSettings
from voice_agent.contract import AUDIO_SAMPLE_RATE
from voice_agent.main import create_app
from voice_agent.selfcheck import file_problems
from voice_agent.speech.stt import MODEL_FILES, SherpaRecognizer

ASR = SpeechSettings().recognizer_dir
VOICE = Path("models/speech/tts/vits-piper-en_US-lessac-medium")
pytestmark = pytest.mark.skipif(not ASR.is_dir(), reason="speech models not fetched")
# The async tests share one event loop, so they can share one loaded recogniser.
shared_loop = pytest.mark.asyncio(loop_scope="module")


def to_pcm(samples: np.ndarray, rate: int) -> bytes:
    """Float audio at any rate to 16 kHz 16-bit PCM, as the PC sends it."""
    if rate != AUDIO_SAMPLE_RATE:
        n = int(len(samples) * AUDIO_SAMPLE_RATE / rate)
        samples = np.interp(np.linspace(0, len(samples) - 1, n), np.arange(len(samples)), samples)
    return bytes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def bundled_sample() -> bytes:
    with wave.open(str(ASR / "test_wavs" / "0.wav")) as w:
        raw = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
        return to_pcm(raw, w.getframerate())


def spoken_by_piper(sentence: str) -> bytes:
    import sherpa_onnx

    onnx = next(VOICE.glob("*.onnx"))
    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(onnx), tokens=str(VOICE / "tokens.txt"), data_dir=str(VOICE / "espeak-ng-data")
    )
    tts = sherpa_onnx.OfflineTts(
        sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=1)
        )
    )
    audio = tts.generate(sentence, sid=0, speed=1.0)
    silence = np.zeros(int(0.3 * audio.sample_rate), np.float32)  # the key goes down first
    return to_pcm(
        np.concatenate([silence, np.asarray(audio.samples, np.float32)]), audio.sample_rate
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def recognizer() -> AsyncIterator[SherpaRecognizer]:
    """Loaded once for the module: loading the model takes several seconds."""
    r = SherpaRecognizer(ASR, workers=1, max_queue=4, start_within_s=10)
    await r.start()
    yield r
    await r.stop()


def test_the_shipped_recogniser_matches_the_manifest() -> None:
    assert file_problems(Path("models"), [ASR / name for name in MODEL_FILES]) == []


@shared_loop
async def test_recognises_real_speech_to_the_last_word(recognizer: SherpaRecognizer) -> None:
    heard = await recognizer.recognize(bundled_sample())
    text = heard.text.lower()
    assert "ask not what your country can do for you" in text
    assert text.rstrip(".").endswith("your country")  # the last word survives
    assert 0 < heard.confidence <= 1


@shared_loop
async def test_silence_is_heard_as_nothing(recognizer: SherpaRecognizer) -> None:
    heard = await recognizer.recognize(bytes(AUDIO_SAMPLE_RATE * 2))
    assert heard.text == "" and heard.confidence == 0


@shared_loop
async def test_an_empty_press_is_heard_as_nothing(recognizer: SherpaRecognizer) -> None:
    assert (await recognizer.recognize(b"")).text == ""


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = make_settings(tmp_path, models_dir=Path("models"), speech=SpeechSettings())
    with TestClient(create_app(settings, adapter=FakeAdapter.with_bundled_fixtures())) as c:
        yield c


@pytest.mark.skipif(not VOICE.is_dir(), reason="voice not fetched")
def test_a_spoken_command_moves_the_exam(client: TestClient) -> None:
    assert client.get("/readyz").json()["speech"] == "running"
    audio = spoken_by_piper("go to question twelve")
    with client.websocket_connect("/v1/session") as ws:
        login(ws)
        send(ws, type="talk_start")
        for k in range(0, len(audio), 3200):
            ws.send_bytes(audio[k : k + 3200])
        send(ws, type="talk_end")
        messages = [m for m in turn(ws) if m["type"] != "stop"]
    assert messages[0]["type"] == "heard"
    assert messages[0]["text"].lower().rstrip(".") == "go to question twelve"
    assert spoken(messages).startswith("Question 12 of 60.")
