"""Bounds on how much work any one client can cause."""

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """At most ``limit`` calls per session in any ``window_s`` seconds."""

    def __init__(
        self, limit: int, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._calls: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self._clock()
        calls = self._calls.setdefault(key, deque())
        while calls and calls[0] <= now - self._window:
            calls.popleft()
        if len(calls) >= self._limit:
            return False
        calls.append(now)
        return True
