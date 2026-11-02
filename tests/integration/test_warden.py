"""The warden: on macOS, what keeps the model server from outliving the service. It is
portable, so it is tested everywhere (on Windows and Linux the system does the job, and
the warden is only used when VOICE_AGENT_WARDEN=1 forces it)."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from tests.integration.test_llama_server_process import FAKE, PARENT, alive, free_port
from voice_agent.runtime import children
from voice_agent.runtime.warden import exit_code

WARDEN = [sys.executable, "-m", "voice_agent.runtime.warden", "--"]


async def test_the_commands_exit_code_comes_through() -> None:
    process = await asyncio.create_subprocess_exec(
        *WARDEN, sys.executable, "-c", "raise SystemExit(7)", stdin=asyncio.subprocess.PIPE
    )
    assert await asyncio.wait_for(process.wait(), 30) == 7


def test_a_signal_is_reported_as_128_plus_its_number() -> None:
    assert exit_code(-9) == 137 and exit_code(-15) == 143 and exit_code(3) == 3


async def test_without_a_command_it_says_how_to_use_it() -> None:
    process = await asyncio.create_subprocess_exec(*WARDEN, stderr=asyncio.subprocess.PIPE)
    _, err = await process.communicate()
    assert process.returncode == 2 and b"usage" in err


# A stand-in for the service: starts a long-running command under the warden, as
# LlamaServer does on macOS, then waits to be killed.
SERVICE = """
import subprocess, sys, time
# Kept referenced: a freed Popen closes its pipe, which the warden takes as death.
lifeline = subprocess.Popen(sys.argv[1:], stdin=subprocess.PIPE)  # never written
print("ready", flush=True)
time.sleep(60)
"""
SLEEPER = "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(60)"


async def wait_for_pid(pid_file: Path) -> int:
    for _ in range(200):
        if pid_file.exists() and pid_file.read_text():
            return int(pid_file.read_text())
        await asyncio.sleep(0.05)
    raise AssertionError("the command never started")


async def gone(pid: int) -> bool:
    for _ in range(100):
        if not alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


async def test_the_command_dies_with_a_killed_service(tmp_path: Path) -> None:
    pid_file = tmp_path / "command.pid"
    service = await asyncio.create_subprocess_exec(
        sys.executable, "-c", SERVICE, *WARDEN, sys.executable, "-c", SLEEPER, str(pid_file),
        stdout=asyncio.subprocess.PIPE,
    )  # fmt: skip
    assert service.stdout is not None
    assert (await asyncio.wait_for(service.stdout.readline(), 30)).strip() == b"ready"
    command = await wait_for_pid(pid_file)
    assert alive(command)
    service.kill()  # no clean shutdown: a crash, or the power button
    await service.wait()
    assert await gone(command), "the command outlived the service"


async def test_the_model_server_under_the_warden_dies_with_a_killed_service(
    tmp_path: Path,
) -> None:
    # The whole path LlamaServer takes on macOS, forced here, with the Windows job object
    # out of the way: only the warden stands between a crash and a leftover server.
    pid_file = tmp_path / "server.pid"
    command = [sys.executable, str(FAKE), "--port", str(free_port()), "--pid-file", str(pid_file)]
    service = await asyncio.create_subprocess_exec(
        sys.executable, "-c", PARENT, *command,
        stdout=asyncio.subprocess.PIPE, env={**os.environ, children.WARDEN_ENV: "1"},
    )  # fmt: skip
    assert service.stdout is not None
    assert (await asyncio.wait_for(service.stdout.readline(), 30)).strip() == b"ready"
    server = int(pid_file.read_text())
    assert alive(server)
    service.kill()
    await service.wait()
    assert await gone(server), "the model server outlived the service"


def test_the_warden_is_used_on_macos_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(children.WARDEN_ENV, raising=False)
    assert children.command_for(["x"]) == ([*WARDEN, "x"] if sys.platform == "darwin" else ["x"])
    monkeypatch.setenv(children.WARDEN_ENV, "1")
    assert children.command_for(["x"]) == [*WARDEN, "x"]
    assert children.stdin_for() == asyncio.subprocess.PIPE
