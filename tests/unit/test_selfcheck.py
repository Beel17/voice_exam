from pathlib import Path

import pytest
from tools.fetch_models import write_manifest

from voice_agent.selfcheck import model_file_problems


@pytest.fixture
def models(tmp_path: Path) -> Path:
    root = tmp_path / "models"
    (root / "llama.cpp").mkdir(parents=True)
    (root / "llama.cpp" / "llama-server.exe").write_bytes(b"server")
    (root / "llama.cpp" / "ggml.dll").write_bytes(b"library")
    (root / "m.gguf").write_bytes(b"model")
    (root / "other.gguf").write_bytes(b"a model this service is not using")
    write_manifest(root)
    return root


def problems(models: Path, model: str = "m.gguf") -> list[str]:
    return model_file_problems(models, models / "llama.cpp" / "llama-server.exe", models / model)


def test_matching_files_pass(models: Path) -> None:
    assert problems(models) == []


def test_a_changed_model_fails(models: Path) -> None:
    (models / "m.gguf").write_bytes(b"tampered")
    assert problems(models) == ["m.gguf does not match the manifest"]


def test_a_changed_library_next_to_the_server_fails(models: Path) -> None:
    (models / "llama.cpp" / "ggml.dll").write_bytes(b"tampered")
    assert problems(models) == ["llama.cpp/ggml.dll does not match the manifest"]


def test_a_missing_library_fails(models: Path) -> None:
    (models / "llama.cpp" / "ggml.dll").unlink()
    assert problems(models) == ["llama.cpp/ggml.dll is missing"]


def test_an_unlisted_model_fails(models: Path) -> None:
    (models / "new.gguf").write_bytes(b"not shipped")
    assert problems(models, "new.gguf") == ["new.gguf is not in the manifest"]


def test_files_outside_the_models_folder_cannot_be_verified(models: Path, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere.gguf"
    outside.write_bytes(b"model")
    found = model_file_problems(models, models / "llama.cpp" / "llama-server.exe", outside)
    assert len(found) == 1 and "outside" in found[0]


def test_a_missing_or_broken_manifest_fails(models: Path) -> None:
    (models / "manifest.json").write_text("{not json", encoding="utf-8")
    assert "missing or unreadable" in problems(models)[0]
    (models / "manifest.json").unlink()
    assert "missing or unreadable" in problems(models)[0]


def test_a_model_not_in_use_does_not_matter(models: Path) -> None:
    (models / "other.gguf").write_bytes(b"changed, but not in use")
    assert problems(models) == []


def test_plain_file_checks_ignore_the_rest_of_the_folder(models: Path) -> None:
    from voice_agent.selfcheck import file_problems

    (models / "llama.cpp" / "ggml.dll").write_bytes(b"changed, but not asked about")
    assert file_problems(models, [models / "m.gguf"]) == []
    (models / "m.gguf").write_bytes(b"tampered")
    assert file_problems(models, [models / "m.gguf"]) == ["m.gguf does not match the manifest"]
