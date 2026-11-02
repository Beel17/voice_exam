"""Build the language-model server from pinned source, so it runs on any Windows PC.

    uv run python -m tools.build_llama          # needs Visual Studio Build Tools and git

llama.cpp's released Windows server uses the PC's own Microsoft C++ runtime, and crashes
at once where that runtime is older than the one it was built against (an exam centre PC
with a 2018 runtime did exactly that). This builds the same version with the runtime
compiled in, and without OpenMP's runtime DLL, then checks that the result imports no
runtime DLL at all. It uses AVX2 (PCs from about 2013 on); a build without it answered in
9 s instead of 0.7 s, too slow to help, so on older processors the service runs on rules
only. The result goes to models/llama-server/ and into models/manifest.json.
"""

import argparse
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Final

from tools.fetch_models import MODELS, write_manifest

ROOT = Path(__file__).parents[1]
TAG: Final = "b11606"  # the release fetch_models.py pinned, built from its own commit
COMMIT: Final = "1b70350c92563449576bcbbf552994b1d1ed847d"
REPO: Final = "https://github.com/ggml-org/llama.cpp"
VSWHERE: Final = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")

COMMON: Final = [
    "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
    "-DBUILD_SHARED_LIBS=OFF", "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",  # runtime inside
    "-DGGML_NATIVE=OFF", "-DGGML_OPENMP=OFF",  # no build-machine tuning, no vcomp140.dll
    "-DLLAMA_OPENSSL=OFF", "-DLLAMA_USE_PREBUILT_UI=OFF", "-DLLAMA_BUILD_UI=OFF",  # offline
    "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_EXAMPLES=OFF", "-DLLAMA_BUILD_APP=OFF",
    "-DLLAMA_BUILD_SERVER=ON", "-DLLAMA_BUILD_TOOLS=ON",
]  # fmt: skip
INSTRUCTIONS: Final = ["AVX", "AVX2", "FMA", "F16C", "BMI2"]
VARIANTS: Final = {"llama-server.exe": [f"-DGGML_{x}=ON" for x in INSTRUCTIONS]}
# Any of these imported means the PC's own copy is used: exactly what must not happen.
RUNTIME_DLLS: Final = ("msvcp", "vcruntime", "vcomp", "api-ms-win-crt", "libomp", "ucrtbase")


def dll_imports(path: Path) -> list[str]:
    """The DLLs a 64-bit Windows program or library loads at start (its import table)."""
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    sections, optional_size = (
        struct.unpack_from("<H", data, pe + 6)[0],
        struct.unpack_from("<H", data, pe + 20)[0],
    )
    optional = pe + 24
    import_rva = struct.unpack_from("<I", data, optional + 120)[0]  # PE32+ data directory 1
    table: list[tuple[int, int, int]] = []
    for k in range(sections):
        header = optional + optional_size + 40 * k
        size, rva, raw_size, raw = (int(v) for v in struct.unpack_from("<IIII", data, header + 8))
        table.append((rva, max(size, raw_size), raw))

    def offset(rva: int) -> int:
        for start, size, raw in table:
            if start <= rva < start + size:
                return rva - start + raw
        raise ValueError(f"{path}: address {rva:#x} is in no section")

    names: list[str] = []
    entry = offset(import_rva)
    while name_rva := struct.unpack_from("<I", data, entry + 12)[0]:
        at = offset(name_rva)
        names.append(data[at : data.index(b"\0", at)].decode("ascii"))
        entry += 20
    return names


def runtime_imports(path: Path) -> list[str]:
    return [n for n in dll_imports(path) if n.lower().startswith(RUNTIME_DLLS)]


def _vcvars() -> Path:
    """Visual Studio 2022's C++ compiler: a released one, never a preview that happens to
    be installed (what reaches exam centres is built with a stable compiler)."""
    if not VSWHERE.is_file():
        raise SystemExit("Visual Studio Build Tools (with the C++ workload) are needed")
    found = subprocess.run(
        [str(VSWHERE), "-products", "*", "-version", "[17.0,18.0)", "-latest", "-requires",
         "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    vcvars = Path(found) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.is_file():
        raise SystemExit("no C++ compiler found in Visual Studio")
    return vcvars


def _without_other_compilers() -> dict[str, str]:
    """This environment minus any other compiler, so vcvars's is the one CMake uses: CC
    and CXX name a compiler CMake takes over anything else, and PATH may list another
    Visual Studio first (a preview compiler was picked up both ways)."""
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("CC", "CXX")}
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep) if "Microsoft Visual Studio" not in p
    )
    return env


def _source(work: Path) -> Path:
    source = work / "src"
    if not source.is_dir():
        subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", "--branch", TAG, REPO, str(source)],
            check=True,
        )
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if head != COMMIT:
        raise SystemExit(f"{source} is at {head}, not the pinned {COMMIT}")
    return source


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--work", type=Path, default=ROOT / "build" / "llama.cpp")
    parser.add_argument("--models", type=Path, default=MODELS)
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("this builds the Windows server; run it on Windows")
    args.work.mkdir(parents=True, exist_ok=True)
    source, vcvars = _source(args.work), _vcvars()
    out = args.models / "llama-server"
    out.mkdir(parents=True, exist_ok=True)
    for name, flags in VARIANTS.items():
        build = args.work / f"out-{Path(name).stem}"
        shutil.rmtree(build, ignore_errors=True)  # CMake remembers a compiler; start clean
        print(f"building {name} (several minutes) ...", flush=True)
        configure = subprocess.list2cmdline(["cmake", "-S", str(source), "-B", str(build),
                                             *COMMON, *flags])  # fmt: skip
        compile_ = subprocess.list2cmdline(["cmake", "--build", str(build),
                                            "--target", "llama-server"])  # fmt: skip
        subprocess.run(
            f'call "{vcvars}" >nul && {configure} && {compile_}',
            shell=True,
            check=True,
            env=_without_other_compilers(),
        )
        version = subprocess.run(
            [str(build / "bin" / "llama-server.exe"), "--version"],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
        print(f"  {(version.stdout + version.stderr).strip().splitlines()[-1]}", flush=True)
        built = build / "bin" / "llama-server.exe"
        if leaks := runtime_imports(built):
            raise SystemExit(f"{name} still loads {', '.join(leaks)}: not self-contained")
        shutil.copy2(built, out / name)
        print(f"  {name}: imports only {', '.join(dll_imports(built))}", flush=True)
    print(f"manifest: {write_manifest(args.models)}")


if __name__ == "__main__":
    main()
