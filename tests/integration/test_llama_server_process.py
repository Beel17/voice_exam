"""The model server as a real child process, using a stand-in that speaks its HTTP API."""

import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from voice_agent.understanding import intents as i
from voice_agent.understanding.llm import LlamaServer, LlmContext, LlmFallback, LlmUnderstander

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
    import asyncio

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


async def test_a_server_that_exits_at_start_is_reported(tmp_path: Path) -> None:
    s = server(tmp_path, "--exit-at-start")
    with pytest.raises(RuntimeError, match="exited"):
        await s.start()


async def test_a_server_that_never_becomes_ready_is_stopped(tmp_path: Path) -> None:
    s = server(tmp_path, "--ready-after", "60", ready_timeout_s=0.5)
    with pytest.raises(RuntimeError, match="not ready"):
        await s.start()
    assert not s.running


async def test_stopping_twice_is_harmless(running: LlamaServer) -> None:
    await running.stop()
    await running.stop()
    assert not running.running
