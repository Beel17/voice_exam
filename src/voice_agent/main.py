"""The app factory: loads everything once at start-up and runs the background passes."""

import asyncio
import logging
import mimetypes
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from fastapi import FastAPI, WebSocket
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from voice_agent import selfcheck
from voice_agent.adapters.base import ExamDataAdapter
from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.api import http, ws
from voice_agent.api.runtime import Runtime
from voice_agent.config import Settings
from voice_agent.examstore.adapter import LocalAdapter
from voice_agent.examstore.store import ExamStore
from voice_agent.runtime.degrade import Degrader, Load
from voice_agent.session.events import SessionStore
from voice_agent.session.manager import SessionManager
from voice_agent.speech.stt import MODEL_FILES, Recognizer, SherpaRecognizer
from voice_agent.speech.tts import SherpaSynthesizer, Synthesizer
from voice_agent.understanding.llm import (
    LlamaServer,
    LlmUnderstander,
    ModelServerFailed,
    cpu_can_run_model,
    llama_server_command,
)

log = logging.getLogger(__name__)

Work = Callable[[], Awaitable[None]]

OUTBOX_EVERY_S: Final = 5.0
TIME_EVERY_S: Final = 30.0
MODEL_CHECK_EVERY_S: Final = 5.0
LOAD_CHECK_EVERY_S: Final = 1.0
CLIENT_DIR: Final = Path(__file__).parent / "client"


def create_app(
    settings: Settings,
    *,
    adapter: ExamDataAdapter | None = None,
    recognizer: Recognizer | None = None,
    synthesizer: Synthesizer | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = await SessionStore.open(settings.data_dir / "sessions.db")
        platform, close_platform = (adapter, _nothing) if adapter else _adapter(settings)
        llm_server, understander, model_note = await _start_language_model(settings)
        owned_recognizer = None if recognizer else await _start_recognizer(settings)
        owned_synthesizer = None if synthesizer else await _start_synthesizer(settings)
        manager = SessionManager(
            store,
            platform,
            adapter_timeout_s=settings.adapter.timeout_s,
            time_warnings_min=settings.time_warnings_min,
            max_utterance_chars=settings.limits.max_utterance_chars,
            understander=understander,
            practice=adapter is None and settings.adapter.kind == "fake",
        )
        runtime = Runtime(
            settings,
            store,
            manager,
            ws.Hub(),
            llm_server,
            recognizer or owned_recognizer,
            synthesizer or owned_synthesizer,
            model_note=model_note,
        )
        app.state.runtime = runtime
        watch_load = _watch_load(runtime, Degrader())
        await watch_load()  # the level is right from the first connection
        background = [
            asyncio.create_task(_every(OUTBOX_EVERY_S, manager.retry_outbox)),
            asyncio.create_task(_every(TIME_EVERY_S, manager.check_time)),
            asyncio.create_task(_every(LOAD_CHECK_EVERY_S, watch_load)),
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
            if owned_recognizer is not None:
                await owned_recognizer.stop()
            if owned_synthesizer is not None:
                await owned_synthesizer.stop()
            await store.close()
            close_platform()

    # No interactive docs: they load assets from the internet, and the service is offline.
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(http.router)
    # The voice client for the exam PC, served by the service itself for testing until the
    # centres' secure browser is known. Windows can map .js to text/plain from its registry,
    # and browsers refuse to run a module served that way.
    mimetypes.add_type("text/javascript", ".js")
    app.mount("/client", StaticFiles(directory=CLIENT_DIR, html=True), name="client")

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/client/")

    @app.get("/status", include_in_schema=False)
    async def status_page() -> RedirectResponse:
        return RedirectResponse("/client/status.html")

    @app.websocket("/v1/session")
    async def session(websocket: WebSocket) -> None:
        rt: Runtime = websocket.app.state.runtime
        await ws.serve(
            websocket,
            rt.manager,
            rt.hub,
            rt.settings.device_token_sha256,
            rt.settings.limits.max_messages_per_10s,
            rt.recognizer,
            rt.synthesizer,
        )

    return app


def _nothing() -> None:
    pass


def _adapter(settings: Settings) -> tuple[ExamDataAdapter, Callable[[], None]]:
    """The platform, and how to let go of it at shutdown."""
    match settings.adapter.kind:
        case "fake":
            fake = FakeAdapter.with_bundled_fixtures(
                load_candidates=settings.adapter.load_candidates,
                state_dir=settings.adapter.state_dir,
            )
            return fake, _nothing
        case "local":
            store = ExamStore(settings.data_dir / "exams.db")
            return LocalAdapter(store), store.close


async def _start_recognizer(settings: Settings) -> SherpaRecognizer | None:
    """Verify, load and warm the recogniser. If that fails, speech is down and the service
    runs in keyboard mode; the exam itself never depends on speech."""
    cfg = settings.speech
    if not cfg.enabled:
        return None
    files = [cfg.recognizer_dir / name for name in MODEL_FILES]
    problems = await asyncio.to_thread(selfcheck.file_problems, settings.models_dir, files)
    if problems:
        for problem in problems:
            log.error("speech check failed: %s", problem)
        log.error("speech recognition not available; keyboard only")
        return None
    recognizer = SherpaRecognizer(
        cfg.recognizer_dir,
        workers=cfg.recognition_workers,
        max_queue=cfg.recognition_queue,
        start_within_s=cfg.recognition_start_within_s,
    )
    try:
        await recognizer.start()
    except Exception:
        log.exception("speech recognition failed to start; keyboard only")
        await recognizer.stop()
        return None
    return recognizer


async def _start_synthesizer(settings: Settings) -> SherpaSynthesizer | None:
    """Verify, load and warm the voice, rendering the fixed prompts. If that fails, speech
    is down; the exam text still reaches the PC for its screen reader or braille display."""
    cfg = settings.speech
    if not cfg.enabled:
        return None
    files = sorted(p for p in cfg.voice_dir.rglob("*") if p.is_file())
    problems = await asyncio.to_thread(selfcheck.file_problems, settings.models_dir, files)
    if not files or problems:
        for problem in problems or [f"{cfg.voice_dir} has no voice files"]:
            log.error("voice check failed: %s", problem)
        return None
    synthesizer = SherpaSynthesizer(
        cfg.voice_dir,
        workers=cfg.synthesis_workers,
        max_queue=cfg.synthesis_queue,
        start_within_s=cfg.synthesis_start_within_s,
    )
    try:
        await synthesizer.start()
    except Exception:
        log.exception("speech synthesis failed to start")
        await synthesizer.stop()
        return None
    log.info("voice: %s", cfg.voice_dir.name)
    return synthesizer


async def _start_language_model(
    settings: Settings,
) -> tuple[LlamaServer | None, LlmUnderstander | None, str | None]:
    """Start and warm the model server. If it cannot start, run on rules only: the exam
    never depends on the language model. The third value says why it is not used, if it
    is not, for the status page."""
    cfg = settings.llm
    if not cfg.enabled:
        return None, None, "switched off in the configuration"
    binary = shutil.which(str(cfg.server_binary))  # finds llama-server.exe on Windows too
    if binary is None or not cfg.model_file.is_file():
        log.warning(
            "language model files not found (%s, %s); running on rules only",
            cfg.server_binary,
            cfg.model_file,
        )
        return None, None, "its files are missing"
    if not cpu_can_run_model():
        # On x86 the server is built for AVX2; without it, answers took 9 s, not 0.7 s.
        log.warning(
            "this processor lacks AVX2, which the language model needs to answer in time; "
            "running on rules only"
        )
        return None, None, "this processor is too old for it (no AVX2)"
    began = time.perf_counter()
    problems = await asyncio.to_thread(
        selfcheck.model_file_problems, settings.models_dir, Path(binary), cfg.model_file
    )
    if problems:
        for problem in problems:
            log.error("model check failed: %s", problem)
        log.error("language model not used; running on rules only")
        return None, None, "its files do not match the manifest (damaged or changed)"
    log.info(
        "model files verified in %.1f s; server %s",
        time.perf_counter() - began,
        Path(binary).name,
    )
    command = llama_server_command(
        Path(binary),
        cfg.model_file,
        port=cfg.port,
        threads=settings.llm_threads,
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
    except ModelServerFailed as e:
        # Said plainly, with the reason: this is what a tester copies and sends back.
        log.error("language model unavailable; running on rules only. %s", e)
    except Exception:
        log.exception("language model unavailable; running on rules only")
    return server, understander, None


def _watch_load(runtime: Runtime, degrader: Degrader) -> Work:
    async def check() -> None:
        recognition = runtime.recognition_stats()
        synthesis = runtime.synthesis_stats()
        load = Load(
            language_model_up=runtime.language_model_state() == "running",
            language_model_refusals=runtime.manager.recent_model_refusals(),
            speech_up=runtime.speech_state() != "down",
            recognition_queued=recognition.queued if recognition else 0,
            recognition_workers=recognition.workers if recognition else 1,
            synthesis_queued=synthesis.queued if synthesis else 0,
            synthesis_workers=synthesis.workers if synthesis else 1,
        )
        level = degrader.update(load)
        if level != runtime.manager.level:
            log.warning("degradation level %d -> %d", runtime.manager.level, level)
            runtime.manager.set_level(level)

    return check


def _keep_model(server: LlamaServer, understander: LlmUnderstander) -> Work:
    async def check() -> None:
        try:
            restarted = await server.ensure_running()
        except ModelServerFailed as e:
            # One line per attempt, ever less often: the full reason was logged at start-up.
            log.warning(
                "language model still not running (%s); next try in %.0f s",
                str(e).split(".", 1)[0],
                server.retry_in_s,
            )
            return
        if restarted:
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
