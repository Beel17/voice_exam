import numpy as np
import pytest

from voice_agent.speech.tempo import change_tempo

RATE = 22050


def tone(hz: float, seconds: float) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def dominant_hz(samples: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.argmax(spectrum) * RATE / len(samples))


@pytest.mark.parametrize("speed", [0.8, 1.5, 2.0, 2.5, 3.0])
def test_length_is_exact_and_pitch_unchanged(speed: float) -> None:
    original = tone(220.0, 2.0)
    changed = change_tempo(original, speed, RATE)
    assert len(changed) == round(len(original) / speed)
    assert dominant_hz(changed) == pytest.approx(220.0, abs=3)
    # Steady, not choppy: the level in the middle stays close to the original's.
    middle = changed[len(changed) // 4 : 3 * len(changed) // 4]
    assert np.sqrt(np.mean(middle**2)) == pytest.approx(0.5 / np.sqrt(2), rel=0.1)
    assert changed.dtype == np.float32


def test_normal_speed_and_silence_are_untouched() -> None:
    original = tone(220.0, 0.5)
    assert change_tempo(original, 1.0, RATE) is original
    empty = np.zeros(0, np.float32)
    assert len(change_tempo(empty, 2.0, RATE)) == 0
