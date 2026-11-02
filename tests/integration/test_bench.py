"""The benchmark tool and its process probes still work (a short run, not a measurement)."""

import argparse
import os
from pathlib import Path

import pytest
from tools import bench
from tools.procstats import cpu_seconds, machine, rss_bytes

VOICE = "models/speech/tts/vits-piper-en_US-lessac-medium"


def test_probes_read_this_process() -> None:
    pid = os.getpid()
    assert rss_bytes(pid) > 10 * 2**20
    before = cpu_seconds(pid)
    sum(i * i for i in range(2_000_000))
    assert cpu_seconds(pid) > before
    info = machine()
    assert set(info) == {"os", "cpu", "logical_cores", "ram_gb", "python"}
    assert isinstance(info["ram_gb"], float) and info["ram_gb"] > 0


async def test_a_short_benchmark_run() -> None:
    args = argparse.Namespace(
        turns=12,
        llm_turns=0,
        sessions=3,
        candidates=3,
        seconds=1.0,
        turn_interval=10.0,
        llm_binary="",
        llm_model=None,
        llm_threads=1,
        llm_slots=1,
        no_speech=True,
        spoken_turns=0,
        voice_dir=VOICE,
    )
    report = await bench.run(args)
    assert report["rules_path"]["turns"] == 12
    assert report["memory"]["sessions"] == 3
    assert report["memory"]["service_rss_mb"] > 20  # the service itself, not a launcher
    assert report["saturation"]["turns_per_cpu_second"] > 0
    bench.show(report)


@pytest.mark.skipif(not Path(VOICE).is_dir(), reason="speech models not fetched")
async def test_a_short_spoken_run() -> None:
    """A synthetic voice speaks commands as microphone audio; each must be heard and
    answered with audio."""
    args = argparse.Namespace(
        turns=4,
        llm_turns=0,
        sessions=1,
        candidates=1,
        seconds=1.0,
        turn_interval=10.0,
        llm_binary="",
        llm_model=None,
        llm_threads=1,
        llm_slots=1,
        no_speech=False,
        spoken_turns=len(bench.SPOKEN),
        voice_dir=VOICE,
    )
    report = await bench.run(args)
    spoken = report["spoken_path"]
    assert spoken["heard_exactly"] == f"{len(bench.SPOKEN)}/{len(bench.SPOKEN)}"
    assert spoken["first_audio_p50_ms"] > 0
    bench.show(report)


def test_cpu_time_as_ps_prints_it() -> None:
    from tools.procstats import parse_cputime

    assert parse_cputime("0:01.23") == 1.23  # minutes:seconds (macOS)
    assert parse_cputime("1:02:03.50") == 3723.5  # hours too
    assert parse_cputime("2-01:00:00") == 2 * 86_400 + 3600  # and days


async def test_a_grandchild_is_found_by_name(tmp_path: Path) -> None:
    # As the model server is found under the warden on macOS: two levels down. (A
    # virtual environment's python on Windows is a launcher with the real one below it,
    # so more than one may be found; the grandchild must be among them.)
    import asyncio
    import signal
    import sys

    from tools.drills import descendants

    pid_file = tmp_path / "grandchild.pid"
    inner = f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(30)"
    outer = (
        "import subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {inner!r}]); time.sleep(30)"
    )
    parent = await asyncio.create_subprocess_exec(sys.executable, "-c", outer)
    try:
        for _ in range(200):
            if pid_file.exists() and pid_file.read_text():
                break
            await asyncio.sleep(0.05)
        grandchild = int(pid_file.read_text())
        assert grandchild in descendants(parent.pid, "python")
        assert grandchild not in descendants(grandchild, "python")  # not its own descendant
    finally:
        for pid in descendants(parent.pid, "python"):
            os.kill(pid, signal.SIGTERM)
        parent.kill()
        await parent.wait()
