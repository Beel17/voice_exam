import io
import itertools
import wave

import numpy as np
import pytest

from voice_agent.contract import AUDIO_OUT_SAMPLE_RATE
from voice_agent.speech.cues import CUE_NAMES, samples, wav


@pytest.mark.parametrize("name", CUE_NAMES)
def test_each_cue_is_a_short_clean_wav(name: str) -> None:
    with wave.open(io.BytesIO(wav(name))) as w:  # type: ignore[arg-type]
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (
            1,
            2,
            AUDIO_OUT_SAMPLE_RATE,
        )
        seconds = w.getnframes() / AUDIO_OUT_SAMPLE_RATE
    assert 0.1 < seconds < 0.5  # a cue, not a speech
    sound = samples(name)  # type: ignore[arg-type]
    assert np.abs(sound).max() < 0.8  # never clips
    # Starts and ends at silence: no click when it plays or stops.
    assert abs(sound[0]) < 0.01 and abs(sound[-1]) < 0.01


def _pitch(sound: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(sound))
    return float(np.argmax(spectrum) * AUDIO_OUT_SAMPLE_RATE / len(sound))


def test_cues_can_be_told_apart() -> None:
    # Different main pitch or a different length, for every pair: never two alike.
    features = {n: (_pitch(samples(n)), len(samples(n))) for n in CUE_NAMES}
    for a, b in itertools.combinations(CUE_NAMES, 2):
        (pitch_a, len_a), (pitch_b, len_b) = features[a], features[b]
        assert abs(pitch_a - pitch_b) > 100 or abs(len_a - len_b) > 0.05 * AUDIO_OUT_SAMPLE_RATE


def test_not_understood_is_low_and_saved_is_high() -> None:
    assert _pitch(samples("not_understood")) < 300 < _pitch(samples("saved"))
