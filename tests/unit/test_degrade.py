import pytest

from voice_agent.runtime.degrade import Degrader, Level, Load, level_for

HEALTHY = Load(language_model_up=True, language_model_refusals=0)


@pytest.mark.parametrize(
    ("load", "level"),
    [
        (HEALTHY, Level.NORMAL),
        (Load(language_model_up=False, language_model_refusals=0), Level.RULES_ONLY),
        # One slow answer is not a shortage: rules only from the third in the window.
        (Load(language_model_up=True, language_model_refusals=2), Level.NORMAL),
        (Load(language_model_up=True, language_model_refusals=3), Level.RULES_ONLY),
        (Load(True, 0, synthesis_queued=6, synthesis_workers=2), Level.NORMAL),
        (Load(True, 0, synthesis_queued=7, synthesis_workers=2), Level.FIXED_PROMPTS),
        (Load(True, 0, recognition_queued=8, recognition_workers=4), Level.NORMAL),
        (Load(True, 0, recognition_queued=9, recognition_workers=4), Level.KEYBOARD),
        (Load(True, 0, speech_up=False), Level.KEYBOARD),
        # The worst problem decides.
        (Load(False, 5, speech_up=False, synthesis_queued=99), Level.KEYBOARD),
    ],
)
def test_level_for_load(load: Load, level: Level) -> None:
    assert level_for(load) == level


class Clock:
    now = 0.0

    def __call__(self) -> float:
        return self.now


def test_levels_rise_at_once_and_fall_only_after_a_calm_spell() -> None:
    clock = Clock()
    d = Degrader(calm_s=10, clock=clock)
    assert d.update(HEALTHY) is Level.NORMAL
    assert d.update(Load(True, 0, speech_up=False)) is Level.KEYBOARD  # at once

    clock.now = 1
    assert d.update(HEALTHY) is Level.KEYBOARD  # calm starts here
    clock.now = 9
    assert d.update(HEALTHY) is Level.KEYBOARD
    clock.now = 11
    assert d.update(HEALTHY) is Level.NORMAL
    assert d.level is Level.NORMAL


def test_a_relapse_restarts_the_calm_spell() -> None:
    clock = Clock()
    d = Degrader(calm_s=10, clock=clock)
    d.update(Load(False, 0))
    clock.now = 1
    d.update(HEALTHY)
    clock.now = 5
    assert d.update(Load(False, 0)) is Level.RULES_ONLY  # the model is down again
    clock.now = 12
    assert d.update(HEALTHY) is Level.RULES_ONLY  # a fresh calm spell starts now
    clock.now = 23
    assert d.update(HEALTHY) is Level.NORMAL
