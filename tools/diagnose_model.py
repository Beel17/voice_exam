"""Find out why the language model will not run on this PC, and what would make it run.

    python -m tools.diagnose_model          # from the bundle folder; writes model-diagnosis.txt

Starts llama-server several ways, one at a time: as shipped, without weight repacking,
without memory-mapping the model, and with only the engine builds for older processors
(llama.cpp picks the build for the newest instructions this processor has, so a fault
in one build shows up only on processors that pick it). For each it records whether the
server started and answered, or how it died, and which build it chose. Takes a few
minutes; send model-diagnosis.txt back.
"""

import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

import httpx

from tools.procstats import machine
from voice_agent.config import load_settings, pc_llm_threads
from voice_agent.understanding.llm import describe_exit

# Engine builds that use AVX-512 instructions (by the processor family they target).
AVX512_BUILDS: Final = frozenset(
    {"cannonlake", "cascadelake", "cooperlake", "icelake", "sapphirerapids", "skylakex", "zen4"}
)
WAIT_S: Final = 180.0  # a slow PC loading the model


def builds(folder: Path) -> list[str]:
    return sorted(p.stem.removeprefix("ggml-cpu-") for p in folder.glob("ggml-cpu-*.dll"))


def copy_with_builds(source: Path, dest: Path, keep: set[str]) -> Path:
    """The llama.cpp folder with only the CPU engine builds in ``keep``."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        source,
        dest,
        ignore=lambda _folder, names: [
            n for n in names if n.startswith("ggml-cpu-") and Path(n).stem[9:] not in keep
        ],
    )
    return dest


def attempt(binary: Path, model: Path, extra: list[str], work: Path, port: int) -> list[str]:
    """Start the server once; report how it went, in a few lines."""
    log = work / "llama-server.log"
    log.unlink(missing_ok=True)
    command = [str(binary), "--model", str(model), "--host", "127.0.0.1", "--port", str(port),
               "--threads", str(pc_llm_threads()), "--ctx-size", "2048", "-lv", "4",
               "--log-file", str(log), *extra]  # fmt: skip
    began = time.monotonic()
    with (work / "stdout.txt").open("wb") as out:
        process = subprocess.Popen(command, stdout=out, stderr=subprocess.STDOUT)
    result = "did not become ready"
    try:
        while time.monotonic() - began < WAIT_S:
            if process.poll() is not None:
                result = f"DIED after {time.monotonic() - began:.1f} s, " + describe_exit(
                    process.returncode
                )
                break
            try:
                if httpx.get(
                    f"http://127.0.0.1:{port}/health", timeout=1, trust_env=False
                ).is_success:
                    asked = time.monotonic()
                    reply = httpx.post(
                        f"http://127.0.0.1:{port}/completion",
                        json={"prompt": "Hello", "n_predict": 4},
                        timeout=120,
                        trust_env=False,
                    )
                    reply.raise_for_status()
                    result = (
                        f"OK: ready after {asked - began:.1f} s, answered in "
                        f"{time.monotonic() - asked:.1f} s"
                    )
                    break
            except httpx.HTTPError:
                if process.poll() is not None:
                    continue  # died while answering: reported on the next pass
            time.sleep(0.5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    lines = [result]
    text = log.read_text("utf-8", "replace") if log.exists() else ""
    text += (work / "stdout.txt").read_text("utf-8", "replace")
    for key in ("loaded CPU backend", "system_info"):
        found = [line.strip() for line in text.splitlines() if key in line]
        if found:
            lines.append(found[0][:300])
    if not result.startswith("OK"):
        tail = [line.strip() for line in text.splitlines() if line.strip()][-6:]
        lines += ["last output:", *(f"  {line[:200]}" for line in tail or ["(nothing)"])]
    return lines


def bare_run(binary: Path, args: list[str]) -> str:
    try:
        done = subprocess.run(
            [str(binary), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        return "did not finish in 60 s"
    output = " / ".join(line.strip() for line in done.stdout.splitlines() if line.strip())
    status = "OK" if done.returncode == 0 else f"DIED, {describe_exit(done.returncode)}"
    return f"{status}; output: {output[:300] or '(nothing)'}"


# Windows records which file a crashing program died in (Application log, event 1000),
# which the program itself cannot say; and the installed security software, which can
# inject itself into programs and crash them before they start.
_WINDOWS_QUERY: Final = r"""
$since = (Get-Date).AddMinutes(-{minutes})
Get-WinEvent -FilterHashtable @{{LogName='Application'; Id=1000; StartTime=$since}} `
    -ErrorAction SilentlyContinue |
  Where-Object {{ $_.Message -match 'llama' }} | Select-Object -First 3 |
  ForEach-Object {{ 'CRASH ' + (($_.Message -split "`r?`n" |
    Where-Object {{ $_ -match 'name|code|offset|path' }}) -join ' | ') }}
$av = Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct `
    -ErrorAction SilentlyContinue | Select-Object -ExpandProperty displayName
'SECURITY ' + ($av -join ', ')
"""


def windows_facts(minutes: int) -> list[str]:
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             _WINDOWS_QUERY.format(minutes=minutes)],
            capture_output=True, text=True, errors="replace", timeout=120,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as e:
        return [f"Windows records: could not be read ({e})", ""]
    crashes = [line[6:] for line in done.stdout.splitlines() if line.startswith("CRASH ")]
    security = [line[9:] for line in done.stdout.splitlines() if line.startswith("SECURITY ")]
    lines = ["Windows crash records for llama-server (the 'faulting module' is the culprit):"]
    lines += [f"  {c[:600]}" for c in crashes] or ["  (none found)"]
    lines += [f"Security software: {(security or ['(unknown)'])[0] or '(none listed)'}", ""]
    return lines


def main() -> None:
    settings = load_settings()
    shipped = Path(str(settings.llm.server_binary)).with_suffix(".exe").resolve()
    if not shipped.is_file():
        shipped = Path(str(settings.llm.server_binary)).resolve()
    model = settings.llm.model_file.resolve()
    work_root = settings.data_dir / "model-diagnosis"
    work_root.mkdir(parents=True, exist_ok=True)
    folder = shipped.parent
    available = set(builds(folder))
    # (title, server program, extra arguments, CPU engine builds to keep, if any)
    tries: list[tuple[str, Path, list[str], set[str] | None]] = [
        (f"{exe.name}, as shipped", exe, [], None)
        for exe in sorted(folder.glob("llama-server*.exe"))
    ]
    tries += [
        ("as shipped, no weight repacking", shipped, ["--no-repack"], None),
        ("as shipped, model read into memory (no mmap)", shipped, ["--load-mode", "none"], None),
    ]
    if available:  # llama.cpp's released layout: one DLL per processor family
        tries += [
            ("without the AVX-512 engine builds", shipped, [], available - AVX512_BUILDS),
            ("basic engine build only (x64)", shipped, [], {"x64"}),
        ]
    report = [
        "Voice exam: language model diagnosis",
        f"machine: {machine()}",
        f"llama.cpp folder: {folder}",
        f"engine builds shipped: {', '.join(sorted(available)) or '(built in)'}",
        "",
    ]
    print("\n".join(report), flush=True)
    began = time.time()
    # Without a model: a crash here is in loading the program itself, not the model.
    for args in (["--version"], ["--list-devices"]):
        block = [f"bare run {' '.join(args)}: " + bare_run(shipped, args), ""]
        print("\n".join(block), flush=True)
        report += block
    for n, (title, exe, extra, keep) in enumerate(tries, start=1):
        print(f"{n}/{len(tries)} {title} ...", flush=True)
        if keep is not None:
            exe = copy_with_builds(folder, work_root / f"builds-{n}", keep) / exe.name
        work = work_root / f"try-{n}"
        work.mkdir(exist_ok=True)
        lines = attempt(exe, model, extra, work, 8790 + n)
        block = [f"{n}. {title}: {lines[0]}", *(f"   {line}" for line in lines[1:]), ""]
        print("\n".join(block), flush=True)
        report += block
    if sys.platform == "win32":  # Windows keeps a record of crashes; others differ
        report += windows_facts(minutes=int((time.time() - began) / 60) + 2)
    out = Path("model-diagnosis.txt")
    out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"Written to {out.resolve()}: please send it back.")


if __name__ == "__main__":
    main()
