from collections.abc import Callable, Iterator

import pytest

from tests.unit.script import Script


@pytest.fixture
def script() -> Iterator[Callable[..., Script]]:
    """Make scripted sessions; every one must rebuild exactly from its event log."""
    made: list[Script] = []

    def make(**kwargs: object) -> Script:
        s = Script(**kwargs)  # type: ignore[arg-type]
        made.append(s)
        return s

    yield make
    for s in made:
        s.check_replay()
