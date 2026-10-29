"""Speech recognition.

Audio arrives while the talk key is held and is recognised when it is released, by a
streaming zipformer (Kroko, English) through sherpa-onnx, in a worker pool: each worker
holds its own recogniser on one thread. The API depends only on the ``Recognizer``
protocol.

Measured in phase 2 (synthetic speech, one thread): about 0.26 s from release to text.
Hotword boosting did not change results with this model, and its token probabilities
do not separate right from wrong (correct single letters scored as low as 0.16), so
confidence is recorded for phase 4 to calibrate on real recordings but is not acted on.
Every answer is read back and needs an explicit yes, which is the real safeguard.
"""

import math
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Final, Protocol

import numpy as np
import sherpa_onnx

from voice_agent.contract import AUDIO_SAMPLE_RATE
from voice_agent.runtime.pools import PoolStats, Priority, WorkerPool

# Kroko only finishes the last word once it has seen about 2 s more audio (measured: with
# 1 s it dropped final words). Silence is appended at once, so it costs compute, not time.
TAIL_S: Final = 2.0
MODEL_FILES: Final = ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")


@dataclass(frozen=True, slots=True)
class Recognition:
    text: str
    confidence: float  # 0..1; recorded, not yet acted on (see the module docstring)


class Recognizer(Protocol):
    async def recognize(self, pcm: bytes) -> Recognition:
        """Recognise 16 kHz mono 16-bit little-endian PCM. May raise ``PoolBusy``."""
        ...


class SherpaRecognizer:
    def __init__(
        self, model_dir: Path, *, workers: int, max_queue: int, start_within_s: float
    ) -> None:
        self._model_dir = model_dir
        self._start_within_s = start_within_s
        self._pool: WorkerPool[sherpa_onnx.OnlineRecognizer] = WorkerPool(
            "recognition", workers, self._load, max_queue=max_queue
        )

    async def start(self) -> None:
        """Load a recogniser per worker and run each once, so the first candidate does not
        pay for the model's first-use setup."""
        await self._pool.start()
        for _ in range(self._pool.stats().workers):
            await self.recognize(bytes(AUDIO_SAMPLE_RATE))  # 0.5 s of silence

    async def stop(self) -> None:
        await self._pool.stop()

    def stats(self) -> PoolStats:
        return self._pool.stats()

    async def recognize(self, pcm: bytes) -> Recognition:
        return await self._pool.run(
            partial(_decode, pcm), Priority.RECOGNITION, start_within_s=self._start_within_s
        )

    def _load(self) -> sherpa_onnx.OnlineRecognizer:
        d = self._model_dir
        return sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(d / "tokens.txt"),
            encoder=str(d / "encoder.onnx"),
            decoder=str(d / "decoder.onnx"),
            joiner=str(d / "joiner.onnx"),
            num_threads=1,  # the pool size is the recogniser's share of the cores
            sample_rate=AUDIO_SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",  # measured no worse than beam search here
        )


def _decode(pcm: bytes, recognizer: sherpa_onnx.OnlineRecognizer) -> Recognition:
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    stream = recognizer.create_stream()
    stream.accept_waveform(AUDIO_SAMPLE_RATE, samples)
    stream.accept_waveform(AUDIO_SAMPLE_RATE, np.zeros(int(TAIL_S * AUDIO_SAMPLE_RATE), np.float32))
    stream.input_finished()
    while recognizer.is_ready(stream):
        recognizer.decode_stream(stream)
    result = recognizer.get_result_all(stream)
    text = str(result.text).strip()
    log_probs = list(result.ys_probs)
    confidence = math.exp(sum(log_probs) / len(log_probs)) if log_probs and text else 0.0
    return Recognition(text=text, confidence=round(min(1.0, confidence), 3))
