"""The model server must run on any Windows PC: no C++ runtime of the PC's own."""

import platform
from pathlib import Path

import pytest
from tools.build_llama import dll_imports, runtime_imports

from voice_agent.understanding import llm

MODELS = Path(__file__).parents[2] / "models"
SERVERS = sorted((MODELS / "llama-server").glob("llama-server*.exe"))


@pytest.mark.skipif(not SERVERS, reason="run tools/build_llama.py first")
@pytest.mark.parametrize("server", SERVERS, ids=lambda p: p.name)
def test_the_shipped_server_loads_no_runtime_dll(server: Path) -> None:
    # A PC with an old runtime (2018) crashed the released server before it said a word.
    assert runtime_imports(server) == []
    assert {n.upper() for n in dll_imports(server)} <= {
        "KERNEL32.DLL", "SHELL32.DLL", "ADVAPI32.DLL", "WS2_32.DLL"
    }  # fmt: skip


@pytest.mark.skipif(
    not (MODELS / "llama.cpp" / "llama.dll").is_file(), reason="release build not fetched"
)
def test_the_import_reader_sees_a_runtime_dependency() -> None:
    assert "MSVCP140.dll" in runtime_imports(MODELS / "llama.cpp" / "llama.dll")


def test_this_processor_is_asked() -> None:
    assert isinstance(llm.cpu_can_run_model(), bool)


def test_arm_processors_always_can(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "machine", lambda: "arm64")  # Apple Silicon
    assert llm.cpu_can_run_model()
