"""Build the Linux package: what a Linux server needs to install the engine, with the exam
app already built, so the server needs no Node.js.

    uv run python -m tools.make_linux_package     # writes dist/voice-exam-linux-<version>.tar.gz

On the server (x86-64, systemd, with internet once):

    tar xzf voice-exam-linux-<version>.tar.gz
    sudo voice-exam-linux-<version>/deploy/linux/install.sh

The models are not in the package (about 2 GB): the install downloads them, or copies a
models folder given with --models. Needs Node.js on the build machine, for the exam app.
"""

import argparse
import io
import tarfile
import tomllib
from collections.abc import Iterator
from pathlib import Path

from tools.make_bundle import BUILT_APP, _build_exam_app

ROOT = Path(__file__).parents[1]
PARTS = ("src", "tools", "config", "deploy", "pyproject.toml", "uv.lock", "README.md")
EXECUTABLE = frozenset(
    {"deploy/linux/install.sh", "deploy/linux/uninstall.sh", "deploy/linux/voice-exam"}
)
SKIPPED = frozenset({"__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"})


def package_files(root: Path = ROOT) -> Iterator[tuple[Path, str]]:
    """Every file to package, as (path on disk, path in the package), in order."""
    for part in PARTS:
        path = root / part
        if path.is_file():
            yield path, part
            continue
        for found in sorted(path.rglob("*")):
            relative = found.relative_to(root)
            if found.is_file() and not SKIPPED & set(relative.parts) and found.suffix != ".pyc":
                yield found, relative.as_posix()


def write_package(out: Path, root: Path = ROOT) -> Path:
    """``out``: one top folder, Unix line endings for the server's scripts, owned by root."""
    top = out.name.removesuffix(".tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        for path, name in package_files(root):
            data = path.read_bytes()
            if name.startswith("deploy/linux/"):
                data = data.replace(b"\r\n", b"\n")  # a Windows checkout may have CRLF
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            info.mode = 0o755 if name in EXECUTABLE else 0o644
            info.mtime = int(path.stat().st_mtime)
            tar.addfile(info, io.BytesIO(data))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, help="default: dist/voice-exam-linux-<version>.tar.gz")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    out: Path = args.out or ROOT / "dist" / f"voice-exam-linux-{version}.tar.gz"
    print("exam app ...", flush=True)
    _build_exam_app()
    if not (BUILT_APP / "index.html").is_file():
        raise SystemExit("the exam app is not built")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_package(out)
    print(f"done: {out} ({out.stat().st_size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()
