"""Build a folder that runs the service on a Windows PC with nothing installed.

    uv run python -m tools.make_bundle            # writes dist/voice-exam
    uv run python -m tools.make_bundle --zip      # and dist/voice-exam.zip, to share
    uv run python -m tools.make_bundle --out E:/voice-exam

Share the zip, never a folder that has been run: running leaves sessions behind (data/),
and a tester signing in on someone else's sessions resumes their exam, finished or not.

Run it once on a machine with internet and Node.js, after tools/fetch_models.py. The folder
holds its own Python, the libraries, the service with the exam app built fresh from
exam-app/, the models and a configuration with a fresh device token; copy it to a flash
drive and run it anywhere, offline. It runs on the exam store, with the practice paper
loaded on first start (eight practice candidates, two of them in audio mode), and has the
centre's exam-store and sync tasks as double-clicks. It is for testing in PC mode: a
centre's real set-up (tools/make_installer.py) issues its own tokens.
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from tools.setup_centre import write_config

ROOT = Path(__file__).parents[1]
EXAM_APP = ROOT / "exam-app"
BUILT_APP = ROOT / "src" / "voice_agent" / "exam"
PRACTICE_EXAM = "exam-sample-60"
PYTHON = "3.12"
LANGUAGE_MODEL = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
# Downloads are already unpacked; the small model is only for comparison on slow PCs; and
# llama.cpp's released server is replaced by the self-contained build in llama-server/
# (tools/build_llama.py), which needs no C++ runtime on the PC.
LEAVE_OUT_OF_MODELS = {"downloads", "qwen2.5-0.5b-instruct-q4_k_m.gguf", "llama.cpp", "winsw"}
# WinSW runs the service as a Windows service: the program is renamed after the service and
# finds its settings in the .xml file of the same name beside it.
SERVICE_EXE = "voice-exam-service.exe"
SERVICE_ID = "VoiceExam"


def service_xml(logs: str, config: str) -> str:
    """WinSW's settings for the service, logging to ``logs``, configured by ``config``."""
    return rf"""<!-- The voice exam service as a Windows service (WinSW 2); %BASE% is here. -->
<service>
  <id>{SERVICE_ID}</id>
  <name>Voice Exam</name>
  <description>Offline voice service for blind candidates taking computer-based exams.</description>
  <executable>%BASE%\python\python.exe</executable>
  <arguments>-m voice_agent</arguments>
  <workingdirectory>%BASE%</workingdirectory>
  <env name="PYTHONPATH" value="%BASE%\lib;%BASE%" />
  <env name="PYTHONNOUSERSITE" value="1" />
  <env name="PYTHONUTF8" value="1" />
  <env name="VOICE_AGENT_CONFIG" value="{config}" />
  <!-- Starts with the PC; if it stops, Windows starts it again (and again, more slowly). -->
  <startmode>Automatic</startmode>
  <onfailure action="restart" delay="5 sec" />
  <onfailure action="restart" delay="30 sec" />
  <onfailure action="restart" delay="2 min" />
  <resetfailure>1 hour</resetfailure>
  <!-- Asked to stop, it shuts down cleanly (the model server with it) or is ended. -->
  <stoptimeout>20 sec</stoptimeout>
  <logpath>{logs}</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>5</keepFiles>
  </log>
</service>
"""


SERVICE_XML = service_xml(
    r"%BASE%\data\logs", r"%BASE%\config\centre.toml"
)  # the test bundle: logs beside it
# Run as administrator: the script starts itself again with Windows' permission prompt.
ELEVATE = r"""@echo off
cd /d "%~dp0"
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
"""
IF_SERVICE_RUNNING = rf"""sc query {SERVICE_ID} 2>nul | find "RUNNING" >nul"""
EXAM_PAGE = "http://127.0.0.1:8765/exam/"


def centre_scripts(prefix: str) -> dict[str, str]:
    """The centre's exam-store and sync tasks, one double-click each. ``prefix`` goes to the
    program folder and sets up the environment, with ``%PY%`` as the bundled Python."""
    py = '"%PY%"'
    store, sync = f"{py} -m voice_agent.examstore", f"{py} -m voice_agent.sync"
    # A path dragged onto the window arrives in quotes; they are taken off.
    ask = (
        'set "{name}="\n'
        'set /p "{name}={question} "\n'
        "if not defined {name} exit /b\n"
        'set "{name}=%{name}:"=%"\n'
    )
    return {
        "Exam store - list papers.bat": f"{prefix}{store} list\npause\n",
        "Exam store - import a paper.bat": prefix
        + ask.format(name="PACKAGE", question="Exam package (.zip) to import, or drag it here:")
        + f'{store} import "%PACKAGE%"\n{store} list\npause\n',
        "Exam store - open a paper for sign-in.bat": f"{prefix}{store} list\n"
        + ask.format(name="EXAM", question="Exam ID to open for sign-in (as listed above):")
        + f'{store} open "%EXAM%"\n{store} list\npause\n',
        "Exam store - close a paper.bat": f"{prefix}{store} list\n"
        + ask.format(name="EXAM", question="Exam ID to close (as listed above):")
        + f'{store} close "%EXAM%"\n{store} list\npause\n',
        "Sync - download papers.bat": f"{prefix}{sync} pull\n{store} list\npause\n",
        "Sync - upload results.bat": f"{prefix}{sync} push\npause\n",
        "Sync - status.bat": f"{prefix}{sync} status\npause\n",
    }


TOOLS = [
    "__init__.py",
    "bench.py",
    "procstats.py",
    "issue_token.py",
    "text_client.py",
    "open_client.py",
    "export_sessions.py",
    "diagnose_model.py",
    "setup_centre.py",
]
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc")
# What running the bundle leaves behind; never part of a copy for someone else.
LOCAL_STATE = frozenset({"data", "feedback", "bench-report.json", "model-diagnosis.txt"})
# The testers choose the voice: one start script each, named so a screen reader says it.
VOICES = {
    "voice 1 (American woman)": "vits-piper-en_US-lessac-medium",
    "voice 2 (British man)": "vits-piper-en_GB-alan-medium",
    "voice 3 (British woman)": "vits-piper-en_GB-jenny_dioco-medium",
}
TEXTS = ROOT / "tools" / "bundle"  # README-FIRST.txt and FEEDBACK-FORM.txt

ENV = """@echo off
cd /d "%~dp0"
set PYTHONPATH=%~dp0lib;%~dp0
set PYTHONNOUSERSITE=1
set PYTHONUTF8=1
set PY=%~dp0python\\python.exe
"""
# The practice paper, loaded into the exam store on first start (and after "Start over").
PRACTICE = rf"""if not exist data\exams.db (
  echo Loading the practice paper into the exam store ...
  "%PY%" -m voice_agent.examstore sample data\practice-paper.zip >nul
  "%PY%" -m voice_agent.examstore import data\practice-paper.zip >nul
  "%PY%" -m voice_agent.examstore open {PRACTICE_EXAM} >nul
)
"""
SCRIPTS = (
    {
        # run-service.bat [voice folder]: the service in this window, the exam page when ready.
        "run-service.bat": ENV
        + rf"""{IF_SERVICE_RUNNING} && (
  echo The voice exam service is already running as a Windows service: opening the page.
  start "" {EXAM_PAGE}
  timeout /t 5 >nul
  exit /b
)
"""
        + PRACTICE
        + r"""if not "%~1"=="" set VOICE_AGENT_SPEECH__VOICE_DIR=models/speech/tts/%~1
echo Starting the voice exam service. Leave this window open; close it to stop.
echo The exam page opens by itself when the service is ready.
start "" /b "%~dp0python\python.exe" -m tools.open_client
"%~dp0python\python.exe" -m voice_agent
pause
""",
        "Open the exam page again.bat": f"""@echo off
start "" {EXAM_PAGE}
""",
        "Show the service status.bat": """@echo off
start "" http://127.0.0.1:8765/status
""",
        "Collect feedback.bat": ENV
        + r"""if not exist feedback mkdir feedback
"%~dp0python\python.exe" -m tools.export_sessions --data data --out feedback
if not exist feedback\FEEDBACK-FORM.txt copy FEEDBACK-FORM.txt feedback\ >nul
if exist bench-report.json copy /y bench-report.json feedback\ >nul
if exist model-diagnosis.txt copy /y model-diagnosis.txt feedback\ >nul
if exist data\logs\*.log copy /y data\logs\*.log feedback\ >nul
echo.
echo Fill in feedback\FEEDBACK-FORM.txt, then send the whole feedback folder back.
start "" explorer "%~dp0feedback"
pause
""",
        "Start over (delete test sessions).bat": rf"""@echo off
cd /d "%~dp0"
{IF_SERVICE_RUNNING} && (
  echo The voice exam service is running as a Windows service. Run "Remove the Windows
  echo service" first, then this again.
  pause
  exit /b
)
echo This deletes every test session on this PC, so each candidate starts afresh.
echo Close the service window first. Collect feedback first if you have not.
choice /m "Delete the test sessions"
if errorlevel 2 exit /b
rmdir /s /q data
echo Done.
pause
""",
        "Diagnose the language model.bat": ENV
        + r"""echo Trying the language model several ways. This takes a few minutes.
echo Close the service window first.
"%~dp0python\python.exe" -m tools.diagnose_model
echo.
echo "Collect feedback" adds model-diagnosis.txt to the feedback folder.
pause
""",
        "Measure this PC.bat": ENV
        + rf"""echo Measuring this PC. This takes about five minutes; do not use the PC meanwhile.
echo Close the service window first.
"%~dp0python\python.exe" -m tools.bench --llm-model models/{LANGUAGE_MODEL} --out bench-report.json
echo.
echo Done. "Collect feedback" adds bench-report.json to the feedback folder.
pause
""",
        "Install as a Windows service.bat": ELEVATE
        + ENV.removeprefix("@echo off\n")
        + PRACTICE
        + rf"""echo Installing the voice exam service: it will start with this PC,
echo in the background, and restart itself if it ever stops.
echo Close any black service window first.
{SERVICE_EXE} install || goto failed
{SERVICE_EXE} start || goto failed
echo.
echo Installed and started. Open the exam page with "Open the exam page again".
pause
exit /b
:failed
echo.
echo It did not work; the lines above say why.
pause
""",
        "Remove the Windows service.bat": ELEVATE
        + rf"""{SERVICE_EXE} stop
{SERVICE_EXE} uninstall
echo.
echo The service is removed. The folder and its test sessions are kept.
pause
""",
    }
    | {
        f"Start exam - {name}.bat": f'@echo off\ncall "%~dp0run-service.bat" {folder}\n'
        for name, folder in VOICES.items()
    }
    | centre_scripts(ENV)
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "voice-exam")
    parser.add_argument("--zip", action="store_true", help="also write <out>.zip, to share")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("the bundle is built for Windows: run this on a Windows PC")
    out: Path = args.out.resolve()
    if out.exists():
        raise SystemExit(f"{out} exists: remove it first")
    model = ROOT / "models" / LANGUAGE_MODEL
    if not model.is_file():
        raise SystemExit("models are missing: run tools/fetch_models.py first")
    if not (ROOT / "models" / "llama-server" / "llama-server.exe").is_file():
        raise SystemExit("the model server is missing: run tools/build_llama.py first")
    if not (ROOT / "models" / "winsw" / "WinSW-x64.exe").is_file():
        raise SystemExit("WinSW is missing: run tools/fetch_models.py first")
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
        ignore=lambda folder, names: [
            n for n in names if Path(folder) == ROOT / "models" and n in LEAVE_OUT_OF_MODELS
        ],
    )
    print("configuration ...", flush=True)
    _write_config(out)
    shutil.copy2(ROOT / "models" / "winsw" / "WinSW-x64.exe", out / SERVICE_EXE)
    (out / SERVICE_EXE).with_suffix(".xml").write_text(SERVICE_XML, encoding="utf-8")
    texts = {
        n: (TEXTS / n).read_text(encoding="utf-8")
        for n in ("README-FIRST.txt", "FEEDBACK-FORM.txt")
    }
    for name, text in (SCRIPTS | texts).items():
        # Windows line endings: cmd needs them, and old Notepad shows one long line without.
        (out / name).write_text(text.replace("\n", "\r\n"), encoding="ascii", newline="")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"done: {out} ({size / 2**30:.1f} GB)")
    if args.zip:
        print("zip ...", flush=True)
        archive = zip_bundle(out, out.with_suffix(".zip"))
        print(f"done: {archive} ({archive.stat().st_size / 2**30:.1f} GB)")


def zip_bundle(folder: Path, archive: Path) -> Path:
    """``folder`` as one zip, under its own name, without anything a run left behind."""
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for path in sorted(folder.rglob("*")):
            relative = path.relative_to(folder)
            if path.is_file() and relative.parts[0] not in LOCAL_STATE:
                z.write(path, Path(folder.name) / relative)
    return archive


def _build_exam_app() -> None:
    """The candidates' exam app, built fresh from exam-app/: never an old build."""
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit("Node.js is needed to build the exam app: install it from nodejs.org")
    subprocess.run([npm, "ci", "--no-audit", "--no-fund"], check=True, cwd=EXAM_APP)
    subprocess.run([npm, "run", "build"], check=True, cwd=EXAM_APP)
    if not (BUILT_APP / "index.html").is_file():
        raise SystemExit(f"the exam app build did not produce {BUILT_APP / 'index.html'}")


def _copy_python(dest: Path) -> Path:
    """A standalone CPython, which runs from any folder (unlike the system's or a venv)."""
    with tempfile.TemporaryDirectory() as tmp:
        # Its own folder, apart from the user's Pythons. uv may fail to make its
        # "3.12" shortcut link there (some Windows set-ups forbid links); the
        # interpreter itself is all the bundle needs.
        subprocess.run(["uv", "python", "install", "--install-dir", tmp, "--no-bin", PYTHON])
        found = sorted(Path(tmp).glob(f"cpython-{PYTHON}.*/python.exe"))
        if not found:
            raise SystemExit(f"uv could not install Python {PYTHON}")
        shutil.copytree(found[-1].parent, dest, ignore=SKIP)
    return dest / "python.exe"


def _install_libraries(python: Path, lib: Path) -> None:
    """The locked runtime dependencies (no development tools), for the bundled Python."""
    with tempfile.TemporaryDirectory() as tmp:
        requirements = Path(tmp) / "requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
                "--quiet",
                "-o",
                str(requirements),
            ],
            check=True,
            cwd=ROOT,
        )
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--quiet",
                "--python",
                str(python),
                "--target",
                str(lib),
                "-r",
                str(requirements),
            ],
            check=True,
        )


def _write_config(out: Path) -> None:
    """The test bundle's configuration: data beside the program, the token in its folder."""
    (out / "config").mkdir()
    shutil.copy2(ROOT / "config" / "centre.example.toml", out / "config")
    config = out / "config"
    write_config(config / "centre.example.toml", config / "centre.toml", out / "DEVICE-TOKEN.txt")


if __name__ == "__main__":
    main()
