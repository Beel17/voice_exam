"""HTTPS for server mode: the real service process, with a certificate made by openssl."""

import os
import shutil
import socket
import ssl
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from tools.make_certificate import make_certificate

from voice_agent.__main__ import tls_files
from voice_agent.config import Settings, TlsSettings


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture
def certificate(tmp_path: Path) -> tuple[Path, Path]:
    if shutil.which("openssl") is None:
        pytest.skip("openssl is not installed")
    return make_certificate(tmp_path / "tls", "exam-server", [])


@pytest.fixture
def server(tmp_path: Path, certificate: tuple[Path, Path]) -> Iterator[int]:
    cert, key = certificate
    port = free_port()
    config = tmp_path / "centre.toml"
    config.write_text(
        f'mode = "server"\nhost = "127.0.0.1"\nport = {port}\n'
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        "[llm]\nenabled = false\n[speech]\nenabled = false\n"
        f'[tls]\ncert_file = "{cert.as_posix()}"\nkey_file = "{key.as_posix()}"\n',
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "voice_agent"],
        env={**os.environ, "VOICE_AGENT_CONFIG": str(config)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield port
    finally:
        process.kill()
        process.wait()


def test_server_mode_serves_https(server: int, certificate: tuple[Path, Path]) -> None:
    cert, _ = certificate
    url = f"https://127.0.0.1:{server}/healthz"
    began = time.monotonic()
    while True:
        try:
            # Verified against the certificate itself, as a centre's PCs would trust it.
            trusted = ssl.create_default_context(cafile=str(cert))
            response = httpx.get(url, verify=trusted, trust_env=False, timeout=2)
            break
        except (httpx.ConnectError, httpx.ConnectTimeout):  # not listening yet
            if time.monotonic() - began > 60:
                raise
            time.sleep(0.2)
    assert response.status_code == 200
    with pytest.raises(httpx.HTTPError):  # plain HTTP is not served alongside
        httpx.get(f"http://127.0.0.1:{server}/healthz", trust_env=False, timeout=2)


def test_both_files_or_neither() -> None:
    with pytest.raises(ValidationError, match="both"):
        TlsSettings(cert_file=Path("a.crt"))
    assert not TlsSettings().enabled


def test_a_missing_certificate_stops_the_start(tmp_path: Path) -> None:
    settings = Settings(
        mode="server", tls=TlsSettings(cert_file=tmp_path / "no.crt", key_file=tmp_path / "no.key")
    )
    with pytest.raises(SystemExit, match=r"tls\.cert_file not found"):
        tls_files(settings)
    assert tls_files(Settings()) == {}  # PC mode: plain HTTP
