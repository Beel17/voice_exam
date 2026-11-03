# Voice Exam Agent

Offline, CPU-only voice service that lets blind candidates take computer-based exams by
conversation. The design doc ("Voice Exam Agent — Service Design") is the source of truth.

## Development

```bash
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Running it (phase 1: typed text, sample exams)

1. Issue a device token. Keep the token for the client; put the digest in the config.

   ```bash
   uv run python tools/issue_token.py
   ```

2. Copy `config/centre.example.toml` to `config/centre.toml` (not tracked) and set
   `device_token_sha256 = ["<digest>"]`. Without model files, set `[llm] enabled = false`
   or leave it on: the service logs that the files are missing and runs on rules only.

3. Start the service, then take an exam by typing in another terminal:

   ```bash
   uv run python -m voice_agent
   uv run python tools/text_client.py --token <token>
   ```

   The sample exam is `UTME-SAMPLE`; candidates `10000001AA` to `10000003AA`.

Every setting can be overridden by environment variables prefixed `VOICE_AGENT_`, and
`VOICE_AGENT_CONFIG` points at another config file.

## Language model

Prepare `models/` once, on any machine with internet (about 2 GB), then copy it with
the package; exam PCs never need internet:

```bash
uv run python -m tools.fetch_models
```

It downloads pinned versions of the llama.cpp server and the Qwen2.5 models, checks each
against its published SHA-256, and writes `models/manifest.json`.

Then build the server the service actually runs (needs git and Visual Studio Build Tools
with the C++ workload, once, on the build machine):

```bash
uv run python -m tools.build_llama
```

llama.cpp's released Windows server uses the PC's own C++ runtime and crashes at once
where that is older than it expects (a work PC with a 2018 runtime did). This builds the
same pinned version as two self-contained programs in `models/llama-server/`, one using
AVX2 and `llama-server-compat.exe` for older processors, checks that neither loads any
runtime DLL, and updates the manifest. The service picks the one the processor can run.

At start-up the service
verifies the files it uses against the manifest, starts the model on 127.0.0.1, warms it,
and restarts it if it dies. If anything is missing or does not match, it logs why and
runs the exam on rules only.

## Speech

`tools.fetch_models` also fetches the speech models: a streaming English recogniser
(Kroko, CC-BY-SA) and three Piper voices (US female, British male, British female; set
`[speech] voice_dir`). Audio goes up as 16 kHz PCM while the talk key is held; each
`say` comes back followed by 22.05 kHz PCM frames and `audio_end`. Speed runs from 0.8x
to 2.5x with unchanged pitch. If the speech files are missing or do not match the
manifest, the service says why in its log: no recognition means keyboard mode; no voice
means replies reach the PC as text only.

## Voice client

The service serves the exam PC's voice client at `/client/` (and redirects `/` there).
Open `http://127.0.0.1:8765/` on the PC, enter the device token, registration number and
exam code, then hold **Space** to talk; keys 1-5, Enter, Esc, R, N and P work too.
Browsers only allow the microphone on secure pages: `http://127.0.0.1` (PC mode) is
fine, but a centre server reached over the network needs HTTPS (phase 3).

## Exam screen

The candidates' exam app (React and TypeScript, in `exam-app/`) is served at
`http://127.0.0.1:8765/exam/`. Build it once on the build machine (needs Node.js; the
result is in `src/voice_agent/exam/`, not kept in git):

```bash
cd exam-app
npm install
npm run build
```

On first use the invigilator enters the PC's device token; it is kept in the browser.
Each candidate then sees a short starter guide, signs in with their examination number,
checks their name and photo against the instructions, and takes the exam: the question
bar per subject (answered, unanswered, marked to revisit), click to answer (saved at
once), Revisit Later, a calculator, and a two-step submit with a summary. A reload or a
dropped connection resumes the exam where it was. Keys: A to E answer, the arrows move.
`npm run dev` serves it with live reloading against a service on port 8765.

**Audio mode** (for blind and visually impaired candidates) is the same app, chosen on
the sign-in screen ("Audio mode") and remembered by the PC. The candidate presses Space,
hears a welcome, a sound in each ear, and a microphone check, then signs in by saying
their examination number ("two zero two six … P for Papa, R for Romeo"); the service
reads it back, and "yes" (or Enter) signs in, after which it says the candidate's name
as the identity check. The invigilator can type the number instead. The exam is then
spoken: hold Space to talk; keys 1 to 5 choose, Enter is yes, Esc no, R repeats, N and
P move, F flags, T tells the time, and S submits (asked twice). The screen shows the
question in large type and what is being said, for candidates with some sight and for
the invigilator. After a reload, Space turns the sound back on and the question is read
again. Browsers allow the microphone on this PC (`127.0.0.1`) or over HTTPS only.

Candidates who read take the same exam on screen (standard mode), over the same
WebSocket: `hello` with `"audio": false` (replies come as text, no speech is made), then
`login` with the examination number alone. The service sends `details` (the exam's
sections and, for the identity check, the candidate's name, number, seat and whether a
photo is held: `GET /v1/sessions/{id}/photo` with the device token), a `question`
message whenever a question becomes current (its text and options, verbatim), and a
`state` after every change carrying the navigator: answered and flagged questions, the
current answer, any pending confirmation, and how the exam ended. Clicks arrive as `act`
messages, each the same action as its voice command, except that selecting an option
saves it at once and stays on the question. Submitting still asks twice. Flags ("flag
this", "which questions are flagged") are the candidate's own reminders and never reach
the platform.

## On a centre server

One machine can serve every candidate in the centre: install it there, then in its
`centre.toml` set `mode = "server"` (it then listens on the LAN, not only on itself) and
open its port (8765) in the firewall. Candidate PCs install nothing: they open
`https://<server>:8765/exam/` in a browser, each with a device token (the server accepts
one per PC, or one shared for a test).

Audio mode across the LAN needs HTTPS, because browsers allow the microphone only over
HTTPS or on the machine itself. Make a certificate for the server (needs openssl, which
Git for Windows has) and name it under `[tls]`:

```bash
uv run python -m tools.make_certificate --name exam-server --ip 192.168.1.10 --out tls
```

```toml
[tls]
cert_file = "C:/ProgramData/VoiceExam/tls/exam-server.crt"
key_file = "C:/ProgramData/VoiceExam/tls/exam-server.key"
```

Install the `.crt` on each candidate PC as a trusted root (double-click it: Install
Certificate, Local Machine, Trusted Root Certification Authorities); a centre with its
own certificate authority uses a certificate from it instead. With HTTPS on, the service
serves only HTTPS, so the server's own shortcuts need `https://` too. Standard mode works
over plain HTTP. In server mode, speech and the language model run on the server's CPU
for every voice candidate; `tools.bench` measures how many it can carry.

### The app inside a secure browser

A secure browser can open `https://<server>:8765/exam/`, or carry the app's files itself.
For that, `uv run python -m tools.make_browser_app` writes `dist/exam-app-<version>.zip`:
the built app (relative paths, so it works from any folder) and `BUNDLING.txt`. Before
compiling the browser, its team sets the engine's address in `engine-config.js`
(`window.EXAM_ENGINE = "https://exam-server:8765";`), and the engine's configuration
allows the browser's origin at the top level of `centre.toml`:
`allowed_origins = ["app://exam-browser"]` (only those pages may then read the photo and
the sound cues; the photo still needs the device token).

### On a Linux server

For an x86-64 Linux server with systemd (Ubuntu 22.04 or newer, or similar), build the
package on the build machine (it carries the exam app already built, so the server needs
no Node.js), copy it over, and install it as root:

```bash
uv run python -m tools.make_linux_package    # dist/voice-exam-linux-<version>.tar.gz
```

```bash
tar xzf voice-exam-linux-0.1.0.tar.gz
sudo voice-exam-linux-0.1.0/deploy/linux/install.sh    # or add: --models /path/to/models
```

It needs internet once, and `uv`. It installs the program in `/opt/voice-exam`, keeps
everything that changes in `/var/lib/voice-exam`, runs it in server mode as the systemd
service `voice-exam` (user `voiceexam`, logs in `journalctl -u voice-exam`), and adds the
`voice-exam` command for the centre's tasks: `sudo voice-exam token`, `store list`,
`store import PAPER.zip`, `store open EXAM_ID`, `sync pull`, `sync push`, `sync status`,
and `certificate --name NAME --ip ADDRESS` (HTTPS, then a restart). Running the installer
again upgrades the program and keeps the data; `deploy/linux/uninstall.sh` removes it
(keeping the data unless given `--purge`). On a VM, give it the host's CPU features
(AVX2), or the language model is switched off.

## Benchmark

Run on the target machine and send back the printed table and `bench-report.json`:

```bash
uv run python -m tools.bench
uv run python -m tools.bench --llm-model models/qwen2.5-1.5b-instruct-q4_k_m.gguf
```

It starts the service as its own process and reports per-turn latency (rules path, and
the model path when a model is given), RAM per live session, and sessions per core.
With speech on, a synthetic voice also speaks commands as microphone audio, and the
spoken row times the whole turn from releasing the talk key to the reply's first audio.

## Exam store and cloud sync

The centre runs exams from its own exam store (`[adapter] kind = "local"`): a SQLite
database, `<data_dir>/exams.db`, holding exam papers, candidates (examination number,
name, photo, audio mode) and every answer. Set it up and load papers with:

```bash
uv run python -m voice_agent.examstore init
uv run python -m voice_agent.examstore import paper.zip     # or: sync pull
uv run python -m voice_agent.examstore list
uv run python -m voice_agent.examstore open EXAM_ID         # candidates may now sign in
uv run python -m voice_agent.examstore sample practice.zip  # a practice paper to try
```

Candidates sign in with their examination number alone, among the open papers. Each gets
their own version of the paper (questions shuffled within subjects, options shuffled
unless the package keeps them), but answers are stored against the paper's own numbers
and letters, so marking never needs to know about versions.

Sync with the cloud is configured under `[sync]` (`url`, `centre_id`; the token goes in
`<data_dir>/sync-token.txt`):

```bash
uv run python -m voice_agent.sync pull     # before the exam: download this centre's papers
uv run python -m voice_agent.sync push     # after: upload every answer and submission
uv run python -m voice_agent.sync status
```

The cloud implements the API in `spec/sync.openapi.json` (three calls). Retrying is
always safe: the cloud must accept an operation it already holds without applying it
again. `python -m voice_agent.sync.standin` is a stand-in cloud to develop against.

## Failure drills

```bash
uv run python -m tools.drills                 # about two minutes
uv run python -m tools.drills --with-model    # also kills the language model's server
```

Each drill runs the service as its own process, with the sample platform keeping its
state on disk as a real one on another machine would, breaks something, and checks what
a candidate cares about: the service killed mid-exam and while saving, the platform down
at a save and at submit, the connection dropped mid-turn, the plug pulled at random while
answers are being written (database integrity checked each time), and the model's server
killed. The last runs a centre on its exam store against the stand-in cloud, all as
separate processes: papers pulled and opened, a candidate answers and submits, the cloud
is down at the first upload and the service is killed, and the cloud must still end up
with every result exactly once. A few of them also run in the test suite.

## Installer

```bash
uv run python -m tools.make_bundle --out build/installer/voice-exam
uv run python -m tools.make_installer
```

Builds `dist/voice-exam-setup-<version>.exe` (about 1.3 GB) with Inno Setup 6, which
must be installed on the build machine. Running it needs administrator rights. It puts the
program in Program Files and everything that changes in `C:\ProgramData\VoiceExam`: the
configuration and device token (written on first install, kept on upgrade), the exam
store, sessions and logs. It installs and starts the Windows service, running on the exam
store, and adds Start-menu shortcuts to the exam app, the status page, the configuration
(set `[sync]` there), the device token, the logs, and "Centre tools": double-clicks to
list, import, open and close papers, and to download papers and upload results (each asks
for administrator rights). No candidate can sign in until a paper is loaded and opened.
Building needs Node.js too: the bundle builds the exam app fresh. Uninstalling removes the
service and the program but keeps `ProgramData\VoiceExam`, which may hold answers not yet
delivered to the platform. It is not code-signed yet, so Windows warns before running it.

## Test bundle

To try it on a PC with nothing installed, build a folder holding its own Python, the
libraries, the exam app (built fresh, so Node.js is needed), the models, and a config with
a fresh device token (needs internet once, after `tools/fetch_models.py`). It runs on the
exam store, with the practice paper loaded on first start:
examination numbers `20260001PR` to `20260008PR`, the last two registered for audio mode.

```bash
uv run python -m tools.make_bundle --zip
```

It can also run as a Windows service, as an exam centre would: "Install as a Windows
service" (asks for administrator rights) sets it up with WinSW (pinned and checksummed by
`fetch_models`, as `voice-exam-service.exe` with its settings in the `.xml` beside it). It
then starts with the PC, has no window, restarts itself if it stops, and logs to
`data/logs/`.

Share `dist/voice-exam.zip`, never a folder that has been run: running leaves sessions in
`data/`, and a tester signing in on someone else's sessions resumes their exam.

Copy `dist/voice-exam` (about 1.5 GB) to a flash drive. It is ready for blind testers:
one "Start exam" script per voice (the testers choose), which starts the service and
opens the exam page when it is ready; "Collect feedback", which writes each session as a
readable transcript (`tools/export_sessions.py`) next to a feedback form; "Start over";
and "Measure this PC". `README-FIRST.txt` inside is the guide for whoever helps the
tester; its text and the form are in `tools/bundle/`.

The Mac version (Apple Silicon) is the same, with `.command` files to double-click and
llama.cpp's own Mac model server, which `fetch_models` unpacks on a Mac. Build it on a Mac:

```bash
uv run python -m tools.make_mac_bundle --zip --check
```

or without one: on GitHub, Actions, then "mac-bundle", then "Run workflow". It builds the
zip on a GitHub Mac, `--check` starts the bundled service once and waits until the model,
speech and voice all run, and the zip is kept under the run's Artifacts for two weeks. Its
files are not signed by an Apple developer yet, so its `README-FIRST.txt` begins with the
one Terminal command that tells macOS to trust the folder.

## Layout

See the design doc's "Code base structure". Phase 1 covers `contract.py`, `config.py`,
`session/`, `understanding/`, `adapters/` (fake), `api/` and `spec/`.
