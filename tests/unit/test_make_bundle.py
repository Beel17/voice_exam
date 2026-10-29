"""The test bundle's configuration and scripts (building a whole bundle is checked by hand:
it downloads Python and copies 1.5 GB)."""

import hashlib
from pathlib import Path

from tools import make_bundle

from voice_agent.config import load_settings


def test_the_config_trusts_only_the_bundled_token(tmp_path: Path) -> None:
    make_bundle._write_config(tmp_path)
    token = (tmp_path / "DEVICE-TOKEN.txt").read_text(encoding="ascii").strip()
    settings = load_settings(tmp_path / "config" / "centre.toml")
    assert settings.device_token_sha256 == (hashlib.sha256(token.encode()).hexdigest(),)
    assert settings.mode == "pc" and settings.bind_host == "127.0.0.1"
    assert token not in (tmp_path / "config" / "centre.toml").read_text(encoding="utf-8")


def test_each_build_gets_its_own_token(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        make_bundle._write_config(tmp_path / name)
    tokens = {(tmp_path / n / "DEVICE-TOKEN.txt").read_text(encoding="ascii") for n in "ab"}
    assert len(tokens) == 2


def test_the_scripts_run_from_their_own_folder() -> None:
    for name, text in make_bundle.SCRIPTS.items():
        assert text.startswith("@echo off"), name
        if "python.exe" in text:
            # Wherever the drive is mounted, and whatever Python the PC has, if any.
            assert 'cd /d "%~dp0"' in text and "PYTHONNOUSERSITE=1" in text, name
            assert '"%~dp0python\\python.exe"' in text, name
    assert f"models/{make_bundle.LANGUAGE_MODEL}" in make_bundle.SCRIPTS["Measure this PC.bat"]


def test_a_start_script_for_each_shipped_voice() -> None:
    root = Path(__file__).parents[2]
    starts = [n for n in make_bundle.SCRIPTS if n.startswith("Start exam - ")]
    assert len(starts) == len(make_bundle.VOICES) == 3
    for folder in make_bundle.VOICES.values():
        assert (root / "tools" / "fetch_models.py").read_text(encoding="utf-8").count(folder)
        assert any(folder in make_bundle.SCRIPTS[n] for n in starts)
    # The voice reaches the service as an environment override of the config file.
    assert "VOICE_AGENT_SPEECH__VOICE_DIR" in make_bundle.SCRIPTS["run-service.bat"]


def test_the_guide_and_form_ship() -> None:
    for name in ("README-FIRST.txt", "FEEDBACK-FORM.txt"):
        text = (make_bundle.TEXTS / name).read_text(encoding="utf-8")
        assert text.isascii(), name  # Notepad and screen readers on any Windows
    guide = (make_bundle.TEXTS / "README-FIRST.txt").read_text(encoding="utf-8")
    for script in make_bundle.SCRIPTS:
        if script != "run-service.bat":
            assert script.removesuffix(".bat") in guide, script
