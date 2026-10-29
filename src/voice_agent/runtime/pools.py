"""Bounded worker pools for CPU work (recognition, synthesis), with priorities.

Each worker owns one thread and its own engine (a recogniser or a voice), loaded once
when the pool starts, so engines never share state and never compete for one core:
the pool size is the pool's share of the machine. Jobs carry a priority (the candidate
waiting on a recognition comes before the second sentence of a reply) and the queue is
bounded: a job that cannot be queued, or does not start before its deadline, is refused
at once with ``PoolBusy`` so the caller can degrade, rather than waiting silently.

Workers are threads, not processes: the engines are native code that releases the GIL.
A crash inside native code would take the service down; process isolation is a phase 5
question, decided by the failure drills.
"""

import asyncio
import itertools
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class Priority(IntEnum):
    """Lower runs first (design doc, "How parallel requests are handled")."""

    RECOGNITION = 0  # the candidate has released the talk key and is waiting
    FIRST_SENTENCE = 1  # the start of a reply: what the candidate hears first
    REST_OF_REPLY = 2
    BACKGROUND = 3  # preparing the next question, warming caches


class PoolBusy(Exception):
    """The pool cannot take or start this job in time; degrade instead of waiting."""


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    deadline: float = field(compare=False)
    run: Callable[[Any], Any] = field(compare=False)
    future: "asyncio.Future[Any]" = field(compare=False)


@dataclass(frozen=True, slots=True)
class PoolStats:
    workers: int
    busy: int
    queued: int
    served: int
    refused: int
    mean_job_s: float


class WorkerPool[S]:
    def __init__(
        self,
        name: str,
        workers: int,
        make_state: Callable[[], S],
        *,
        max_queue: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if workers < 1:
            raise ValueError("a pool needs at least one worker")
        self.name = name
        self._size = workers
        self._make_state = make_state
        self._max_queue = max_queue
        self._clock = clock
        self._queue: asyncio.PriorityQueue[_Job] = asyncio.PriorityQueue()
        self._seq = itertools.count()
        self._threads: list[ThreadPoolExecutor] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._busy = 0
        self._served = 0
        self._refused = 0
        self._mean_job_s = 0.0

    async def start(self) -> None:
        """Start the workers, loading each one's engine on its own thread, all at once:
        loading a speech model takes seconds, and workers should not wait in line."""
        loop = asyncio.get_running_loop()
        self._threads = [
            ThreadPoolExecutor(1, thread_name_prefix=f"{self.name}-{k}") for k in range(self._size)
        ]
        states = await asyncio.gather(
            *(loop.run_in_executor(thread, self._make_state) for thread in self._threads)
        )
        for thread, state in zip(self._threads, states, strict=True):
            self._tasks.append(asyncio.create_task(self._work(thread, state)))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        while not self._queue.empty():  # nobody may be left waiting on a stopped pool
            job = self._queue.get_nowait()
            if not job.future.done():
                job.future.set_exception(PoolBusy(f"{self.name}: stopped"))
        for thread in self._threads:
            thread.shutdown(wait=True)
        self._tasks.clear()
        self._threads.clear()

    async def run[R](
        self, job: Callable[[S], R], priority: Priority, *, start_within_s: float
    ) -> R:
        """Run ``job(engine)`` on a worker. Raises ``PoolBusy`` if the queue is full or the
        job has not started within ``start_within_s``; once started, it runs to the end."""
        if self._queue.qsize() >= self._max_queue:
            self._refused += 1
            raise PoolBusy(f"{self.name}: queue full")
        future: asyncio.Future[R] = asyncio.get_running_loop().create_future()
        deadline = self._clock() + start_within_s
        self._queue.put_nowait(_Job(priority, next(self._seq), deadline, job, future))
        return await future

    def stats(self) -> PoolStats:
        return PoolStats(
            workers=self._size,
            busy=self._busy,
            queued=self._queue.qsize(),
            served=self._served,
            refused=self._refused,
            mean_job_s=self._mean_job_s,
        )

    async def _work(self, thread: ThreadPoolExecutor, state: S) -> None:
        loop = asyncio.get_running_loop()
        while True:
            job = await self._queue.get()
            if job.future.done():  # the caller gave up (cancelled) while it waited
                continue
            if self._clock() > job.deadline:
                self._refused += 1
                job.future.set_exception(PoolBusy(f"{self.name}: not started in time"))
                continue
            self._busy += 1
            began = self._clock()
            try:
                result = await loop.run_in_executor(thread, job.run, state)
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.set_exception(PoolBusy(f"{self.name}: stopped"))
                raise
            except Exception as e:  # the job's own failure belongs to its caller
                if not job.future.done():
                    job.future.set_exception(e)
            else:
                if not job.future.done():
                    job.future.set_result(result)
            finally:
                self._busy -= 1
                took = self._clock() - began
                self._served += 1
                # A smoothed average, so one slow job does not swing degradation.
                weight = 1.0 if self._served == 1 else 0.2
                self._mean_job_s += (took - self._mean_job_s) * weight
