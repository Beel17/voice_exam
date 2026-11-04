"""Zip the built exam app for bundling inside a secure browser.

    uv run python -m tools.make_browser_app     # writes dist/exam-app-<version>.zip

The zip holds the app exactly as the engine serves it at /exam/ (built fresh; needs
Node.js), with BUNDLING.txt saying what the browser's team sets: the engine's address in
engine-config.js, and the browser's origin in the engine's allowed_origins.
"""

import argparse
import tomllib
import zipfile
from pathlib import Path

from tools.make_bundle import BUILT_APP, _build_exam_app

ROOT = Path(__file__).parents[1]
GUIDE = """Exam app, for bundling inside a secure browser
==============================================

These files are the candidates' exam app, already built: plain HTML, JavaScript and CSS.
Bundle the whole folder with the browser and open index.html. All paths are relative.

1. Before compiling the browser, set the engine's address in engine-config.js:
     window.EXAM_ENGINE = "https://exam-server:8765";
   (the centre server's name or address, and its port). Nothing else needs changing.

2. In the engine's configuration (/var/lib/voice-exam/centre.toml on a Linux server), let
   the browser's pages call it, with the origin the browser gives its local pages, then
   restart the engine (sudo systemctl restart voice-exam):
     allowed_origins = ["app://exam-browser"]
   It goes near the top of the file, with the other top-level settings.

3. Browsers allow the microphone (audio mode) only on secure pages: serve the pages over
   HTTPS (the engine itself, with sudo voice-exam certificate ..., or a proxy in front of
   it), or let the compiled app call the engine over HTTP and use the microphone.

4. To sign a candidate in without typing, open index.html?candidate=CANDIDATE_ID, or set
   window.EXAM_CANDIDATE = "CANDIDATE_ID" before the app loads. The engine accepts the
   candidate ID or the examination number, and with look_up_candidates under [sync] asks
   the platform about candidates it does not hold yet.

The browser must allow: WebSockets to the engine, the microphone (audio mode), and Web
Audio with an AudioWorklet loaded from the app's own capture-worklet.js. Without the
microphone, audio mode still works by keyboard.

Alternatively, if the browser can open a URL, skip all this: point it at
https://exam-server:8765/exam/ and the engine serves the app itself.
"""


def write_zip(out: Path, app: Path = BUILT_APP) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(app.rglob("*")):
            if path.is_file():
                z.write(path, f"exam-app/{path.relative_to(app).as_posix()}")
        z.writestr("exam-app/BUNDLING.txt", GUIDE.replace("\n", "\r\n"))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    print("exam app ...", flush=True)
    _build_exam_app()
    out = ROOT / "dist" / f"exam-app-{version}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_zip(out)
    print(f"done: {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
