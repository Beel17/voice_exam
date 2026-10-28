"""Speech recognition: what the API needs from a recogniser.

Audio arrives while the talk key is held and is recognised when it is released. The
recogniser itself (sherpa-onnx, in a worker pool) comes in phase 2, step 2; the API
depends only on this protocol.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Recognition:
    text: str
    confidence: float  # 0..1


class Recognizer(Protocol):
    async def recognize(self, pcm: bytes) -> Recognition:
        """Recognise 16 kHz mono 16-bit little-endian PCM. May raise ``PoolBusy``."""
        ...
