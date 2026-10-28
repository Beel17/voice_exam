"""Start-up with a language model, using the stand-in server under the real command line."""

import asyncio
import hashlib
import json
import os
import socket
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tools.fetch_models import write_manifest

from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.api.runtime import Runtime
from voice_agent.config import LlmSettings, Settings
from voice_agent.contract import SCHEMA_VERSION
from voice_agent.main import _every, _keep_model, _watch_load, create_app
from voice_agent.runtime.degrade import Degrader, Level

FAKE = Path(__file__).parents[1] / "fakes" / "fake_llama_server.py"
TOKEN = "device-token-for-tests-0001"
DOWN = {"status": "degraded", "language_model": "down"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def script(folder: Path, name: str, windows: str, posix: str) -> Path:
    """A small executable script: a .cmd file on Windows, a shell script elsewhere."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (f"{name}.cmd" if os.name == "nt" else name)
    path.write_text(windows if os.name == "nt" else posix, encoding="utf-8")
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def fake_binary(folder: Path) -> Path:
    """A launcher that accepts llama-server's arguments and starts the stand-in."""
    return script(
        folder,
        "llama-server",
        f'@"{sys.executable}" "{FAKE}" %*\n',
        f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n',
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """A models folder laid out like a real one, with its manifest."""
    models = tmp_path / "models"
    binary = fake_binary(models / "llama.cpp")
    (models / "llama.cpp" / "ggml.dll").write_bytes(b"a library the server loads")
    model = models / "model.gguf"
    model.write_bytes(b"not really a model")
    write_manifest(models)
    return Settings(
        data_dir=tmp_path,
        models_dir=models,
        device_token_sha256=(hashlib.sha256(TOKEN.encode()).hexdigest(),),
        llm=LlmSettings(server_binary=binary, model_file=model, port=free_port(), timeout_s=5),
    )


def readiness(settings: Settings) -> dict[str, str]:
    app = create_app(settings, adapter=FakeAdapter.with_bundled_fixtures())
    with TestClient(app) as client:
        result: dict[str, str] = client.get("/readyz").json()
        return result


def test_free_speech_goes_to_the_model(settings: Settings) -> None:
    app = create_app(settings, adapter=FakeAdapter.with_bundled_fixtures())
    with TestClient(app) as client, client.websocket_connect("/v1/session") as ws:
        assert client.get("/readyz").json() == {"status": "ok", "language_model": "running"}
        hello = {
            "type": "hello",
            "schema_version": SCHEMA_VERSION,
            "device_id": "pc",
            "token": TOKEN,
        }
        ws.send_text(json.dumps(hello))
        ws.receive_json()
        ws.send_text(
            json.dumps({"type": "login", "reg_no": "10000001AA", "exam_code": "UTME-SAMPLE"})
        )
        while ws.receive_json()["type"] != "state":
            pass
        for text in ("start", "hmm I reckon it is probably B"):
            ws.send_text(json.dumps({"type": "text", "text": text}))
            said = []
            while (message := ws.receive_json())["type"] != "state":
                said.append(message.get("text", ""))
        assert "you chose option B: frank" in " ".join(said)

        # The model server dies; the keeper restarts and re-warms it.
        rt: Runtime = client.app.state.runtime  # type: ignore[attr-defined]
        assert rt.llm_server is not None
        client.portal.call(rt.llm_server.stop)  # type: ignore[union-attr]
        assert client.get("/readyz").json()["language_model"] == "down"
        understander = rt.manager._understander
        assert understander is not None
        keep = _keep_model(rt.llm_server, understander)  # type: ignore[arg-type]

        async def restart() -> None:
            await keep()

        client.portal.call(restart)  # type: ignore[union-attr]
        assert rt.llm_server.restarts == 1
        assert client.get("/readyz").json()["language_model"] == "running"


def test_a_model_that_will_not_start_means_rules_only(settings: Settings) -> None:
    folder = settings.models_dir / "llama.cpp"
    broken = script(folder, "broken", "@exit /b 3\n", "#!/bin/sh\nexit 3\n")
    write_manifest(settings.models_dir)  # verified, so it is the start that fails
    llm = settings.llm.model_copy(update={"server_binary": broken})
    assert readiness(settings.model_copy(update={"llm": llm})) == DOWN


@pytest.mark.parametrize("tamper", ["model", "library", "manifest"])
def test_files_that_do_not_match_the_manifest_are_not_used(settings: Settings, tamper: str) -> None:
    models = settings.models_dir
    match tamper:
        case "model":
            (models / "model.gguf").write_bytes(b"a different model")
        case "library":
            (models / "llama.cpp" / "ggml.dll").write_bytes(b"swapped")
        case "manifest":
            (models / "manifest.json").unlink()
    assert readiness(settings) == DOWN


async def test_background_work_survives_a_failure() -> None:
    runs = 0

    async def flaky() -> None:
        nonlocal runs
        runs += 1
        if runs == 1:
            raise RuntimeError("platform hiccup")

    task = asyncio.create_task(_every(0.01, flaky))
    while runs < 3:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


class _Manager:
    def __init__(self) -> None:
        self.level = Level.NORMAL
        self.refusals = 0

    def recent_model_refusals(self) -> int:
        return self.refusals

    def set_level(self, level: Level) -> None:
        self.level = level

    def current(self) -> Level:  # read through a call: mypy would keep a narrowed value
        return self.level


class _Runtime:
    def __init__(self) -> None:
        self.manager = _Manager()
        self.model = "running"

    def language_model_state(self) -> str:
        return self.model


async def test_the_load_watch_moves_the_level() -> None:
    rt = _Runtime()
    watch = _watch_load(rt, Degrader(calm_s=0))  # type: ignore[arg-type]
    await watch()
    assert rt.manager.current() is Level.NORMAL
    rt.model = "down"
    await watch()
    assert rt.manager.current() is Level.RULES_ONLY
    rt.model = "running"
    await watch()  # calm spell starts
    await watch()
    assert rt.manager.current() is Level.NORMAL
    rt.manager.refusals = 3  # running, but overloaded
    await watch()
    assert rt.manager.current() is Level.RULES_ONLY
