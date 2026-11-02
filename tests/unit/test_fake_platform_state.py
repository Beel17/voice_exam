"""The sample platform with a state folder: it outlives the service and fails on demand."""

import time
from pathlib import Path

import pytest

from voice_agent.adapters.base import AdapterUnavailable
from voice_agent.adapters.fake_adapter import FakeAdapter


async def signed_in(state: Path) -> tuple[FakeAdapter, str]:
    platform = FakeAdapter.with_bundled_fixtures(state_dir=state)
    session = await platform.verify_candidate("10000001AA", "UTME-SAMPLE")
    return platform, session.candidate_id


async def test_saves_outlive_the_service(tmp_path: Path) -> None:
    platform, candidate = await signed_in(tmp_path)
    await platform.save_answer(candidate, 1, "B", "s1:5")
    await platform.submit(candidate, "s1:9")
    again = FakeAdapter.with_bundled_fixtures(state_dir=tmp_path)  # a new process
    assert (await again.get_saved_answers(candidate)).answers == {1: "B"}
    await again.save_answer(candidate, 1, "B", "s1:5")  # a retried write is still recognised
    assert (tmp_path / "platform.json").is_file() and not (tmp_path / "platform.json.tmp").exists()


async def test_time_is_kept_across_restarts(tmp_path: Path) -> None:
    platform, candidate = await signed_in(tmp_path)
    left = await platform.get_time_left(candidate)
    again = FakeAdapter.with_bundled_fixtures(state_dir=tmp_path)
    await again.verify_candidate("10000001AA", "UTME-SAMPLE")  # signing in again
    assert left - 2 <= await again.get_time_left(candidate) <= left


async def test_the_down_switch(tmp_path: Path) -> None:
    platform, candidate = await signed_in(tmp_path)
    (tmp_path / "platform-down").touch()
    with pytest.raises(AdapterUnavailable):
        await platform.save_answer(candidate, 1, "A", "s1:5")
    (tmp_path / "platform-down").unlink()
    await platform.save_answer(candidate, 1, "A", "s1:5")


async def test_the_slow_switch(tmp_path: Path) -> None:
    platform, candidate = await signed_in(tmp_path)
    (tmp_path / "platform-slow").write_text("0.3", encoding="ascii")
    began = time.monotonic()
    await platform.get_saved_answers(candidate)
    assert time.monotonic() - began >= 0.25  # Windows may wake a sleep a little early


async def test_without_a_state_folder_nothing_is_written(tmp_path: Path) -> None:
    platform = FakeAdapter.with_bundled_fixtures()
    session = await platform.verify_candidate("10000001AA", "UTME-SAMPLE")
    await platform.save_answer(session.candidate_id, 1, "A", "s1:5")
    assert list(tmp_path.iterdir()) == []
