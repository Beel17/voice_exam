"""The real voice (Piper via sherpa-onnx). Skipped where the models are not present."""

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from voice_agent.config import SpeechSettings
from voice_agent.contract import AUDIO_OUT_SAMPLE_RATE
from voice_agent.runtime.pools import Priority
from voice_agent.selfcheck import file_problems
from voice_agent.speech.tts import SherpaSynthesizer, fixed_sentences

VOICES = Path("models/speech/tts")
DEFAULT = SpeechSettings().voice_dir
pytestmark = pytest.mark.skipif(not DEFAULT.is_dir(), reason="voices not fetched")
shared_loop = pytest.mark.asyncio(loop_scope="module")


def seconds(pcm: bytes) -> float:
    return len(pcm) / 2 / AUDIO_OUT_SAMPLE_RATE


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def voice() -> AsyncIterator[SherpaSynthesizer]:
    v = SherpaSynthesizer(DEFAULT, workers=1, max_queue=16, start_within_s=30)
    await v.start()
    yield v
    await v.stop()


@pytest.mark.parametrize(
    "folder", sorted(p.name for p in VOICES.iterdir()) if VOICES.is_dir() else []
)
def test_every_shipped_voice_matches_the_manifest(folder: str) -> None:
    files = [p for p in (VOICES / folder).rglob("*") if p.is_file()]
    assert files and file_problems(Path("models"), files) == []


@shared_loop
async def test_fixed_prompts_are_ready_at_once(voice: SherpaSynthesizer) -> None:
    began = time.perf_counter()
    for sentence in fixed_sentences():
        await voice.synthesize(sentence, 1.0, Priority.FIRST_SENTENCE)
    assert time.perf_counter() - began < 0.05  # all rendered at start-up


@shared_loop
async def test_speech_is_real_and_cached(voice: SherpaSynthesizer) -> None:
    began = time.perf_counter()
    pcm = await voice.synthesize("Question 12 of 60.", 1.0, Priority.FIRST_SENTENCE)
    made = time.perf_counter() - began
    assert 0.8 < seconds(pcm) < 3.0
    began = time.perf_counter()
    assert await voice.synthesize("Question 12 of 60.", 1.0, Priority.FIRST_SENTENCE) == pcm
    assert time.perf_counter() - began < made / 10


@shared_loop
async def test_speed_changes_the_length(voice: SherpaSynthesizer) -> None:
    sentence = "Option C: the movement of water across a membrane."
    slow = await voice.synthesize(sentence, 0.8, Priority.FIRST_SENTENCE)
    normal = await voice.synthesize(sentence, 1.0, Priority.FIRST_SENTENCE)
    fast = await voice.synthesize(sentence, 2.5, Priority.FIRST_SENTENCE)
    # Piper's own setting reached only 1.7x at "2.5"; the tempo change gives the rate asked
    # for (exactly: see test_tempo.py). The tolerance here is the voice's own variation:
    # it picks phoneme lengths with some randomness, so two renderings differ by up to ~10%.
    assert seconds(slow) == pytest.approx(seconds(normal) / 0.8, rel=0.15)
    assert seconds(fast) == pytest.approx(seconds(normal) / 2.5, rel=0.15)


@shared_loop
async def test_symbols_are_spoken_not_skipped(voice: SherpaSynthesizer) -> None:
    with_symbols = await voice.synthesize("Simplify 2³ × 2².", 1.0, Priority.FIRST_SENTENCE)
    in_words = await voice.synthesize(
        "Simplify 2 cubed times 2 squared.", 1.0, Priority.FIRST_SENTENCE
    )
    assert abs(seconds(with_symbols) - seconds(in_words)) < 0.15
