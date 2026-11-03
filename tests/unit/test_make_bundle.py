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
            assert '"%~dp0python\\python.exe"' in text or "PY=%~dp0python\\python.exe" in text
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


def test_the_zip_leaves_out_what_a_run_left_behind(tmp_path: Path) -> None:
    import zipfile

    folder = tmp_path / "voice-exam"
    for name in (
        "run-service.bat",
        "lib/voice_agent/main.py",
        "data/sessions.db",
        "data/llama-server.log",
        "feedback/session-01.txt",
        "bench-report.json",
    ):
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("x", encoding="ascii")
    archive = make_bundle.zip_bundle(folder, tmp_path / "voice-exam.zip")
    with zipfile.ZipFile(archive) as z:
        assert sorted(z.namelist()) == [
            "voice-exam/lib/voice_agent/main.py",
            "voice-exam/run-service.bat",
        ]


def test_the_windows_service_definition() -> None:
    import xml.etree.ElementTree as ET

    service = ET.fromstring(make_bundle.SERVICE_XML)
    assert service.findtext("id") == make_bundle.SERVICE_ID
    # Runs the bundled Python on the service, from this folder, with the centre's config.
    assert service.findtext("executable") == r"%BASE%\python\python.exe"
    assert service.findtext("arguments") == "-m voice_agent"
    env = {e.get("name"): e.get("value") for e in service.findall("env")}
    assert env["VOICE_AGENT_CONFIG"] == r"%BASE%\config\centre.toml"
    assert env["PYTHONNOUSERSITE"] == "1" and r"%BASE%\lib" in (env["PYTHONPATH"] or "")
    # Starts with the PC, and Windows starts it again if it stops.
    assert service.findtext("startmode") == "Automatic"
    assert [f.get("action") for f in service.findall("onfailure")] == ["restart"] * 3
    # The install script asks for administrator rights itself, and installs then starts.
    install = make_bundle.SCRIPTS["Install as a Windows service.bat"]
    assert "-Verb RunAs" in install
    exe = make_bundle.SERVICE_EXE
    assert install.index(f"{exe} install") < install.index(f"{exe} start")
    # A running service and a black window would fight over the port: the start scripts
    # just open the page when the service is running.
    assert make_bundle.IF_SERVICE_RUNNING in make_bundle.SCRIPTS["run-service.bat"]


def test_the_centres_tasks_ship_with_the_bundle() -> None:
    tasks = make_bundle.centre_scripts(make_bundle.ENV)
    for name, text in tasks.items():
        assert make_bundle.SCRIPTS[name] == text, name
    imported = make_bundle.SCRIPTS["Exam store - import a paper.bat"]
    # Nothing typed: nothing done. A path dragged in keeps its quotes off.
    assert imported.index("if not defined PACKAGE exit /b") < imported.index('PACKAGE:"=')
    assert "voice_agent.sync push" in make_bundle.SCRIPTS["Sync - upload results.bat"]


def test_the_practice_paper_is_loaded_on_first_start() -> None:
    for name in ("run-service.bat", "Install as a Windows service.bat"):
        script = make_bundle.SCRIPTS[name]
        assert "if not exist data\\exams.db" in script, name
        assert f"examstore open {make_bundle.PRACTICE_EXAM}" in script, name
    # The service starts after the paper is loaded, so candidates can sign in at once.
    run = make_bundle.SCRIPTS["run-service.bat"]
    assert run.index("examstore open") < run.index("-m voice_agent\n")
    assert make_bundle.EXAM_PAGE.endswith("/exam/")
