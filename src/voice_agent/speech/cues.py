"""Sound cues: short tones for routine events, instead of spoken prompts.

Made here from sine tones, so there are no sound files to ship or verify, and every
centre hears the same four sounds. The PC fetches each once (``/v1/cues/{name}.wav``) and
plays it locally when told to, so "listening" sounds the instant the talk key goes down.
The four are meant to be told apart without looking: different pitch, rhythm and
direction. Blind testers judge them in phase 2; changing one is a change of numbers here.
"""

import io
import wave
from typing import Final

import numpy as np
from numpy.typing import NDArray

from voice_agent.contract import AUDIO_OUT_SAMPLE_RATE, CueName

CUE_NAMES: Final[tuple[CueName, ...]] = ("listening", "saved", "not_understood", "time_warning")

# Each cue as notes: (frequency in Hz, or 0 for a rest; length in seconds).
_NOTES: Final[dict[CueName, list[tuple[float, float]]]] = {
    "listening": [(660, 0.06), (990, 0.08)],  # quick, rising: "go ahead"
    "saved": [(784, 0.09), (1175, 0.16)],  # bright, a fifth up: done
    "not_understood": [(220, 0.12), (0, 0.06), (196, 0.16)],  # low, falling, twice
    "time_warning": [(1000.0, 0.08), (0.0, 0.07)] * 2 + [(1000.0, 0.08)],  # three short beeps
}


def _note(hz: float, seconds: float) -> NDArray[np.float32]:
    n = int(seconds * AUDIO_OUT_SAMPLE_RATE)
    if hz == 0:
        return np.zeros(n, np.float32)
    t = np.arange(n) / AUDIO_OUT_SAMPLE_RATE
    tone = np.sin(2 * np.pi * hz * t) + 0.3 * np.sin(4 * np.pi * hz * t)  # a little warmth
    # 8 ms fades: a tone that starts or stops abruptly clicks.
    fade = min(n // 2, int(0.008 * AUDIO_OUT_SAMPLE_RATE))
    envelope = np.ones(n)
    envelope[:fade] = np.linspace(0, 1, fade)
    envelope[n - fade :] = np.linspace(1, 0, fade)
    return (tone * envelope * 0.45).astype(np.float32)


def samples(name: CueName) -> NDArray[np.float32]:
    return np.concatenate([_note(hz, s) for hz, s in _NOTES[name]])


def wav(name: CueName) -> bytes:
    """The cue as a WAV file (16-bit mono, the same rate as speech)."""
    pcm = (np.clip(samples(name), -1, 1) * 32767).astype("<i2").tobytes()
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(AUDIO_OUT_SAMPLE_RATE)
        w.writeframes(pcm)
    return out.getvalue()
