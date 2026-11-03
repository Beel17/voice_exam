"""Deploying on a Linux server: the package, the scripts in it, and the centre's set-up.
(Installing it for real is checked by hand on a Linux machine: it needs root and systemd.)"""

import tarfile
from pathlib import Path

import pytest
from tools import make_linux_package
from tools.setup_centre import enable_tls, write_config

from voice_agent.config import load_settings

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "config" / "centre.example.toml"
DEPLOY = ROOT / "deploy" / "linux"


def test_a_linux_server_serves_the_centre(tmp_path: Path) -> None:
    config = tmp_path / "centre.toml"
    write_config(EXAMPLE, config, tmp_path / "DEVICE-TOKEN.txt", "/var/lib/voice-exam/data",
                 server=True)  # fmt: skip
    settings = load_settings(config)
    assert settings.mode == "server" and settings.bind_host == "0.0.0.0"
    assert settings.adapter.kind == "local" and not settings.tls.enabled


def test_https_is_switched_on_in_place(tmp_path: Path) -> None:
    config = tmp_path / "centre.toml"
    write_config(EXAMPLE, config, tmp_path / "DEVICE-TOKEN.txt", server=True)
    cert, key = tmp_path / "tls" / "exam-server.crt", tmp_path / "tls" / "exam-server.key"
    enable_tls(config, cert, key)
    tls = load_settings(config).tls
    assert (tls.cert_file, tls.key_file) == (cert, key)
    enable_tls(config, cert.with_name("new.crt"), key.with_name("new.key"))  # again: replaced
    assert load_settings(config).tls.cert_file == cert.with_name("new.crt")


def test_https_needs_the_tls_section(tmp_path: Path) -> None:
    config = tmp_path / "centre.toml"
    config.write_text('mode = "server"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="cert_file"):
        enable_tls(config, tmp_path / "a.crt", tmp_path / "a.key")


def test_the_package_holds_the_program_and_never_build_clutter() -> None:
    names = [name for _, name in make_linux_package.package_files()]
    for needed in ("pyproject.toml", "uv.lock", "deploy/linux/install.sh",
                   "deploy/linux/voice-exam.service", "src/voice_agent/main.py",
                   "tools/fetch_models.py", "tools/setup_centre.py"):  # fmt: skip
        assert needed in names, needed
    for name in names:
        assert "node_modules" not in name and "__pycache__" not in name, name
        assert not name.startswith(("exam-app/", "models/", "data/", "dist/")), name


def test_the_scripts_arrive_runnable_with_unix_line_endings(tmp_path: Path) -> None:
    root = tmp_path / "root"
    for part in make_linux_package.PARTS:
        (root / part).parent.mkdir(parents=True, exist_ok=True)
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\r\n", encoding="utf-8")
    for part in ("tools", "config"):
        (root / part).mkdir()
    (root / "deploy" / "linux").mkdir(parents=True)
    (root / "deploy" / "linux" / "install.sh").write_bytes(b"#!/bin/sh\r\necho hi\r\n")
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        (root / name).write_text("", encoding="utf-8")
    out = make_linux_package.write_package(tmp_path / "voice-exam-linux-9.tar.gz", root)
    with tarfile.open(out) as tar:
        script = tar.getmember("voice-exam-linux-9/deploy/linux/install.sh")
        assert script.mode == 0o755 and script.uid == 0
        assert tar.extractfile(script).read() == b"#!/bin/sh\necho hi\n"  # type: ignore[union-attr]
        assert tar.getmember("voice-exam-linux-9/src/a.py").mode == 0o644


def test_the_service_runs_as_its_own_user_on_its_data() -> None:
    unit = (DEPLOY / "voice-exam.service").read_text(encoding="utf-8")
    assert "User=voiceexam" in unit and "Restart=always" in unit
    assert "VOICE_AGENT_CONFIG=/var/lib/voice-exam/centre.toml" in unit
    assert "ReadWritePaths=/var/lib/voice-exam" in unit
    install = (DEPLOY / "install.sh").read_text(encoding="utf-8")
    # The data and configuration survive an upgrade; set-up keeps an existing config.
    assert 'rm -rf "$DATA' not in install and "--server" in install
    assert "libgomp1" in install  # the Linux model server needs it
    for script in ("install.sh", "uninstall.sh", "voice-exam"):
        assert b"\r\n" not in (DEPLOY / script).read_bytes(), script
