"""The model server as a real child process, using a stand-in that speaks its HTTP API."""

import asyncio
import os
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from voice_agent.understanding import intents as i
from voice_agent.understanding.llm import (
    LlamaServer,
    LlmContext,
    LlmFallback,
    LlmUnderstander,
    ModelServerFailed,
    describe_exit,
)

FAKE = Path(__file__).parents[1] / "fakes" / "fake_llama_server.py"
CTX = LlmContext(question=1, of=60, option_count=4, awaiting=None)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def server(tmp_path: Path, *extra: str, ready_timeout_s: float = 10) -> LlamaServer:
    port = free_port()
    command = [sys.executable, str(FAKE), "--port", str(port), "--threads", "2", *extra]
    return LlamaServer(
        command, port=port, log_path=tmp_path / "llm.log", ready_timeout_s=ready_timeout_s
    )


@pytest.fixture
async def running(tmp_path: Path) -> AsyncIterator[LlamaServer]:
    s = server(tmp_path, "--ready-after", "0.3")
    await s.start()
    yield s
    await s.stop()


async def test_start_waits_until_ready_then_answers(running: LlamaServer) -> None:
    assert running.running
    llm = LlmUnderstander(running.base_url, timeout_s=5)
    said = await llm.understand("s", "hmm I reckon it is probably B", CTX)
    assert said == i.Answer(args=i.ChoiceArgs(choice="B"))
    assert await llm.understand("s", "GARBAGE please", CTX) == i.Unclear()
    with pytest.raises(LlmFallback, match="unavailable"):
        await llm.understand("s", "FAIL", CTX)
    await llm.aclose()


async def test_a_crashed_server_is_restarted(running: LlamaServer) -> None:
    llm = LlmUnderstander(running.base_url, timeout_s=5)
    with pytest.raises(LlmFallback, match="unavailable"):
        await llm.understand("s", "CRASH", CTX)
    assert not running.running or await _exits(running)
    assert await running.ensure_running() is True
    assert running.restarts == 1
    assert await llm.understand("s", "is B the right one", CTX) == i.NotAllowed()
    assert await running.ensure_running() is False
    await llm.aclose()


async def _exits(s: LlamaServer) -> bool:
    for _ in range(50):
        if not s.running:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_a_hung_answer_times_out(running: LlamaServer) -> None:
    llm = LlmUnderstander(running.base_url, timeout_s=0.3)
    with pytest.raises(LlmFallback, match="timeout"):
        await llm.understand("s", "HANG", CTX)
    await llm.aclose()


async def test_a_server_that_exits_at_start_is_reported_with_its_reason(tmp_path: Path) -> None:
    (tmp_path / "llm.log").write_text("an earlier run's output\n", encoding="utf-8")
    s = server(tmp_path, "--exit-at-start")
    with pytest.raises(ModelServerFailed) as failed:
        await s.start()
    # What a tester pastes back says why: the exit code, and this run's output only.
    message = str(failed.value)
    assert "exit code 3" in message
    assert "failed to load model" in message and "earlier run" not in message


def test_windows_exit_codes_are_explained() -> None:
    # As a signed int (Python's returncode on Windows can be either) and unsigned.
    assert "Visual C++" in describe_exit(0xC0000135)
    assert "Visual C++" in describe_exit(0xC0000135 - 2**32)
    assert describe_exit(-1073741819).startswith("exit code 0xC0000005: it crashed")
    assert describe_exit(1) == "exit code 1"


async def test_a_failing_server_is_retried_less_and_less_often(tmp_path: Path) -> None:
    s = server(tmp_path, "--exit-at-start")
    with pytest.raises(ModelServerFailed):
        await s.ensure_running()
    assert 4 < s.retry_in_s <= 5
    assert await s.ensure_running() is False  # too soon: not even tried
    s._next_try = 0.0
    with pytest.raises(ModelServerFailed):
        await s.ensure_running()
    assert 9 < s.retry_in_s <= 10
    assert s.failures == 2


async def test_a_server_that_never_becomes_ready_is_stopped(tmp_path: Path) -> None:
    s = server(tmp_path, "--ready-after", "60", ready_timeout_s=0.5)
    with pytest.raises(RuntimeError, match="not ready"):
        await s.start()
    assert not s.running


async def test_stopping_twice_is_harmless(running: LlamaServer) -> None:
    await running.stop()
    await running.stop()
    assert not running.running


def alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY
        if not handle:
            return False
        try:
            return bool(kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


PARENT = """
import asyncio, sys
from pathlib import Path
from voice_agent.understanding.llm import LlamaServer

async def main():
    command = sys.argv[1:]  # python, the stand-in, --port, N, --pid-file, path
    port = int(command[command.index("--port") + 1])
    server = LlamaServer(command, port=port, log_path=Path(command[-1] + ".log"))
    await server.start()
    print("ready", flush=True)
    await asyncio.sleep(60)

asyncio.run(main())
"""


async def test_the_model_server_dies_with_a_killed_service(tmp_path: Path) -> None:
    pid_file = tmp_path / "server.pid"
    port = free_port()
    command = [sys.executable, str(FAKE), "--port", str(port), "--pid-file", str(pid_file)]
    parent = await asyncio.create_subprocess_exec(
        sys.executable, "-c", PARENT, *command, stdout=asyncio.subprocess.PIPE
    )
    assert parent.stdout is not None
    assert (await asyncio.wait_for(parent.stdout.readline(), 30)).strip() == b"ready"
    server_pid = int(pid_file.read_text())
    assert alive(server_pid)

    parent.kill()  # no clean shutdown: as in a crash or Task Manager
    await parent.wait()
    for _ in range(100):
        if not alive(server_pid):
            break
        await asyncio.sleep(0.05)
    assert not alive(server_pid), "the model server outlived the service"
