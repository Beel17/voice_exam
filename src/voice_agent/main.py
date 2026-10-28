"""The app factory: loads everything once at start-up and runs the background passes."""

import asyncio
import logging
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from fastapi import FastAPI, WebSocket

from voice_agent import selfcheck
from voice_agent.adapters.base import ExamDataAdapter
from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.api import http, ws
from voice_agent.api.runtime import Runtime
from voice_agent.config import Settings
from voice_agent.session.events import SessionStore
from voice_agent.session.manager import SessionManager
from voice_agent.understanding.llm import LlamaServer, LlmUnderstander, llama_server_command

log = logging.getLogger(__name__)

Work = Callable[[], Awaitable[None]]

OUTBOX_EVERY_S: Final = 5.0
TIME_EVERY_S: Final = 30.0
MODEL_CHECK_EVERY_S: Final = 5.0


def create_app(settings: Settings, *, adapter: ExamDataAdapter | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = await SessionStore.open(settings.data_dir / "sessions.db")
        llm_server, understander = await _start_language_model(settings)
        manager = SessionManager(
            store,
            adapter or _adapter(settings),
            adapter_timeout_s=settings.adapter.timeout_s,
            time_warnings_min=settings.time_warnings_min,
            max_utterance_chars=settings.limits.max_utterance_chars,
            understander=understander,
        )
        app.state.runtime = Runtime(settings, store, manager, ws.Hub(), llm_server)
        background = [
            asyncio.create_task(_every(OUTBOX_EVERY_S, manager.retry_outbox)),
            asyncio.create_task(_every(TIME_EVERY_S, manager.check_time)),
        ]
        if llm_server is not None and understander is not None:
            background.append(
                asyncio.create_task(
                    _every(MODEL_CHECK_EVERY_S, _keep_model(llm_server, understander))
                )
            )
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            if understander is not None:
                await understander.aclose()
            if llm_server is not None:
                await llm_server.stop()
            await store.close()

    # No interactive docs: they load assets from the internet, and the service is offline.
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(http.router)

    @app.websocket("/v1/session")
    async def session(websocket: WebSocket) -> None:
        rt: Runtime = websocket.app.state.runtime
        await ws.serve(
            websocket,
            rt.manager,
            rt.hub,
            rt.settings.device_token_sha256,
            rt.settings.limits.max_messages_per_10s,
        )

    return app


def _adapter(settings: Settings) -> ExamDataAdapter:
    match settings.adapter.kind:
        case "fake":
            return FakeAdapter.with_bundled_fixtures(
                load_candidates=settings.adapter.load_candidates
            )


async def _start_language_model(
    settings: Settings,
) -> tuple[LlamaServer | None, LlmUnderstander | None]:
    """Start and warm the model server. If it cannot start, run on rules only: the exam
    never depends on the language model."""
    cfg = settings.llm
    if not cfg.enabled:
        return None, None
    binary = shutil.which(str(cfg.server_binary))  # finds llama-server.exe on Windows too
    if binary is None or not cfg.model_file.is_file():
        log.warning(
            "language model files not found (%s, %s); running on rules only",
            cfg.server_binary,
            cfg.model_file,
        )
        return None, None
    began = time.perf_counter()
    problems = await asyncio.to_thread(
        selfcheck.model_file_problems, settings.models_dir, Path(binary), cfg.model_file
    )
    if problems:
        for problem in problems:
            log.error("model check failed: %s", problem)
        log.error("language model not used; running on rules only")
        return None, None
    log.info("model files verified in %.1f s", time.perf_counter() - began)
    command = llama_server_command(
        Path(binary),
        cfg.model_file,
        port=cfg.port,
        threads=cfg.threads,
        slots=cfg.slots,
        context_per_slot=cfg.context_per_slot,
    )
    server = LlamaServer(command, port=cfg.port, log_path=settings.data_dir / "llama-server.log")
    understander = LlmUnderstander(
        server.base_url,
        timeout_s=cfg.timeout_s,
        slots=cfg.slots,
        max_waiting=cfg.max_waiting,
        max_calls_per_minute=cfg.max_calls_per_minute,
    )
    try:
        await server.start()
        await understander.warm()
    except Exception:
        log.exception("language model unavailable; running on rules only")
    return server, understander


def _keep_model(server: LlamaServer, understander: LlmUnderstander) -> Work:
    async def check() -> None:
        if await server.ensure_running():
            log.warning("language model restarted (%d restarts)", server.restarts)
            await understander.warm()

    return check


async def _every(seconds: float, work: Work) -> None:
    """Run ``work`` forever at an interval; one failure never stops the next run."""
    while True:
        await asyncio.sleep(seconds)
        try:
            await work()
        except Exception:
            log.exception("background %s failed", getattr(work, "__name__", "work"))
