"""Speech synthesis: Piper voices through sherpa-onnx, in a worker pool.

Replies are spoken sentence by sentence, so the candidate hears the first sentence while
the rest is still being made; the first sentence runs at a higher priority than the rest.
Synthesised sentences are cached per speed, and the fixed prompts every session uses are
rendered once at start-up, so most replies other than question text cost nothing.

Measured in phase 2 (one thread, medium voices): about 3.5x faster than real time, and
"Question 12 of 60." in 0.24 s at normal speed.
"""

import re
from collections import OrderedDict
from functools import partial
from pathlib import Path
from typing import Final, Protocol

import numpy as np
import sherpa_onnx

from voice_agent.contract import AUDIO_OUT_SAMPLE_RATE
from voice_agent.runtime.pools import PoolStats, Priority, WorkerPool
from voice_agent.session import replies as r
from voice_agent.speech.speakable import speakable
from voice_agent.speech.tempo import change_tempo

# A sentence ends at . ? or ! followed by a space; "0.75" and "Option A: frank" stay whole.
_SENTENCE_END: Final = re.compile(r"(?<=[.?!])\s+")


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text.strip()) if s]


def fixed_sentences() -> list[str]:
    """Sentences every session says, whatever the exam: worth rendering at start-up."""
    replies: list[r.Reply] = [
        r.Saved(1, "A"), r.NothingToConfirm(), r.SayYesToConfirm(), r.Help(),
        r.NotUnderstood(offer_keyboard=False), r.NotUnderstood(offer_keyboard=True),
        r.OutOfScope(), r.IdentifyPrompt(), r.OneMoment(), r.StillThinking(), r.NothingHeard(),
        r.KeyboardMode(), r.VoiceBack(),
        r.AtFirst(), r.AllAnswered(), r.NotSubmitted(), r.ConfirmSubmitFinal(0),
        r.Submitted(queued=False), r.IntroPrompt(), r.Unanswered(()),
    ]  # fmt: skip
    sentences = {
        sentence
        for reply in replies
        for part in r.render(reply)
        for sentence in split_sentences(part.text)
    }
    sentences.add("Say yes to save it, or no to cancel.")
    sentences.update(f"Say a letter, A to {letter}, to answer." for letter in "BCDE")
    return sorted(sentences)


class Synthesizer(Protocol):
    async def synthesize(self, sentence: str, speed: float, priority: Priority) -> bytes:
        """16-bit mono PCM at ``AUDIO_OUT_SAMPLE_RATE``. May raise ``PoolBusy``."""
        ...


class SherpaSynthesizer:
    def __init__(
        self,
        voice_dir: Path,
        *,
        workers: int,
        max_queue: int,
        start_within_s: float,
        cache_entries: int = 2000,
    ) -> None:
        self._voice_dir = voice_dir
        self._start_within_s = start_within_s
        self._pool: WorkerPool[sherpa_onnx.OfflineTts] = WorkerPool(
            "synthesis", workers, self._load, max_queue=max_queue
        )
        # Sentence and speed -> PCM. At ~100 KB per spoken second this stays well under
        # the memory budget's few hundred MB for the service.
        self._cache: OrderedDict[tuple[str, float], bytes] = OrderedDict()
        self._cache_entries = cache_entries

    async def start(self) -> None:
        await self._pool.start()
        rate = await self._pool.run(_sample_rate, Priority.BACKGROUND, start_within_s=60)
        if rate != AUDIO_OUT_SAMPLE_RATE:
            await self._pool.stop()
            raise RuntimeError(
                f"{self._voice_dir.name} speaks at {rate} Hz, not {AUDIO_OUT_SAMPLE_RATE}"
            )
        for sentence in fixed_sentences():
            await self.synthesize(sentence, 1.0, Priority.BACKGROUND)

    async def stop(self) -> None:
        await self._pool.stop()

    def stats(self) -> PoolStats:
        return self._pool.stats()

    async def synthesize(self, sentence: str, speed: float, priority: Priority) -> bytes:
        """16-bit mono PCM at ``AUDIO_OUT_SAMPLE_RATE``. May raise ``PoolBusy``."""
        key = (sentence, speed)
        if (cached := self._cache.get(key)) is not None:
            self._cache.move_to_end(key)
            return cached
        job = partial(_generate, speakable(sentence), speed)
        pcm = await self._pool.run(job, priority, start_within_s=self._start_within_s)
        self._cache[key] = pcm
        if len(self._cache) > self._cache_entries:
            self._cache.popitem(last=False)
        return pcm

    def _load(self) -> sherpa_onnx.OfflineTts:
        d = self._voice_dir
        vits = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=str(next(d.glob("*.onnx"))),
            tokens=str(d / "tokens.txt"),
            data_dir=str(d / "espeak-ng-data"),
        )
        model = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=1)
        return sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model))


def _sample_rate(voice: sherpa_onnx.OfflineTts) -> int:
    return int(voice.sample_rate)


def _generate(text: str, speed: float, voice: sherpa_onnx.OfflineTts) -> bytes:
    # Always synthesise at the voice's natural pace and change the tempo afterwards:
    # Piper's own speed setting is not proportional and cannot reach 2.5x (see tempo.py).
    audio = voice.generate(text, sid=0, speed=1.0)
    samples = np.asarray(audio.samples, dtype=np.float32)
    samples = np.clip(change_tempo(samples, speed, int(audio.sample_rate)), -1.0, 1.0)
    return bytes((samples * 32767).astype("<i2").tobytes())
