"""HTTP endpoints: health for the installer and invigilators, status and audit for admins."""

import os
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict

from voice_agent.api.runtime import Runtime
from voice_agent.api.ws import token_allowed
from voice_agent.session.manager import NotFound
from voice_agent.speech import cues
from voice_agent.speech.cues import CUE_NAMES

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)


def runtime(request: Request) -> Runtime:
    rt: Runtime = request.app.state.runtime
    return rt


def device(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Runtime:
    """An exam PC: the device token it was issued at set-up."""
    rt = runtime(request)
    if credentials is None or not token_allowed(
        credentials.credentials, rt.settings.device_token_sha256
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "device token required")
    return rt


def admin(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Runtime:
    rt = runtime(request)
    if credentials is None or not token_allowed(
        credentials.credentials, rt.settings.admin_token_sha256
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin token required")
    return rt


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Health(_Body):
    status: Literal["ok", "degraded"]
    language_model: Literal["running", "down", "disabled"]
    speech: Literal["running", "down", "disabled"]
    voice: Literal["running", "down", "disabled"]


class Status(_Body):
    mode: Literal["pc", "server"]
    # For monitoring tools and the benchmark (a launcher's PID is not the service's).
    process_id: int
    live_sessions: int
    connected: int
    unsynced_writes: int
    language_model: Literal["running", "down", "disabled"]
    language_model_restarts: int
    speech: Literal["running", "down", "disabled"]
    voice: Literal["running", "down", "disabled"]
    # 0 normal, 1 rules only, 2 fixed prompts, 3 keyboard (see runtime/degrade.py).
    degradation_level: int


class Overview(_Body):
    """The service at a glance, for the invigilators' status page. Counts and states
    only: nothing that identifies a candidate, so it needs no token."""

    status: Literal["ok", "degraded"]
    mode: Literal["pc", "server"]
    up_since: float  # Unix time
    now: float
    connected: int  # exam PCs connected now
    live_sessions: int
    # Answers and submissions confirmed by candidates but not yet accepted by the
    # platform. Sent automatically; the one number an invigilator must watch.
    unsynced_writes: int
    language_model: Literal["running", "down", "disabled"]
    language_model_problem: str | None
    language_model_restarts: int
    speech: Literal["running", "down", "disabled"]
    voice: Literal["running", "down", "disabled"]
    degradation_level: int


class AuditEvent(_Body):
    seq: int
    at: float
    kind: str
    event: dict[str, object]


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(rt: Annotated[Runtime, Depends(runtime)]) -> Health:
    model = rt.language_model_state()
    speech, voice = rt.speech_state(), rt.voice_state()
    healthy = "down" not in (model, speech, voice)
    return Health(
        status="ok" if healthy else "degraded", language_model=model, speech=speech, voice=voice
    )


@router.get("/v1/overview")
async def overview(rt: Annotated[Runtime, Depends(runtime)]) -> Overview:
    model, speech, voice = rt.language_model_state(), rt.speech_state(), rt.voice_state()
    return Overview(
        status="ok" if "down" not in (model, speech, voice) else "degraded",
        mode=rt.settings.mode,
        up_since=rt.started_at,
        now=time.time(),
        connected=len(rt.hub.owners),
        live_sessions=rt.manager.live_count,
        unsynced_writes=len(await rt.store.pending_writes()),
        language_model=model,
        language_model_problem=rt.language_model_problem(),
        language_model_restarts=rt.llm_server.restarts if rt.llm_server else 0,
        speech=speech,
        voice=voice,
        degradation_level=rt.manager.level,
    )


@router.get("/v1/cues/{name}.wav")
async def cue(name: str) -> Response:
    """A sound cue, for the PC to fetch once and play locally."""
    if name not in CUE_NAMES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such cue")
    return Response(
        content=cues.wav(name),
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/v1/sessions/{session_id}/photo")
async def photo(session_id: str, rt: Annotated[Runtime, Depends(device)]) -> Response:
    """The candidate's photo, for the identity check on their own exam PC."""
    try:
        data = await rt.manager.photo(session_id)
    except NotFound:
        data = None
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no photo")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/v1/status")
async def status_(rt: Annotated[Runtime, Depends(admin)]) -> Status:
    model = rt.language_model_state()
    return Status(
        mode=rt.settings.mode,
        process_id=os.getpid(),
        live_sessions=rt.manager.live_count,
        connected=len(rt.hub.owners),
        unsynced_writes=len(await rt.store.pending_writes()),
        language_model=model,
        language_model_restarts=rt.llm_server.restarts if rt.llm_server else 0,
        speech=rt.speech_state(),
        voice=rt.voice_state(),
        degradation_level=rt.manager.level,
    )


@router.get("/v1/sessions/{session_id}/audit")
async def audit(session_id: str, rt: Annotated[Runtime, Depends(admin)]) -> list[AuditEvent]:
    if await rt.store.get_session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such session")
    return [
        AuditEvent(seq=e.seq, at=e.at, kind=e.event.kind, event=e.event.model_dump(mode="json"))
        for e in await rt.store.load(session_id)
    ]
