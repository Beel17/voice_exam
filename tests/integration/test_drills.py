"""A few failure drills on the real service process (the full set: python -m tools.drills)."""

import tempfile
from pathlib import Path

import pytest
from tools import drills


@pytest.mark.parametrize(
    "drill",
    [drills.killed_mid_exam, drills.platform_down_at_save, drills.connection_dropped_mid_turn],
    ids=lambda d: d.__name__,
)
async def test_the_service_recovers(drill: drills.Drill) -> None:
    with tempfile.TemporaryDirectory(prefix="voice-drill-", ignore_cleanup_errors=True) as tmp:
        service = drills.Service(Path(tmp), with_model=False)
        service.start()
        try:
            ok, detail = await drill(service)
        finally:
            service.stop()
    assert ok, detail
