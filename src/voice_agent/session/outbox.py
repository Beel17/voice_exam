"""Confirmed writes waiting for the platform.

A write enters the outbox in the same transaction as the event that confirmed it, so it
survives a crash or power cut. It leaves only when the platform has answered (saved or
refused). Writes for a session are delivered strictly in the order they were confirmed:
an old answer must never reach the platform after a newer one for the same question.
"""

import asyncio
from dataclasses import dataclass
from typing import Literal

from voice_agent.adapters.base import AdapterRejected, AdapterUnavailable, ExamDataAdapter
from voice_agent.contract import OptionLetter
from voice_agent.session.machine import Effect, SaveAnswer, SubmitExam, WriteOutcome


@dataclass(frozen=True, slots=True)
class OutboxItem:
    operation_id: str
    session_id: str
    candidate_id: str
    kind: Literal["save", "submit"]
    question: int | None
    choice: OptionLetter | None
    attempts: int = 0


def items_for(effects: tuple[Effect, ...], session_id: str, candidate_id: str) -> list[OutboxItem]:
    """The outbox rows a step's effects create; stored with the step's event."""
    items: list[OutboxItem] = []
    for effect in effects:
        match effect:
            case SaveAnswer(question=n, choice=choice, operation_id=op):
                items.append(OutboxItem(op, session_id, candidate_id, "save", n, choice))
            case SubmitExam(operation_id=op):
                items.append(OutboxItem(op, session_id, candidate_id, "submit", None, None))
    return items


async def deliver(
    item: OutboxItem, adapter: ExamDataAdapter, timeout_s: float
) -> WriteOutcome | None:
    """Send one write. ``None`` means the platform did not answer; try again later."""
    if item.kind == "save":
        assert item.question is not None
        call = adapter.save_answer(item.candidate_id, item.question, item.choice, item.operation_id)
    else:
        call = adapter.submit(item.candidate_id, item.operation_id)
    try:
        await asyncio.wait_for(call, timeout_s)
    except (AdapterUnavailable, TimeoutError):
        return None
    except AdapterRejected as e:
        return WriteOutcome(operation_id=item.operation_id, outcome="rejected", reason=e.reason)
    return WriteOutcome(operation_id=item.operation_id, outcome="saved")
