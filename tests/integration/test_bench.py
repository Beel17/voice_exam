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
