"""Prepare the models folder once, on any machine with internet; exam PCs never need it.

    uv run python -m tools.fetch_models            # language and speech models (~2 GB)
    uv run python -m tools.fetch_models --no-small # skip the 0.5B comparison model

Downloads pinned versions, checks each against its published SHA-256 while it streams,
unpacks the llama.cpp server, and writes models/manifest.json with the SHA-256 of every
file. Copy the whole models/ folder with the package; at start-up the service checks the
files it uses against the manifest, so every centre runs exactly the same models.
"""

import argparse
import hashlib
import json
import sys
import tarfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

MODELS = Path("models")
CHUNK = 1 << 20
ATTEMPTS = 3


@dataclass(frozen=True)
class Artifact:
    name: str
    url: str
    sha256: str
    size: int
    file: str  # where it is saved under models/
    unpack_to: str | None = None  # for archives: the folder to extract into
    optional: bool = False


LLAMA_CPP = Artifact(
    name="llama.cpp server b11606, Windows x64, CPU",
    url="https://github.com/ggml-org/llama.cpp/releases/download/b11606/llama-b11606-bin-win-cpu-x64.zip",
    sha256="8c0f4f2a1b808eebe00e738edf027cceea1aa9e6c883618bd37151666093f1b4",
    size=19_514_151,
    file="downloads/llama-b11606-bin-win-cpu-x64.zip",
    unpack_to="llama.cpp",
)
QWEN_1_5B = Artifact(
    name="Qwen2.5 1.5B Instruct, Q4_K_M (Apache-2.0)",
    url="https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/"
    "91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf",
    sha256="6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
    size=1_117_320_736,
    file="qwen2.5-1.5b-instruct-q4_k_m.gguf",
)
QWEN_0_5B = Artifact(
    name="Qwen2.5 0.5B Instruct, Q4_K_M (Apache-2.0), for the floor tier",
    url="https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/"
    "9217f5db79a29953eb74d5343926648285ec7e67/qwen2.5-0.5b-instruct-q4_k_m.gguf",
    sha256="74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db",
    size=491_400_032,
    file="qwen2.5-0.5b-instruct-q4_k_m.gguf",
    optional=True,
)
_SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


def _speech(name: str, archive: str, sha256: str, size: int, folder: str) -> Artifact:
    tag = "asr-models" if folder == "asr" else "tts-models"
    return Artifact(
        name=name,
        url=f"{_SHERPA}/{tag}/{archive}",
        sha256=sha256,
        size=size,
        file=f"downloads/{archive}",
        unpack_to=f"speech/{folder}",
    )


# Recognition: streaming English (Kroko community model, CC-BY-SA: attribution needed).
# Chosen over the 20M zipformer, which lost the start of speech even on its own samples.
ASR_KROKO = _speech(
    "Speech recognition: streaming zipformer, English (Kroko, 2025, CC-BY-SA)",
    "sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06.tar.bz2",
    "c8676e5ff9ac2a85296e53ee0fd4d5fb1db6770e7a7647166eeafe349ade6834",
    57_267_600,
    "asr",
)
# Voices for the blind testers to choose between (Piper, medium quality). The int8
# versions were measured nearly three times slower on CPU, so full precision is used.
VOICES = [
    _speech(
        "Voice: US English, female (lessac)",
        "vits-piper-en_US-lessac-medium.tar.bz2",
        "9e3febfacf0abf4270172d2958bcec246032b7e88efc2720840cc80c93de334e",
        67_230_653,
        "tts",
    ),
    _speech(
        "Voice: British English, male (alan)",
        "vits-piper-en_GB-alan-medium.tar.bz2",
        "a48d4017da0f77668b27bed63fe6e04dd64c6397e1fadad4f460efb0ef7c9012",
        67_220_121,
        "tts",
    ),
    _speech(
        "Voice: British English, female (jenny)",
        "vits-piper-en_GB-jenny_dioco-medium.tar.bz2",
        "a0888024569bafbefc05a4b48ddf8419d8dbbf3205f4af37cf7c6f1a87cc20c5",
        67_225_842,
        "tts",
    ),
]

ARTIFACTS = [LLAMA_CPP, QWEN_1_5B, QWEN_0_5B, ASR_KROKO, *VOICES]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(artifact: Artifact, models: Path) -> Path:
    target = models / artifact.file
    if (
        target.is_file()
        and target.stat().st_size == artifact.size
        and sha256_of(target) == artifact.sha256
    ):
        print(f"  already here: {artifact.file}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return _download(artifact, target)
        except httpx.HTTPError as e:
            print(f"\n  attempt {attempt} of {ATTEMPTS} failed: {e}")
            if attempt < ATTEMPTS:
                time.sleep(5 * attempt)
    raise SystemExit(
        f"Could not download {artifact.name}. Check the internet connection and run this "
        "again: files already downloaded and verified are kept."
    )


def _download(artifact: Artifact, target: Path) -> Path:
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    done = 0
    with httpx.stream("GET", artifact.url, follow_redirects=True, timeout=60) as response:
        response.raise_for_status()
        with partial.open("wb") as out:
            for chunk in response.iter_bytes(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                print(
                    f"\r  {artifact.file}: {done / 2**20:,.0f} / {artifact.size / 2**20:,.0f} MB",
                    end="",
                )
    print()
    if done != artifact.size or digest.hexdigest() != artifact.sha256:
        partial.unlink()
        raise SystemExit(f"{artifact.name}: checksum or size mismatch; nothing kept")
    partial.replace(target)
    return target


def unpack(archive: Path, into: Path) -> None:
    into = into.resolve()
    if archive.name.endswith(".tar.bz2"):
        with tarfile.open(archive, "r:bz2") as tf:
            # The "data" filter refuses absolute paths, "..", links out of the folder
            # and device files: the archive can only ever write plain files inside it.
            tf.extractall(into, filter="data")
        return
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            # Never write outside the target folder, whatever the archive says.
            if not (into / member).resolve().is_relative_to(into):
                raise SystemExit(f"{archive.name}: unsafe path in archive: {member}")
        zf.extractall(into)


def write_manifest(models: Path) -> Path:
    files = {
        p.relative_to(models).as_posix(): sha256_of(p)
        for p in sorted(models.rglob("*"))
        if p.is_file() and p.name != "manifest.json" and "downloads" not in p.parts
    }
    sources = [{"name": a.name, "url": a.url, "sha256": a.sha256} for a in ARTIFACTS]
    manifest = models / "manifest.json"
    manifest.write_text(json.dumps({"files": files, "sources": sources}, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--models", type=Path, default=MODELS)
    parser.add_argument("--no-small", action="store_true", help="skip the 0.5B model")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Only the Windows build is pinned so far; Linux comes with packaging.")
    for artifact in ARTIFACTS:
        if artifact.optional and args.no_small:
            continue
        print(f"{artifact.name} ({artifact.size / 2**20:,.0f} MB)")
        path = fetch(artifact, args.models)
        if artifact.unpack_to:
            # Unpack once: the files may be in use (a running model server holds its DLLs).
            marker = path.with_name(path.name + ".unpacked")
            if marker.is_file() and marker.read_text() == artifact.sha256:
                continue
            unpack(path, args.models / artifact.unpack_to)
            marker.write_text(artifact.sha256)
    manifest = write_manifest(args.models)
    print(f"wrote {manifest}")


if __name__ == "__main__":
    main()
