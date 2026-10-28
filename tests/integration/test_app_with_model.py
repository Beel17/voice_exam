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

from voice_agent.adapters.fake_adapter import FakeAdapter
from voice_agent.api.runtime import Runtime
from voice_agent.config import LlmSettings, Settings
from voice_agent.contract import SCHEMA_VERSION
from voice_agent.main import _every, _keep_model, create_app

FAKE = Path(__file__).parents[1] / "fakes" / "fake_llama_server.py"
TOKEN = "device-token-for-tests-0001"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def fake_binary(tmp_path: Path) -> Path:
    """A launcher that accepts llama-server's arguments and starts the stand-in."""
    if os.name == "nt":
        path = tmp_path / "llama-server.cmd"
        path.write_text(f'@"{sys.executable}" "{FAKE}" %*\n', encoding="utf-8")
    else:
        path = tmp_path / "llama-server"
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    model = tmp_path / "model.gguf"
    model.write_bytes(b"not really a model")
    return Settings(
        data_dir=tmp_path,
        device_token_sha256=(hashlib.sha256(TOKEN.encode()).hexdigest(),),
        llm=LlmSettings(
            server_binary=fake_binary(tmp_path), model_file=model, port=free_port(), timeout_s=5
        ),
    )


def test_free_speech_goes_to_the_model(settings: Settings) -> None:
    app = create_app(settings, adapter=FakeAdapter.with_bundled_fixtures())
    with TestClient(app) as client, client.websocket_connect("/v1/session") as ws:
        assert client.get("/readyz").json() == {"status": "ok", "language_model": "running"}
        ws.send_text(
            json.dumps(
                {
                    "type": "hello",
                    "schema_version": SCHEMA_VERSION,
                    "device_id": "pc",
                    "token": TOKEN,
                }
            )
        )
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


def test_a_model_that_will_not_start_means_rules_only(settings: Settings, tmp_path: Path) -> None:
    broken = tmp_path / ("broken.cmd" if os.name == "nt" else "broken")
    broken.write_text(
        "@exit /b 3\n" if os.name == "nt" else "#!/bin/sh\nexit 3\n", encoding="utf-8"
    )
    if os.name != "nt":
        broken.chmod(broken.stat().st_mode | stat.S_IEXEC)
    llm = settings.llm.model_copy(update={"server_binary": broken})
    app = create_app(
        settings.model_copy(update={"llm": llm}), adapter=FakeAdapter.with_bundled_fixtures()
    )
    with TestClient(app) as client:
        assert client.get("/readyz").json() == {"status": "degraded", "language_model": "down"}


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
