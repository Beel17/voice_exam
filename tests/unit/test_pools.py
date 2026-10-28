import asyncio
import threading
import time
from collections.abc import AsyncIterator
from functools import partial

import pytest

from voice_agent.runtime.pools import PoolBusy, Priority, WorkerPool


class Engine:
    """Stands in for a recogniser or voice: records which thread loaded it."""

    def __init__(self) -> None:
        self.thread = threading.current_thread().name
        self.release = threading.Event()


def thread_name_after_a_pause(engine: Engine) -> str:
    time.sleep(0.05)
    return engine.thread


@pytest.fixture
async def pool() -> AsyncIterator[WorkerPool[Engine]]:
    p: WorkerPool[Engine] = WorkerPool("test", 1, Engine, max_queue=4)
    await p.start()
    yield p
    await p.stop()


async def blocked(pool: WorkerPool[Engine]) -> tuple["asyncio.Task[str]", threading.Event]:
    """Occupy the (single) worker until the returned event is set."""
    gate = threading.Event()
    task = asyncio.create_task(
        pool.run(lambda e: (gate.wait(5), "first")[1], Priority.BACKGROUND, start_within_s=5)
    )
    while pool.stats().busy == 0:
        await asyncio.sleep(0.005)
    return task, gate


async def test_each_worker_loads_its_engine_on_its_own_thread() -> None:
    p: WorkerPool[Engine] = WorkerPool("asr", 3, Engine, max_queue=8)
    await p.start()
    try:
        names = await asyncio.gather(
            *(
                p.run(
                    thread_name_after_a_pause,
                    Priority.RECOGNITION,
                    start_within_s=5,
                )
                for _ in range(3)
            )
        )
        assert len(set(names)) == 3 and all(n.startswith("asr-") for n in names)
    finally:
        await p.stop()


async def test_jobs_return_results_and_errors(pool: WorkerPool[Engine]) -> None:
    assert await pool.run(lambda e: 6 * 7, Priority.RECOGNITION, start_within_s=1) == 42
    with pytest.raises(ZeroDivisionError):
        await pool.run(lambda e: 1 / 0, Priority.RECOGNITION, start_within_s=1)
    # The worker survives a failing job.
    assert await pool.run(lambda e: "ok", Priority.RECOGNITION, start_within_s=1) == "ok"


def record(order: list[str], name: str, engine: Engine) -> None:
    order.append(name)


async def test_higher_priority_runs_first(pool: WorkerPool[Engine]) -> None:
    first, gate = await blocked(pool)
    order: list[str] = []
    jobs = [
        (Priority.BACKGROUND, "next question"),
        (Priority.REST_OF_REPLY, "second sentence"),
        (Priority.RECOGNITION, "recognition"),
        (Priority.FIRST_SENTENCE, "first sentence"),
    ]
    tasks = [
        asyncio.create_task(pool.run(partial(record, order, name), p, start_within_s=5))
        for p, name in jobs
    ]
    await asyncio.sleep(0.02)
    gate.set()
    await asyncio.gather(first, *tasks)
    assert order == ["recognition", "first sentence", "second sentence", "next question"]


async def test_a_full_queue_refuses_at_once(pool: WorkerPool[Engine]) -> None:
    first, gate = await blocked(pool)
    waiting = [
        asyncio.create_task(pool.run(lambda e: None, Priority.BACKGROUND, start_within_s=5))
        for _ in range(4)
    ]
    await asyncio.sleep(0.01)
    with pytest.raises(PoolBusy, match="queue full"):
        await pool.run(lambda e: None, Priority.RECOGNITION, start_within_s=5)
    assert pool.stats().refused == 1
    gate.set()
    await asyncio.gather(first, *waiting)


async def test_a_job_that_cannot_start_in_time_is_refused(pool: WorkerPool[Engine]) -> None:
    first, gate = await blocked(pool)
    late = asyncio.create_task(pool.run(lambda e: "ran", Priority.RECOGNITION, start_within_s=0.01))
    await asyncio.sleep(0.05)
    gate.set()
    with pytest.raises(PoolBusy, match="not started in time"):
        await late
    await first


async def test_a_cancelled_job_is_skipped(pool: WorkerPool[Engine]) -> None:
    first, gate = await blocked(pool)
    ran: list[str] = []
    gone = asyncio.create_task(
        pool.run(lambda e: ran.append("x"), Priority.RECOGNITION, start_within_s=5)
    )
    await asyncio.sleep(0.01)
    gone.cancel()
    gate.set()
    await first
    await pool.run(lambda e: None, Priority.BACKGROUND, start_within_s=5)
    assert ran == []


async def test_stopping_fails_jobs_still_waiting() -> None:
    p: WorkerPool[Engine] = WorkerPool("test", 1, Engine, max_queue=4)
    await p.start()
    first, gate = await blocked(p)
    queued = asyncio.create_task(p.run(lambda e: None, Priority.BACKGROUND, start_within_s=5))
    await asyncio.sleep(0.01)
    gate.set()
    await p.stop()
    with pytest.raises(PoolBusy, match="stopped"):
        await queued
    await asyncio.gather(first, return_exceptions=True)


async def test_workers_run_in_parallel() -> None:
    p: WorkerPool[Engine] = WorkerPool("tts", 2, Engine, max_queue=4)
    await p.start()
    try:
        began = time.perf_counter()
        await asyncio.gather(
            *(
                p.run(lambda e: time.sleep(0.2), Priority.FIRST_SENTENCE, start_within_s=5)
                for _ in range(2)
            )
        )
        assert time.perf_counter() - began < 0.35
        stats = p.stats()
        assert (stats.workers, stats.served, stats.busy, stats.queued) == (2, 2, 0, 0)
        assert 0.15 < stats.mean_job_s < 0.35
    finally:
        await p.stop()


def test_a_pool_needs_a_worker() -> None:
    with pytest.raises(ValueError):
        WorkerPool("x", 0, Engine, max_queue=1)
