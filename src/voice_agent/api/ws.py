"""``/v1/session``: one WebSocket per exam PC for the whole exam.

The first frame must be ``hello`` with a device token issued at set-up; anything else, or
a bad token, closes the connection. The candidate is then identified (``login``) or the
connection resumes a session (``resume_session_id``). A session has at most one
connection: a newer one (the PC reconnected) takes over and the older one is closed.

Outgoing messages come from the session's queue, written by a separate task, so speech
from background work (a sync finishing, a time warning) reaches the PC without waiting
for the candidate to speak.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Final

from fastapi import WebSocket, WebSocketDisconnect

from voice_agent.adapters.base import AdapterRejected, AdapterUnavailable
from voice_agent.contract import (
    SCHEMA_VERSION,
    BadMessage,
    Error,
    ErrorCode,
    Heard,
    Hello,
    KeyPress,
    Login,
    Ping,
    PlaybackDone,
    Pong,
    Say,
    ServerMessage,
    SessionInfo,
    TalkEnd,
    TalkStart,
    TextInput,
    parse_client_message,
)
from voice_agent.runtime.limits import RateLimiter
from voice_agent.session.manager import LiveSession, NotFound, SessionManager
from voice_agent.session.replies import (
    IdentifyPrompt,
    NotRegistered,
    PlatformUnavailable,
    TemplateReply,
    render,
)

log = logging.getLogger(__name__)

HELLO_TIMEOUT_S: Final = 10.0
FRAMES_WINDOW_S: Final = 10.0

CLOSE_PROTOCOL: Final = 4400
CLOSE_UNAUTHORIZED: Final = 4401
CLOSE_SUPERSEDED: Final = 4409


def token_allowed(token: str, digests: Iterable[str]) -> bool:
    """Constant-time check of a device token against the configured SHA-256 digests."""
    digest = hashlib.sha256(token.encode()).hexdigest()
    return any(hmac.compare_digest(digest, allowed) for allowed in digests)


@dataclass
class Hub:
    """Which connection currently owns each session."""

    owners: dict[str, WebSocket] = field(default_factory=dict)

    async def take(self, session_id: str, ws: WebSocket) -> None:
        previous = self.owners.get(session_id)
        self.owners[session_id] = ws
        if previous is not None and previous is not ws:
            with contextlib.suppress(Exception):  # it may already be gone
                await _send(
                    previous, _error(ErrorCode.SUPERSEDED, "Taken over by a newer connection.")
                )
                await previous.close(CLOSE_SUPERSEDED)

    def owns(self, session_id: str, ws: WebSocket) -> bool:
        return self.owners.get(session_id) is ws

    def release(self, session_id: str, ws: WebSocket) -> None:
        if self.owners.get(session_id) is ws:
            del self.owners[session_id]


async def serve(
    ws: WebSocket,
    manager: SessionManager,
    hub: Hub,
    device_tokens: Iterable[str],
    max_messages_per_10s: int,
) -> None:
    await ws.accept()
    hello = await _hello(ws, device_tokens)
    if hello is None:
        return
    connection = _Connection(ws, manager, hub, max_messages_per_10s)
    try:
        await connection.start(hello)
        await connection.run()
    except WebSocketDisconnect:
        pass
    finally:
        connection.detach()


async def _hello(ws: WebSocket, device_tokens: Iterable[str]) -> Hello | None:
    try:
        raw = await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S)
        message = parse_client_message(raw)
    except (TimeoutError, BadMessage, WebSocketDisconnect, KeyError):
        message = None
    if not isinstance(message, Hello):
        await _refuse(ws, ErrorCode.BAD_MESSAGE, "The first message must be hello.", CLOSE_PROTOCOL)
        return None
    if message.schema_version != SCHEMA_VERSION:
        await _refuse(
            ws,
            ErrorCode.UNSUPPORTED_SCHEMA,
            f"This service speaks schema version {SCHEMA_VERSION}.",
            CLOSE_PROTOCOL,
        )
        return None
    if not token_allowed(message.token, device_tokens):
        await _refuse(ws, ErrorCode.UNAUTHORIZED, "Unknown device.", CLOSE_UNAUTHORIZED)
        return None
    return message


async def _refuse(ws: WebSocket, code: ErrorCode, message: str, close_code: int) -> None:
    with contextlib.suppress(Exception):
        await _send(ws, _error(code, message))
        await ws.close(close_code)


class _Connection:
    def __init__(
        self, ws: WebSocket, manager: SessionManager, hub: Hub, max_messages_per_10s: int
    ) -> None:
        self._ws = ws
        self._manager = manager
        self._hub = hub
        self._session: LiveSession | None = None
        self._writer: asyncio.Task[None] | None = None
        self._limiter = RateLimiter(max_messages_per_10s, FRAMES_WINDOW_S)

    async def start(self, hello: Hello) -> None:
        if hello.resume_session_id is not None:
            try:
                await self._attach(await self._manager.resume(hello.resume_session_id), True)
                return
            except NotFound:
                await _send(self._ws, _error(ErrorCode.SESSION_NOT_FOUND, "No such session."))
        await self._say(IdentifyPrompt())

    async def run(self) -> None:
        while True:
            frame = await self._ws.receive()
            if frame["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(frame.get("code", 1000))
            if self._session is not None and not self._hub.owns(self._session.session_id, self._ws):
                return  # superseded; the hub has already told the client and closed
            if not self._limiter.allow("frames"):
                await _send(self._ws, _error(ErrorCode.RATE_LIMITED, "Too many messages."))
                continue
            if frame.get("bytes") is not None:
                # Audio arrives with the speech layer (phase 2); text and keys work now.
                await _send(self._ws, _error(ErrorCode.BAD_MESSAGE, "Audio is not enabled."))
                continue
            try:
                message = parse_client_message(frame.get("text") or "")
            except BadMessage as e:
                await _send(self._ws, _error(ErrorCode.BAD_MESSAGE, str(e)))
                continue
            await self._handle(message)

    async def _handle(self, message: object) -> None:
        match message:
            case Ping():
                await _send(self._ws, Pong())
            case Login(reg_no=reg_no, exam_code=exam_code) if self._session is None:
                await self._login(reg_no, exam_code)
            case TextInput(text=text) if self._session is not None:
                self._session.emit(
                    Heard(text=text, confidence=1.0, seq=self._session.state.seq + 1)
                )
                await self._manager.handle_text(self._session, text)
            case KeyPress(key=key) if self._session is not None:
                await self._manager.handle_key(self._session, key)
            case TextInput() | KeyPress():
                await _send(self._ws, _error(ErrorCode.NOT_IDENTIFIED, "Sign in first."))
            case TalkStart() | TalkEnd() | PlaybackDone():
                pass  # meaningful once audio is enabled (phase 2)
            case _:
                await _send(self._ws, _error(ErrorCode.BAD_MESSAGE, "Not expected now."))

    async def _login(self, reg_no: str, exam_code: str) -> None:
        try:
            session = await self._manager.identify(reg_no, exam_code)
        except AdapterRejected:
            await self._say(NotRegistered())
            return
        except AdapterUnavailable:
            await _send(self._ws, _error(ErrorCode.ADAPTER_UNAVAILABLE, "Platform unreachable."))
            await self._say(PlatformUnavailable())
            return
        await self._attach(session, session.resumed)

    async def _attach(self, session: LiveSession, resumed: bool) -> None:
        await self._hub.take(session.session_id, self._ws)
        self._session = session
        await _send(self._ws, SessionInfo(session_id=session.session_id, resumed=resumed))
        self._writer = asyncio.create_task(self._write(session))

    async def _write(self, session: LiveSession) -> None:
        queue = session.output  # replaced on the next resume, which ends this writer
        with contextlib.suppress(Exception):  # the socket closing ends the writer too
            while self._hub.owns(session.session_id, self._ws):
                message = await queue.get()
                await _send(self._ws, message)

    async def _say(self, reply: TemplateReply) -> None:
        for n, spoken in enumerate(render(reply)):
            await _send(self._ws, Say(utterance_id=f"pre.{n}", text=spoken.text, kind=spoken.kind))

    def detach(self) -> None:
        if self._writer is not None:
            self._writer.cancel()
        if self._session is not None:
            self._hub.release(self._session.session_id, self._ws)


def _error(code: ErrorCode, message: str) -> Error:
    return Error(code=code, message=message)


async def _send(ws: WebSocket, message: ServerMessage) -> None:
    await ws.send_text(message.model_dump_json())
