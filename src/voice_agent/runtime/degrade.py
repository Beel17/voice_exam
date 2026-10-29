"""Degradation levels: what the service gives up, in order, when it is short of capacity.

    0 Normal         everything on
    1 Rules only     the language model is down or overloaded; sentences the rules do not
                     know get "not understood", which lists what can be said
    2 Fixed prompts  synthesis is behind; replies use pre-rendered prompts, question text
                     is still synthesised, with priority
    3 Keyboard       recognition is behind, or speech is down; candidates are told to use
                     the keyboard, and told again when voice is back

A level rises as soon as the load calls for it and falls only after the load has stayed
lower for a while, so the service does not flap between modes a candidate would notice.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum


class Level(IntEnum):
    NORMAL = 0
    RULES_ONLY = 1
    FIXED_PROMPTS = 2
    KEYBOARD = 3


@dataclass(frozen=True, slots=True)
class Load:
    language_model_up: bool
    language_model_refusals: int  # model calls refused as busy or too slow, recently
    speech_up: bool = True  # recogniser and voices loaded; True when speech is not in use
    recognition_queued: int = 0
    recognition_workers: int = 1
    synthesis_queued: int = 0
    synthesis_workers: int = 1


# How many jobs may wait per worker before the level rises. Recognition is strictest:
# every queued job is a candidate who has stopped talking and is waiting.
RECOGNITION_BACKLOG = 2
SYNTHESIS_BACKLOG = 3
# Model calls refused as busy or too slow within the recent window before every sentence
# goes to rules only. One slow answer is not a shortage of capacity: on a PC, a single
# timeout used to switch the model off for every sentence of the next 45 seconds.
MODEL_REFUSALS = 3


def level_for(load: Load) -> Level:
    if (
        not load.speech_up
        or load.recognition_queued > RECOGNITION_BACKLOG * load.recognition_workers
    ):
        return Level.KEYBOARD
    if load.synthesis_queued > SYNTHESIS_BACKLOG * load.synthesis_workers:
        return Level.FIXED_PROMPTS
    if not load.language_model_up or load.language_model_refusals >= MODEL_REFUSALS:
        return Level.RULES_ONLY
    return Level.NORMAL


class Degrader:
    def __init__(self, calm_s: float = 15.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._calm_s = calm_s
        self._clock = clock
        self._level = Level.NORMAL
        self._calm_since: float | None = None

    @property
    def level(self) -> Level:
        return self._level

    def update(self, load: Load) -> Level:
        wanted = level_for(load)
        now = self._clock()
        if wanted > self._level:
            self._level, self._calm_since = wanted, None
        elif wanted < self._level:
            if self._calm_since is None:
                self._calm_since = now
            elif now - self._calm_since >= self._calm_s:
                self._level, self._calm_since = wanted, None
        else:
            self._calm_since = None
        return self._level
