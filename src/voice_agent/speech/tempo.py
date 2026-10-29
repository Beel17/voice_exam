"""Speech faster or slower without changing its pitch (WSOLA).

Piper's own speed control is not proportional and tops out early: measured in phase 2,
the US voice reached only 1.96x at its highest setting. Experienced screen-reader users
listen at 2-3x, so the service synthesises at normal speed and changes the tempo here,
which gives every voice the exact rate asked for and keeps it sounding like itself.

Waveform-similarity overlap-add: the output is built from short overlapping windows of
the input, each taken from near where the requested rate says it should come from, at
the offset that best continues the previous window, so speech stays smooth.
"""

from typing import Final

import numpy as np
from numpy.typing import NDArray

FRAME_S: Final = 0.030
SEARCH_S: Final = 0.010
# The correlation search looks at every 4th sample: four times faster, same choices in
# practice, since speech changes slowly at that scale.
_DECIMATE: Final = 4


def change_tempo(samples: NDArray[np.float32], speed: float, rate: int) -> NDArray[np.float32]:
    """``samples`` played ``speed`` times faster (``speed`` < 1 is slower), same pitch."""
    if abs(speed - 1.0) < 1e-3 or len(samples) == 0:
        return samples
    frame = int(FRAME_S * rate)
    hop_out = frame // 2
    hop_in = hop_out * speed
    search = int(SEARCH_S * rate)
    window = np.hanning(frame).astype(np.float32)

    padded = np.concatenate(
        [np.zeros(search, np.float32), samples, np.zeros(frame + search + hop_out, np.float32)]
    )
    frames = int(len(samples) / hop_in) + 1
    out = np.zeros(frames * hop_out + frame, np.float32)
    weight = np.zeros_like(out)

    chosen = search  # where the previous window started, in ``padded``
    for k in range(frames):
        target = int(k * hop_in) + search
        if k == 0:
            start = target
        else:
            # What would naturally follow the previous window...
            follow = padded[chosen + hop_out : chosen + hop_out + frame : _DECIMATE]
            # ...compared with every window near where this one should start.
            region = padded[target - search : target + search + frame]
            candidates = np.lib.stride_tricks.sliding_window_view(region, frame)[:, ::_DECIMATE]
            start = target - search + int(np.argmax(candidates @ follow))
        out[k * hop_out : k * hop_out + frame] += padded[start : start + frame] * window
        weight[k * hop_out : k * hop_out + frame] += window
        chosen = start

    length = round(len(samples) / speed)
    return (out[:length] / np.maximum(weight[:length], 1e-3)).astype(np.float32)
