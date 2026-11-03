"""The installer: its first-run set-up, and the Inno Setup script it is built from."""

import hashlib
from pathlib import Path

from tools import make_installer
from tools.setup_centre import write_config

from voice_agent.config import load_settings

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "config" / "centre.example.toml"


def test_set_up_writes_the_config_and_token_with_the_data(tmp_path: Path) -> None:
    data = tmp_path / "ProgramData" / "VoiceExam"
    assert write_config(
        EXAMPLE, data / "centre.toml", data / "DEVICE-TOKEN.txt", (data / "data").as_posix()
    )
    token = (data / "DEVICE-TOKEN.txt").read_text(encoding="ascii").strip()
    settings = load_settings(data / "centre.toml")
    assert settings.device_token_sha256 == (hashlib.sha256(token.encode()).hexdigest(),)
    assert settings.data_dir == data / "data"  # sessions with the data, not the program
    assert settings.mode == "pc"


def test_set_up_again_keeps_the_centres_settings(tmp_path: Path) -> None:
    config, token = tmp_path / "centre.toml", tmp_path / "DEVICE-TOKEN.txt"
    write_config(EXAMPLE, config, token)
    before = (config.read_text(encoding="utf-8"), token.read_text(encoding="ascii"))
    assert not write_config(EXAMPLE, config, token)  # an upgrade or reinstall
    assert (config.read_text(encoding="utf-8"), token.read_text(encoding="ascii")) == before


def test_the_installer_script(tmp_path: Path) -> None:
    iss = make_installer.script(tmp_path / "bundle", tmp_path / "stage", "1.2.3")
    lines = iss.splitlines()
    assert "PrivilegesRequired=admin" in lines  # a Windows service needs it
    assert "AppVersion=1.2.3" in lines and f"AppId={{{make_installer.APP_ID}" in lines
    # Set up, then install the service, then start it: in that order.
    run = [line for line in lines if line.startswith("Filename:") and "Flags: runhidden" in line]
    assert [("setup_centre" in r, '"install"' in r, '"start"' in r) for r in run[:3]] == [
        (True, False, False), (False, True, False), (False, False, True)
    ]  # fmt: skip
    # Uninstalling removes the service and the program folder, never the data.
    assert any('"uninstall"' in line for line in lines)
    deleted = [line for line in lines if line.startswith("Type: filesandordirs")]
    assert deleted == ['Type: filesandordirs; Name: "{app}"']
    assert "commonappdata" not in "".join(deleted)
    # An upgrade stops the running service before replacing its files.
    assert "PrepareToInstall" in iss and "'stop'" in iss


def test_a_centre_runs_on_its_exam_store(tmp_path: Path) -> None:
    write_config(EXAMPLE, tmp_path / "centre.toml", tmp_path / "DEVICE-TOKEN.txt")
    assert load_settings(tmp_path / "centre.toml").adapter.kind == "local"


def test_the_installed_centre_tasks() -> None:
    from tools.make_bundle import centre_scripts

    for name, text in centre_scripts(make_installer.INSTALLED_ENV).items():
        # As administrator (the exam store is in ProgramData), on the installed program,
        # with the centre's own configuration.
        assert "-Verb RunAs" in text and 'cd /d "%~dp0.."' in text, name
        assert "VOICE_AGENT_CONFIG=%ProgramData%\\VoiceExam\\centre.toml" in text, name
    iss = make_installer.script(Path("bundle"), Path("stage"), "1.0")
    assert 'DestDir: "{app}\\centre"' in iss
    assert "Centre tools" in iss and 'String: "http://127.0.0.1:8765/exam/"' in iss
