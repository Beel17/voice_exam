"""Build a folder that runs the service on an Apple Silicon Mac with nothing installed.

    uv run python -m tools.make_mac_bundle            # writes dist/voice-exam-mac
    uv run python -m tools.make_mac_bundle --zip      # and dist/voice-exam-mac.zip, to share
    uv run python -m tools.make_mac_bundle --zip --check   # then start it once to prove it

The Mac twin of tools/make_bundle.py: the same service, models, practice paper and fresh
device token, with double-clickable .command files in place of .bat files. Run it on a
Mac with internet and Node.js, after tools/fetch_models.py (which fetches the Mac's model
server there); GitHub's Mac machines build it too (.github/workflows/mac-bundle.yml).

The files are not signed by a registered Apple developer yet, so macOS refuses to open
them once downloaded until the folder is trusted; README-FIRST.txt gives the one command.
"""

import argparse
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

from tools.make_bundle import (
    LANGUAGE_MODEL,
    LEAVE_OUT_OF_MODELS,
    PRACTICE_EXAM,
    PYTHON,
    ROOT,
    SKIP,
    TEXTS,
    TOOLS,
    VOICES,
    _build_exam_app,
    _install_libraries,
    _write_config,
)

EXAM_PAGE = "http://127.0.0.1:8765/exam/"
GUIDE = "README-FIRST-mac.txt"  # shipped as README-FIRST.txt
READY_WITHIN_S = 300
PAUSE = 'read -r -n 1 -s -p "Press any key to close this window."\necho\n'

ENV = """#!/bin/bash
cd "$(dirname "$0")" || exit 1
export PYTHONPATH="$PWD/lib:$PWD" PYTHONNOUSERSITE=1 PYTHONUTF8=1
PY="$PWD/python/bin/python3"
"""
# The practice paper, loaded into the exam store on first start (and after "Start over").
PRACTICE = f"""if [ ! -f data/exams.db ]; then
  echo "Loading the practice paper into the exam store ..."
  "$PY" -m voice_agent.examstore sample data/practice-paper.zip >/dev/null
  "$PY" -m voice_agent.examstore import data/practice-paper.zip >/dev/null
  "$PY" -m voice_agent.examstore open {PRACTICE_EXAM} >/dev/null
fi
"""
START = '#!/bin/bash\nexec "$(dirname "$0")/run-service.sh" {folder}\n'


def centre_scripts(prefix: str) -> dict[str, str]:
    """The centre's exam-store and sync tasks, one double-click each (as on Windows)."""
    store, sync = '"$PY" -m voice_agent.examstore', '"$PY" -m voice_agent.sync'

    # Without -r, read undoes the backslashes Terminal puts in a path dragged onto it.
    def ask(name: str, question: str) -> str:
        return f'read -p "{question} " {name}\n[ -n "${name}" ] || exit 0\n'

    return {
        "Exam store - list papers.command": f"{prefix}{store} list\n{PAUSE}",
        "Exam store - import a paper.command": prefix
        + ask("PACKAGE", "Exam package (.zip) to import, or drag it here:")
        + f'{store} import "$PACKAGE"\n{store} list\n{PAUSE}',
        "Exam store - open a paper for sign-in.command": f"{prefix}{store} list\n"
        + ask("EXAM", "Exam ID to open for sign-in (as listed above):")
        + f'{store} open "$EXAM"\n{store} list\n{PAUSE}',
        "Exam store - close a paper.command": f"{prefix}{store} list\n"
        + ask("EXAM", "Exam ID to close (as listed above):")
        + f'{store} close "$EXAM"\n{store} list\n{PAUSE}',
        "Sync - download papers.command": f"{prefix}{sync} pull\n{store} list\n{PAUSE}",
        "Sync - upload results.command": f"{prefix}{sync} push\n{PAUSE}",
        "Sync - status.command": f"{prefix}{sync} status\n{PAUSE}",
    }


SCRIPTS = (
    {
        # run-service.sh [voice folder]: the service in this window, the exam page when ready.
        # Closing the window ends the service, and the warden ends the model server with it.
        "run-service.sh": ENV
        + PRACTICE
        + """if [ -n "$1" ]; then export VOICE_AGENT_SPEECH__VOICE_DIR="models/speech/tts/$1"; fi
echo "Starting the voice exam service. Leave this window open; close it to stop."
echo "The exam page opens by itself when the service is ready."
"$PY" -m tools.open_client &
OPENER=$!
"$PY" -m voice_agent
kill "$OPENER" 2>/dev/null
"""
        + PAUSE,
        "Open the exam page again.command": f"#!/bin/bash\nopen {EXAM_PAGE}\n",
        "Show the service status.command": "#!/bin/bash\nopen http://127.0.0.1:8765/status\n",
        "Collect feedback.command": ENV
        + """mkdir -p feedback
"$PY" -m tools.export_sessions --data data --out feedback
[ -f feedback/FEEDBACK-FORM.txt ] || cp FEEDBACK-FORM.txt feedback/
for f in bench-report.json model-diagnosis.txt data/logs/*.log; do
  [ -f "$f" ] && cp "$f" feedback/
done
echo
echo "Fill in feedback/FEEDBACK-FORM.txt, then send the whole feedback folder back."
open feedback
"""
        + PAUSE,
        "Start over (delete test sessions).command": """#!/bin/bash
cd "$(dirname "$0")" || exit 1
echo "This deletes every test session on this Mac, so each candidate starts afresh."
echo "Close the service window first. Collect feedback first if you have not."
read -r -p "Delete the test sessions? (y/n) " ANSWER
case "$ANSWER" in y|Y|yes|Yes) ;; *) exit 0 ;; esac
rm -rf data
echo "Done."
"""
        + PAUSE,
        "Diagnose the language model.command": ENV
        + """echo "Trying the language model several ways. This takes a few minutes."
echo "Close the service window first."
"$PY" -m tools.diagnose_model
echo
echo '"Collect feedback" adds model-diagnosis.txt to the feedback folder.'
"""
        + PAUSE,
        "Measure this Mac.command": ENV
        + f"""echo "Measuring this Mac. This takes about five minutes; do not use it meanwhile."
echo "Close the service window first."
"$PY" -m tools.bench --llm-model models/{LANGUAGE_MODEL} --out bench-report.json
echo
echo 'Done. "Collect feedback" adds bench-report.json to the feedback folder.'
"""
        + PAUSE,
    }
    | {
        f"Start exam - {name}.command": START.format(folder=folder)
        for name, folder in VOICES.items()
    }
    | centre_scripts(ENV)
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "voice-exam-mac")
    parser.add_argument("--zip", action="store_true", help="also write <out>.zip, to share")
    parser.add_argument("--check", action="store_true", help="then start it once to prove it")
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise SystemExit("the Mac bundle is built on an Apple Silicon Mac")
    out: Path = args.out.resolve()
    if out.exists():
        raise SystemExit(f"{out} exists: remove it first")
    if not (ROOT / "models" / LANGUAGE_MODEL).is_file():
        raise SystemExit("models are missing: run tools/fetch_models.py first")
    if not (ROOT / "models" / "llama-server" / "llama-server").is_file():
        raise SystemExit("the model server is missing: run tools/fetch_models.py on this Mac")
    out.mkdir(parents=True)

    print("exam app ...", flush=True)
    _build_exam_app()
    print("python ...", flush=True)
    python = _copy_python(out / "python")
    print("libraries ...", flush=True)
    _install_libraries(python, out / "lib")
    shutil.copytree(ROOT / "src" / "voice_agent", out / "lib" / "voice_agent", ignore=SKIP)
    (out / "tools").mkdir()
    for name in TOOLS:
        shutil.copy2(ROOT / "tools" / name, out / "tools" / name)
    print("models ...", flush=True)
    shutil.copytree(
        ROOT / "models",
        out / "models",
        symlinks=True,  # the model server's libraries link to their versioned files
        ignore=lambda folder, names: [
            n for n in names if Path(folder) == ROOT / "models" and n in LEAVE_OUT_OF_MODELS
        ],
    )
    print("configuration ...", flush=True)
    _write_config(out)
    shutil.copy2(TEXTS / GUIDE, out / "README-FIRST.txt")
    shutil.copy2(TEXTS / "FEEDBACK-FORM.txt", out / "FEEDBACK-FORM.txt")
    for name, text in SCRIPTS.items():
        (out / name).write_text(text, encoding="ascii", newline="\n")
        (out / name).chmod(0o755)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file() and not p.is_symlink())
    print(f"done: {out} ({size / 2**30:.1f} GB)")
    if args.zip:
        print("zip ...", flush=True)
        archive = out.with_suffix(".zip")
        # ditto, as the Finder's own Compress does: it keeps the links and the
        # permission to run the scripts, which a plain zip would lose.
        subprocess.run(["ditto", "-c", "-k", "--keepParent", out, archive], check=True)
        print(f"done: {archive} ({archive.stat().st_size / 2**30:.1f} GB)")
    if args.check:
        # After the zip: a run leaves sessions behind, which no copy for testers may hold.
        check(out)


def check(folder: Path) -> None:
    """Start the bundled service as a tester would, wait until the language model, speech
    and voice all run, then stop it and see the model server go with it."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV"))}
    script = folder / "run-service.sh"
    service = subprocess.Popen(
        [str(script)], cwd=folder, env=env, stdin=subprocess.PIPE, start_new_session=True
    )
    try:
        state = _wait_until_ready()
        print(f"check: {state}", flush=True)
        if any(state[part] != "running" for part in ("language_model", "speech", "voice")):
            raise SystemExit("check failed: the bundled service is not fully running")
    finally:
        # The whole group, as closing the Terminal window would (a negative pid).
        os.kill(-service.pid, signal.SIGTERM)
        if service.stdin:
            service.stdin.close()  # nothing to press at "Press any key"
        service.wait(timeout=30)
    time.sleep(2)
    if subprocess.run(["pgrep", "-x", "llama-server"], capture_output=True).stdout.strip():
        raise SystemExit("check failed: the model server outlived the service")
    print("check: passed; the model server stopped with the service")


def _wait_until_ready() -> dict[str, str]:
    deadline = time.monotonic() + READY_WITHIN_S
    state: dict[str, str] = {}
    while time.monotonic() < deadline:
        try:
            state = httpx.get("http://127.0.0.1:8765/readyz", timeout=2, trust_env=False).json()
            if state.get("status") == "ok":  # nothing down: the model has loaded
                return state
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise SystemExit(f"check failed: not ready within {READY_WITHIN_S} s ({state})")


def _copy_python(dest: Path) -> Path:
    """A standalone CPython, which runs from any folder (unlike the system's or a venv)."""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["uv", "python", "install", "--install-dir", tmp, "--no-bin", PYTHON], check=True
        )
        found = sorted(Path(tmp).glob(f"cpython-{PYTHON}.*/bin/python3"))
        if not found:
            raise SystemExit(f"uv could not install Python {PYTHON}")
        shutil.copytree(found[-1].parents[1], dest, symlinks=True, ignore=SKIP)
    return dest / "bin" / "python3"


if __name__ == "__main__":
    main()
