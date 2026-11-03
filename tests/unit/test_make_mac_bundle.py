"""The Mac test bundle's scripts and guide (building one is checked on a Mac, by
.github/workflows/mac-bundle.yml: it downloads Python and copies 1.5 GB)."""

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
from tools import make_bundle, make_mac_bundle
from tools.fetch_models import LINUX, MACOS, WINDOWS, artifacts_for, unpack

SCRIPTS = make_mac_bundle.SCRIPTS


def test_the_scripts_run_from_their_own_folder() -> None:
    for name, text in SCRIPTS.items():
        assert name.endswith((".command", ".sh")) and text.startswith("#!/bin/bash\n"), name
        assert text.isascii(), name
        if '"$PY"' in text:
            # Wherever the folder is, and whatever Python the Mac has, if any.
            assert 'cd "$(dirname "$0")" || exit 1' in text, name
            assert "PYTHONNOUSERSITE=1" in text and 'PY="$PWD/python/bin/python3"' in text


def test_the_same_double_clicks_as_on_windows() -> None:
    # Less the Windows service, which on the Mac is launchd's job (a later step).
    windows = {n.removesuffix(".bat").replace("this PC", "this Mac") for n in make_bundle.SCRIPTS}
    windows -= {"Install as a Windows service", "Remove the Windows service", "run-service"}
    mac = {n.removesuffix(".command") for n in SCRIPTS if n.endswith(".command")}
    assert mac == windows


def test_a_start_script_for_each_shipped_voice() -> None:
    starts = [n for n in SCRIPTS if n.startswith("Start exam - ")]
    assert len(starts) == len(make_bundle.VOICES) == 3
    for folder in make_bundle.VOICES.values():
        assert any(folder in SCRIPTS[n] for n in starts)
    assert "VOICE_AGENT_SPEECH__VOICE_DIR" in SCRIPTS["run-service.sh"]


def test_the_practice_paper_is_loaded_before_the_service_starts() -> None:
    run = SCRIPTS["run-service.sh"]
    assert "if [ ! -f data/exams.db ]" in run
    assert run.index(f"examstore open {make_bundle.PRACTICE_EXAM}") < run.index(
        '"$PY" -m voice_agent\n'
    )


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash here")
def test_every_script_is_valid_bash(tmp_path: Path) -> None:
    bash = shutil.which("bash")
    assert bash is not None
    for name, text in SCRIPTS.items():
        script = tmp_path / "script.sh"
        script.write_text(text, encoding="ascii", newline="\n")
        checked = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
        assert checked.returncode == 0, f"{name}: {checked.stderr}"


def test_the_guide_names_every_double_click() -> None:
    guide = (make_bundle.TEXTS / make_mac_bundle.GUIDE).read_text(encoding="utf-8")
    assert guide.isascii()  # TextEdit and VoiceOver, whatever the Mac's settings
    for name in SCRIPTS:
        if name.endswith(".command"):
            assert name.removesuffix(".command") in guide, name
    # The one step macOS needs before it opens anything downloaded and unsigned.
    assert "xattr -dr com.apple.quarantine" in guide
    assert ".bat" not in guide and "Windows" not in guide


def test_each_computer_fetches_its_own_model_server() -> None:
    assert artifacts_for("win32", "AMD64") == WINDOWS
    assert artifacts_for("darwin", "arm64") == MACOS
    assert [a for a in MACOS if a not in WINDOWS] == [MACOS[0]]  # models and voices shared
    assert MACOS[0].unpack_to == "llama-server"  # where the service looks for it
    assert artifacts_for("linux", "x86_64") == LINUX
    assert [a for a in LINUX if a not in WINDOWS] == [LINUX[0]]
    assert LINUX[0].unpack_to == "llama-server" and "ubuntu-x64" in LINUX[0].url
    for platform, machine in (("darwin", "x86_64"), ("linux", "aarch64")):
        with pytest.raises(SystemExit):
            artifacts_for(platform, machine)


def test_unpacking_leaves_out_the_archives_own_folder(tmp_path: Path) -> None:
    built = tmp_path / "llama-b1"
    built.mkdir()
    (built / "llama-server").write_text("server", encoding="ascii")
    (built / "libllama.0.6.dylib").write_text("library", encoding="ascii")
    archive = tmp_path / "llama.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(built, arcname="llama-b1")
        link = tarfile.TarInfo("llama-b1/libllama.dylib")
        link.type, link.linkname = tarfile.SYMTYPE, "libllama.0.6.dylib"
        tf.addfile(link)
    unpack(archive, tmp_path / "out", inner="llama-b1")
    names = {p.name for p in (tmp_path / "out").iterdir()}
    assert {"llama-server", "libllama.0.6.dylib"} <= names
    assert (tmp_path / "out" / "llama-server").read_text(encoding="ascii") == "server"


def test_unpacking_still_refuses_paths_out_of_the_folder(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        evil = tarfile.TarInfo("top/../../escaped")
        evil.size = 0
        tf.addfile(evil)
    with pytest.raises(tarfile.FilterError):
        unpack(archive, tmp_path / "out", inner="top")
    assert not (tmp_path / "escaped").exists()
