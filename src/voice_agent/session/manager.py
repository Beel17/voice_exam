"""Live sessions: identification, carrying out the machine's effects, reconnect and resume.

This is where the pure exam script meets the world. For each event the manager steps the
machine, writes the event (and any outbox rows it creates) to the store, and only then
carries out the effects, so nothing the candidate hears can run ahead of what is logged.

One lock per session makes its events strictly sequential, which is what lets the log
replay exactly. Different sessions run concurrently.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from voice_agent.adapters.base import AdapterError, AdapterUnavailable, ExamDataAdapter, Question
from voice_agent.contract import Key, Say, ServerMessage
from voice_agent.session import machine as m
from voice_agent.session.events import SessionRecord, SessionStore
from voice_agent.session.outbox import deliver, items_for
from voice_agent.session.replies import QuestionNotAvailable, Reply, needs_question, render
from voice_agent.understanding import grammar
from voice_agent.understanding import intents as i
from voice_agent.understanding.intents import Intent, action_for_key
from voice_agent.understanding.llm import FallbackReason, LlmContext, LlmFallback, Understander

# Enough for any one turn; a client that stops reading loses the oldest messages, never
# blocks the session.
OUTPUT_QUEUE_SIZE: Final = 256


class NotFound(Exception):
    """No session with that ID."""


@dataclass(eq=False)
class LiveSession:
    record: SessionRecord
    state: m.SessionState
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    output: asyncio.Queue[ServerMessage] = field(
        default_factory=lambda: asyncio.Queue(OUTPUT_QUEUE_SIZE)
    )
    time_left_s: int | None = None
    warned: set[int] = field(default_factory=set)
    # False only for a session this login has just created.
    resumed: bool = False

    @property
    def session_id(self) -> str:
        return self.record.session_id

    def emit(self, message: ServerMessage) -> None:
        """Queue a message for the client; a client that stops reading loses the oldest."""
        if self.output.full():
            self.output.get_nowait()
        self.output.put_nowait(message)

    def drain(self) -> list[ServerMessage]:
        """Everything queued for the client so far."""
        messages: list[ServerMessage] = []
        while not self.output.empty():
            messages.append(self.output.get_nowait())
        return messages


class SessionManager:
    def __init__(
        self,
        store: SessionStore,
        adapter: ExamDataAdapter,
        *,
        adapter_timeout_s: float = 2.0,
        time_warnings_min: Sequence[int] = (15, 5),
        max_utterance_chars: int = 300,
        understander: Understander | None = None,
        new_session_id: Callable[[], str] = lambda: uuid.uuid4().hex,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._adapter = adapter
        self._timeout = adapter_timeout_s
        self._max_utterance_chars = max_utterance_chars
        self._understander = understander
        self._warnings = tuple(sorted(time_warnings_min, reverse=True))
        self._new_id = new_session_id
        self._clock = clock
        self._live: dict[str, LiveSession] = {}
        self._loading = asyncio.Lock()
        # Questions are the same for every candidate in an exam; fetch each once.
        self._questions: dict[tuple[str, int], Question] = {}

    @property
    def live_count(self) -> int:
        return len(self._live)

    # --- Starting and resuming --------------------------------------------------------

    async def identify(self, reg_no: str, exam_code: str) -> LiveSession:
        """Verify the candidate with the platform; resume their session or start one.

        Raises ``AdapterRejected`` (not registered) or ``AdapterUnavailable``.
        """
        verified = await self._call(self._adapter.verify_candidate(reg_no, exam_code))
        # Held while finding or creating, so two logins for one candidate (say, a PC that
        # reconnects mid-login) can never start two sessions.
        async with self._loading:
            existing = await self._store.find_session(verified.candidate_id, verified.exam_id)
            if existing is None:
                created = await self._create(verified.candidate_id, verified.exam_id)
        if existing is not None:
            return await self.resume(existing.session_id)
        session, effects = created
        async with session.lock:
            await self._run(session, effects)
            self._emit_state(session)
        return session

    async def _create(
        self, candidate_id: str, exam_id: str
    ) -> tuple[LiveSession, tuple[m.Effect, ...]]:
        outline = await self._call(self._adapter.get_exam_outline(exam_id))
        saved = await self._call(self._adapter.get_saved_answers(candidate_id))
        record = SessionRecord(
            session_id=self._new_id(),
            candidate_id=candidate_id,
            exam_id=exam_id,
            created_at=self._clock(),
        )
        started = m.Started(
            session_id=record.session_id,
            candidate_id=record.candidate_id,
            exam_id=record.exam_id,
            question_count=outline.question_count,
            sections=outline.sections,
            duration_s=outline.duration_s,
            saved=saved.answers,
        )
        state, effects = m.start(started)
        await self._store.create_session(record, started)
        session = LiveSession(record, state)
        self._live[record.session_id] = session
        return session, effects

    async def resume(self, session_id: str) -> LiveSession:
        """Reconnect to a session, rebuilding it from its log if this process lost it."""
        session = await self._get(session_id)
        session.resumed = True
        # A fresh queue: whatever was waiting for the old connection is stale now.
        session.output = asyncio.Queue(OUTPUT_QUEUE_SIZE)
        async with session.lock:
            await self._feed(session, m.Resumed())
            self._emit_state(session)
        return session

    async def _get(self, session_id: str) -> LiveSession:
        if (session := self._live.get(session_id)) is not None:
            return session
        async with self._loading:  # two reconnects at once must not both replay
            if (session := self._live.get(session_id)) is not None:
                return session
            record = await self._store.get_session(session_id)
            if record is None:
                raise NotFound(session_id)
            stored = await self._store.load(session_id)
            events = [s.event for s in stored]
            session = LiveSession(record, m.replay(events))
            session.warned = self._already_warned(events)
            self._live[session_id] = session
            return session

    def _already_warned(self, events: Iterable[m.Event]) -> set[int]:
        spoken = [e.minutes for e in events if isinstance(e, m.TimeWarning)]
        # A warning for 5 minutes means the 15-minute one is past too.
        return {t for t in self._warnings if spoken and t >= min(spoken)}

    # --- Candidate input --------------------------------------------------------------

    async def handle_text(self, session: LiveSession, text: str) -> None:
        """A sentence the candidate typed or said: rules first, then the language model."""
        text = " ".join(text.split())
        async with session.lock:
            began = time.perf_counter()
            intent, source, fallback = await self._understand(session, text)
            latency_ms = round((time.perf_counter() - began) * 1000)
            await self._understood(
                session, intent, source, text, latency_ms=latency_ms, fallback=fallback
            )

    async def _understand(
        self, session: LiveSession, text: str
    ) -> tuple[Intent, m.Source, FallbackReason | Literal["disabled"] | None]:
        if len(text) > self._max_utterance_chars:
            return i.Unclear(), m.Source.RULES, None
        if (intent := grammar.match(text, self._rules_context(session))) is not None:
            return intent, m.Source.RULES, None
        if self._understander is None:
            return i.Unclear(), m.Source.RULES, "disabled"
        try:
            intent = await self._understander.understand(
                session.session_id, text, self._llm_context(session)
            )
        except LlmFallback as e:
            return i.Unclear(), m.Source.RULES, e.reason
        return intent, m.Source.LLM, None

    def _llm_context(self, session: LiveSession) -> LlmContext:
        state = session.state
        pending = m.snapshot(state, None).pending_confirm
        return LlmContext(
            question=state.current,
            of=state.question_count,
            option_count=state.option_count,
            awaiting=pending.kind if pending else None,
        )

    async def handle(
        self, session: LiveSession, intent: Intent, source: m.Source, text: str | None = None
    ) -> None:
        async with session.lock:
            await self._understood(session, intent, source, text)

    async def _understood(
        self,
        session: LiveSession,
        intent: Intent,
        source: m.Source,
        text: str | None,
        *,
        latency_ms: int | None = None,
        fallback: FallbackReason | Literal["disabled"] | None = None,
    ) -> None:
        if text is not None:
            text = text[: self._max_utterance_chars]  # the audit log keeps what was used
        event = m.Understood(
            intent=intent, source=source, text=text, latency_ms=latency_ms, fallback=fallback
        )
        await self._feed(session, event)
        self._emit_state(session)

    def _rules_context(self, session: LiveSession) -> grammar.RulesContext:
        state = session.state
        question = (
            self._questions.get((session.record.exam_id, state.current))
            if state.option_count is not None
            else None
        )
        options = (
            tuple((o.text,) + ((o.spoken_text,) if o.spoken_text else ()) for o in question.options)
            if question
            else ()
        )
        return grammar.RulesContext(
            option_texts=options, section_titles=tuple(s.title for s in state.sections)
        )

    async def handle_key(self, session: LiveSession, key: Key) -> None:
        await self.handle(session, action_for_key(key), m.Source.KEY, text=key)

    # --- Background passes (called periodically by the runtime) -----------------------

    async def retry_outbox(self) -> None:
        """Try again every write the platform has not acknowledged, oldest first."""
        session_ids = list(dict.fromkeys(i.session_id for i in await self._store.pending_writes()))
        for session_id in session_ids:
            session = await self._get(session_id)
            async with session.lock:
                await self._flush(session)

    async def check_time(self) -> None:
        """Ask the platform for each live session's time; warn, or end the exam at zero."""
        for session in list(self._live.values()):
            async with session.lock:
                if session.state.phase is m.Phase.ENDED:
                    continue
                seconds = await self._time_left(session)
                if seconds is None:
                    continue
                if seconds == 0:
                    await self._feed(session, m.TimeUp())
                else:
                    crossed = {t for t in self._warnings if seconds <= t * 60} - session.warned
                    if crossed:
                        session.warned |= crossed
                        await self._feed(session, m.TimeWarning(minutes=min(crossed)))
                self._emit_state(session)

    # --- Stepping and effects ---------------------------------------------------------

    async def _feed(self, session: LiveSession, event: m.Event) -> None:
        """Step, persist, then act. Caller holds the session lock."""
        state, effects = m.step(session.state, event)
        record = session.record
        settle = None
        if isinstance(event, m.WriteOutcome) and event.outcome != "queued":
            settle = (event.operation_id, event.outcome)
        await self._store.append(
            record.session_id,
            state.seq,
            event,
            enqueue=items_for(effects, record.session_id, record.candidate_id),
            settle=settle,
        )
        session.state = state
        await self._run(session, effects)

    async def _run(self, session: LiveSession, effects: Iterable[m.Effect]) -> None:
        seq = session.state.seq
        for index, effect in enumerate(effects):
            match effect:
                case m.Load(question=n):
                    question = await self._question(session.record.exam_id, n)
                    if question is None:
                        await self._feed(session, m.QuestionUnavailable(question=n))
                    else:
                        loaded = m.QuestionLoaded(question=n, option_count=len(question.options))
                        await self._feed(session, loaded)
                case m.Speak(reply=reply):
                    await self._speak(session, reply, f"{seq}.{index}")
                case m.SaveAnswer(operation_id=op) | m.SubmitExam(operation_id=op):
                    await self._flush(session)
                    if session.state.awaiting == op:  # not delivered: held in the outbox
                        await self._feed(session, m.WriteOutcome(operation_id=op, outcome="queued"))
                case m.FetchTimeLeft():
                    seconds = await self._time_left(session)
                    await self._feed(session, m.TimeLeftKnown(seconds=seconds))

    async def _flush(self, session: LiveSession) -> None:
        """Deliver this session's pending writes in order; stop at the first that fails."""
        for item in await self._store.pending_writes(session.session_id):
            await self._store.record_attempt(item.operation_id)
            outcome = await deliver(item, self._adapter, self._timeout)
            if outcome is None:
                return
            await self._feed(session, outcome)

    async def _speak(self, session: LiveSession, reply: Reply, utterance_id: str) -> None:
        question = None
        if (n := needs_question(reply)) is not None:
            question = await self._question(session.record.exam_id, n)
            if question is None:
                reply = QuestionNotAvailable(n)
        for part, spoken in enumerate(render(reply, question)):
            say = Say(utterance_id=f"{utterance_id}.{part}", text=spoken.text, kind=spoken.kind)
            session.emit(say)

    async def _question(self, exam_id: str, n: int) -> Question | None:
        key = (exam_id, n)
        if (cached := self._questions.get(key)) is not None:
            return cached
        try:
            question = await self._call(self._adapter.get_question(exam_id, n))
        except AdapterError:
            return None
        self._questions[key] = question
        return question

    async def _time_left(self, session: LiveSession) -> int | None:
        try:
            seconds = await self._call(self._adapter.get_time_left(session.record.candidate_id))
        except AdapterError:
            return None
        session.time_left_s = seconds
        return seconds

    async def _call[T](self, awaitable: Awaitable[T]) -> T:
        # A platform that does not answer in time is treated exactly like one that is down.
        try:
            return await asyncio.wait_for(awaitable, self._timeout)
        except TimeoutError:
            raise AdapterUnavailable("platform did not answer in time") from None

    def _emit_state(self, session: LiveSession) -> None:
        session.emit(m.snapshot(session.state, session.time_left_s))
