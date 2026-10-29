"""Start-up checks: the model files are exactly the ones the package shipped.

``models/manifest.json`` (written by tools/fetch_models.py) holds the SHA-256 of every
model file. The service uses a model only if the model file and every manifest entry in
the server's folder (the binary and its libraries) are present and match, so every centre
and every PC runs the same, untampered models. Speech models are checked the same way.
The hardware speed test comes in phase 6.
"""

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

_CHUNK = 1 << 20


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def model_file_problems(models_dir: Path, server_binary: Path, model_file: Path) -> list[str]:
    """Everything wrong with the files the language model needs; empty if all is well.
    The server loads libraries from its own folder, so all of those must match too."""
    return _problems(models_dir, [model_file, server_binary], whole_folder_of=server_binary)


def file_problems(models_dir: Path, files: Iterable[Path]) -> list[str]:
    """Everything wrong with these model files (a recogniser, a voice)."""
    return _problems(models_dir, list(files), whole_folder_of=None)


def _problems(models_dir: Path, files: list[Path], whole_folder_of: Path | None) -> list[str]:
    manifest_path = models_dir / "manifest.json"
    try:
        expected: dict[str, str] = json.loads(manifest_path.read_text("utf-8"))["files"]
    except (OSError, ValueError, KeyError):
        return [f"{manifest_path} is missing or unreadable"]

    root = models_dir.resolve()
    to_check: dict[str, Path] = {}
    for path in files:
        try:
            name = path.resolve().relative_to(root).as_posix()
        except ValueError:
            return [f"{path} is outside {models_dir}, so it cannot be verified"]
        to_check[name] = path
    if whole_folder_of is not None:
        folder = whole_folder_of.resolve().parent.relative_to(root).as_posix()
        for name in expected:
            if folder != "." and name.startswith(folder + "/"):
                to_check.setdefault(name, models_dir / name)

    problems = []
    for name, path in sorted(to_check.items()):
        if name not in expected:
            problems.append(f"{name} is not in the manifest")
        elif not path.is_file():
            problems.append(f"{name} is missing")
        elif _sha256(path) != expected[name]:
            problems.append(f"{name} does not match the manifest")
    return problems
