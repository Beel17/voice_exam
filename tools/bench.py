"""Measure the service on this machine: turn latency, RAM per session, sessions per core.

    uv run python -m tools.bench
    uv run python -m tools.bench --llm-binary models/llama-server --llm-model models/m.gguf

It starts the real service as a separate process (as in a centre), with the fake platform,
and drives it over real WebSockets. Run it on the target hardware and send back the table
(and the JSON written to --out).

Typed turns are timed from a sentence arriving to the first reply text and its first
audio. Spoken turns (a synthetic voice speaking commands as microphone audio) are timed
from releasing the talk key, so they include recognition: that is the number that matters.
"""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from websockets.asyncio.client import ClientConnection, connect

from tools.procstats import cpu_seconds, machine, rss_bytes
from voice_agent.config import pc_llm_threads
from voice_agent.contract import AUDIO_BYTES_PER_SECOND

# Targets from the brief, for the whole turn on the minimum tier. Phase 1 measures the
# part before speech; it should be a small fraction of these.
TARGET_RULES_S = 1.2
TARGET_LLM_S = 2.5

FRAME_BYTES = AUDIO_BYTES_PER_SECOND // 10  # 100 ms, as the client sends

# Commands the spoken phase says aloud (synthetic voice: an easy case for recognition).
SPOKEN = [
    "go to question twelve",
    "where am i",
    "next",
    "previous",
    "repeat",
    "go to question thirty",
    "how much time is left",
    "which questions have i not answered",
]
RULES_SCRIPT = [
    "go to question {n}",
    "A",
    "yes",
    "repeat",
    "where am I",
    "how many have I answered",
]
FREE_SPEECH = [
    "hmm I reckon it is probably B",
    "what was the third option again",
    "tell me everything I have chosen so far",
    "you are reading like a snail",
    "can we go and do the ones I left out",
    "I would like to hear that last bit once more",
    "is it remaining plenty time",
    "on second thoughts leave that one empty",
]


@dataclass
class Samples:
    first_say: list[float] = field(default_factory=list)
    first_audio: list[float] = field(default_factory=list)  # empty when speech is off
    done: list[float] = field(default_factory=list)

    def summary(self) -> dict[str, float | int]:
        def pct(xs: list[float], p: float) -> float:
            xs = sorted(xs)
            return round(xs[min(len(xs) - 1, int(p * len(xs)))] * 1000, 1)

        if not self.done:
            return {"turns": 0}
        summary: dict[str, float | int] = {
            "turns": len(self.done),
            "first_reply_p50_ms": pct(self.first_say, 0.5),
            "first_reply_p95_ms": pct(self.first_say, 0.95),
            "turn_p50_ms": pct(self.done, 0.5),
            "turn_p95_ms": pct(self.done, 0.95),
        }
        if self.first_audio:
            summary["first_audio_p50_ms"] = pct(self.first_audio, 0.5)
            summary["first_audio_p95_ms"] = pct(self.first_audio, 0.95)
        return summary


class Candidate:
    def __init__(self, ws: ClientConnection, speaks: bool) -> None:
        self.ws = ws
        self.speaks = int(speaks)  # whether each say is followed by audio and audio_end
        self.heard = ""  # what the service recognised, last time
        self.said: list[str] = []  # every reply's text, in order
        self.state: dict[str, Any] = {}  # the latest state message

    @classmethod
    async def sign_in(cls, url: str, token: str, reg_no: str, speaks: bool) -> "Candidate":
        ws = await connect(url, max_size=2**20)
        hello = {
            "type": "hello",
            "schema_version": 1,
            "device_id": f"bench-{reg_no}",
            "token": token,
        }
        await ws.send(json.dumps(hello))
        await ws.send(json.dumps({"type": "login", "reg_no": reg_no, "exam_code": "UTME-SAMPLE"}))
        candidate = cls(ws, speaks)
        await candidate._until_state()
        return candidate

    async def turn(self, text: str, samples: Samples | None = None) -> None:
        began = time.perf_counter()
        await self.ws.send(json.dumps({"type": "text", "text": text}))
        first_say, first_audio = await self._until_state(began)
        if samples is not None:
            samples.first_say.append(first_say)
            samples.done.append(time.perf_counter() - began)
            if first_audio is not None:
                samples.first_audio.append(first_audio)

    async def talk(self, pcm: bytes, samples: Samples) -> None:
        """Hold the talk key, send the audio, release: timed from the release, which is
        when the candidate has stopped speaking."""
        await self.ws.send(json.dumps({"type": "talk_start"}))
        for start in range(0, len(pcm), FRAME_BYTES):
            await self.ws.send(pcm[start : start + FRAME_BYTES])
        await self.ws.send(json.dumps({"type": "talk_end"}))
        released = time.perf_counter()
        first_say, first_audio = await self._until_state(released, after_heard=True)
        samples.first_say.append(first_say)
        samples.done.append(time.perf_counter() - released)
        if first_audio is not None:
            samples.first_audio.append(first_audio)

    async def _until_state(
        self, began: float | None = None, *, after_heard: bool = False
    ) -> tuple[float, float | None]:
        """Seconds to the first reply text and to its first audio, if any. A turn ends with
        the ``state`` message and, when a voice is in use, the last ``audio_end``. The
        service also sends a bare ``state`` every 30 s (time left), so only a state that
        follows this turn's reply counts: its ``say`` or ``cue`` or, for speech, its
        ``heard``."""
        start = began if began is not None else time.perf_counter()
        first_say: float | None = None
        first_audio: float | None = None
        open_replies = 0
        stated = False
        mine = False
        while not (stated and open_replies == 0):
            frame = await self.ws.recv()
            now = time.perf_counter() - start
            if isinstance(frame, bytes):
                if mine and first_audio is None:
                    first_audio = now
                continue
            message = json.loads(frame)
            if message["type"] in ("say", "cue") and not after_heard:
                mine = True
            if message["type"] == "say":
                self.said.append(message["text"])
                if mine and first_say is None:
                    first_say = now
                open_replies += self.speaks
            elif message["type"] == "audio_end":
                open_replies -= 1
                await self.ws.send(
                    json.dumps({"type": "playback_done", "utterance_id": message["utterance_id"]})
                )
            elif message["type"] == "heard":
                self.heard = message["text"]
                mine = True
            elif message["type"] == "state":
                self.state = message
                stated = mine
        return (first_say if first_say is not None else time.perf_counter() - start), first_audio

    async def close(self) -> None:
        await self.ws.close()


def script(k: int) -> str:
    n = (k // len(RULES_SCRIPT)) % 60 + 1
    return RULES_SCRIPT[k % len(RULES_SCRIPT)].format(n=n)


async def rules_latency(url: str, token: str, turns: int, speaks: bool) -> Samples:
    samples = Samples()
    c = await Candidate.sign_in(url, token, "LOAD00001", speaks)
    await c.turn("start")
    for k in range(turns):
        await c.turn(script(k), samples)
    await c.close()
    return samples


async def llm_latency(url: str, token: str, turns: int, speaks: bool) -> Samples:
    samples = Samples()
    c = await Candidate.sign_in(url, token, "LOAD00002", speaks)
    await c.turn("start")
    for k in range(turns):
        await c.turn(FREE_SPEECH[k % len(FREE_SPEECH)], samples)
        await c.turn("no")  # leave nothing pending, so every turn starts the same way
    await c.close()
    return samples


def spoken(sentences: list[str], voice_dir: Path) -> list[bytes]:
    """Each sentence said by a Piper voice, as the PC would send it: 16 kHz 16-bit PCM,
    with the moment of silence before speech that holding the talk key gives."""
    import numpy as np
    import sherpa_onnx

    vits = sherpa_onnx.OfflineTtsVitsModelConfig(
        model=str(next(voice_dir.glob("*.onnx"))),
        tokens=str(voice_dir / "tokens.txt"),
        data_dir=str(voice_dir / "espeak-ng-data"),
    )
    model = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=1)
    tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=model))
    clips = []
    for sentence in sentences:
        audio = tts.generate(sentence, sid=0, speed=1.0)
        x = np.concatenate([np.zeros(int(0.3 * audio.sample_rate)), np.asarray(audio.samples)])
        n = int(len(x) * 16000 / audio.sample_rate)
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
        clips.append(bytes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()))
    return clips


async def spoken_latency(
    url: str, token: str, turns: int, speaks: bool, voice_dir: Path
) -> dict[str, Any]:
    """The full spoken turn: recognition, understanding, the reply's first audio."""
    clips = spoken(SPOKEN, voice_dir)
    samples = Samples()
    misheard: list[tuple[str, str]] = []
    c = await Candidate.sign_in(url, token, "LOAD00004", speaks)
    await c.turn("start")
    for k in range(turns):
        said = SPOKEN[k % len(SPOKEN)]
        c.heard = ""
        await c.talk(clips[k % len(SPOKEN)], samples)
        if c.heard.lower().strip(" .?!") != said:
            misheard.append((said, c.heard))
    await c.close()
    return samples.summary() | {
        "heard_exactly": f"{turns - len(misheard)}/{turns}",
        "misheard": misheard[:5],
    }


async def ram_per_session(
    url: str, token: str, pid: int, sessions: int, speaks: bool
) -> dict[str, Any]:
    first = await Candidate.sign_in(url, token, "LOAD00003", speaks)
    await first.turn("start")  # warms shared caches, so they are not charged to sessions
    await asyncio.sleep(0.5)
    before = rss_bytes(pid)
    held = []
    for k in range(sessions):
        c = await Candidate.sign_in(url, token, f"LOAD{k + 10:05d}", speaks)
        await c.turn("start")
        held.append(c)
    await asyncio.sleep(0.5)
    after = rss_bytes(pid)
    for c in [first, *held]:
        await c.close()
    return {
        "sessions": sessions,
        "service_rss_mb": round(after / 2**20, 1),
        "per_session_kb": round((after - before) / sessions / 1024, 1),
    }


async def saturation(
    url: str,
    token: str,
    pid: int,
    candidates: int,
    seconds: float,
    turn_interval_s: float,
    speaks: bool,
) -> dict[str, Any]:
    """Many candidates with no pause between turns: the service's ceiling."""
    clients = [
        await Candidate.sign_in(url, token, f"LOAD{k + 1000:05d}", speaks)
        for k in range(candidates)
    ]
    for c in clients:
        await c.turn("start")
    samples = Samples()
    stop = time.perf_counter() + seconds

    async def drive(c: Candidate, offset: int) -> None:
        k = offset
        while time.perf_counter() < stop:
            await c.turn(script(k), samples)
            k += 1

    cpu_before, wall_before = cpu_seconds(pid), time.perf_counter()
    await asyncio.gather(*(drive(c, k * 7) for k, c in enumerate(clients)))
    cpu_used = cpu_seconds(pid) - cpu_before
    wall = time.perf_counter() - wall_before
    for c in clients:
        await c.close()
    turns_per_cpu_s = len(samples.done) / cpu_used if cpu_used else 0.0
    return {
        "candidates": candidates,
        **samples.summary(),
        "turns_per_second": round(len(samples.done) / wall, 1),
        "service_cpu_cores_busy": round(cpu_used / wall, 2),
        "turns_per_cpu_second": round(turns_per_cpu_s, 1),
        "assumed_seconds_between_turns": turn_interval_s,
        "sessions_per_core_rules_path": int(turns_per_cpu_s * turn_interval_s),
    }


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def start_service(
    workdir: Path, args: argparse.Namespace, token: str
) -> tuple[subprocess.Popen[bytes], int, int]:
    port = free_port()
    llm = "enabled = false"
    if args.llm_model:
        llm = (
            f'enabled = true\nserver_binary = "{Path(args.llm_binary).as_posix()}"\n'
            f'model_file = "{Path(args.llm_model).as_posix()}"\nport = {free_port()}\n'
            f"threads = {args.llm_threads}\nslots = {args.llm_slots}\ntimeout_s = 10"
        )
    config = workdir / "centre.toml"
    config.write_text(
        f'mode = "server"\nhost = "127.0.0.1"\nport = {port}\ndata_dir = "{workdir.as_posix()}"\n'
        f'device_token_sha256 = ["{hashlib.sha256(token.encode()).hexdigest()}"]\n'
        f'admin_token_sha256 = ["{hashlib.sha256(token.encode()).hexdigest()}"]\n'
        f"[limits]\nmax_messages_per_10s = 10000\n"
        f"[adapter]\nload_candidates = {args.sessions + args.candidates + 1100}\n[llm]\n{llm}\n"
        f"[speech]\nenabled = {'false' if args.no_speech else 'true'}\n",
        encoding="utf-8",
    )
    with (workdir / "service.log").open("wb") as log:  # the child keeps its own handle
        process = subprocess.Popen(
            [sys.executable, "-m", "voice_agent"],
            env={**os.environ, "VOICE_AGENT_CONFIG": str(config)},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        try:
            ready = httpx.get(f"http://127.0.0.1:{port}/readyz", timeout=1, trust_env=False)
            if ready.status_code == 200:
                if args.llm_model and ready.json()["language_model"] != "running":
                    process.kill()
                    raise SystemExit(_failure("the language model did not start", workdir))
                # The service's own PID: on Windows, sys.executable in a virtual
                # environment is a launcher that starts the real interpreter as a child.
                status = httpx.get(
                    f"http://127.0.0.1:{port}/v1/status",
                    headers={"Authorization": f"Bearer {token}"},
                    trust_env=False,
                )
                return process, port, int(status.json()["process_id"])
        except httpx.HTTPError:
            pass
        if process.poll() is not None:
            raise SystemExit(_failure("the service exited", workdir))
        time.sleep(0.2)
    process.kill()
    raise SystemExit(_failure("the service did not become ready", workdir))


def _failure(what: str, workdir: Path) -> str:
    log = (workdir / "service.log").read_text(encoding="utf-8", errors="replace")
    tail = "\n".join(log.splitlines()[-25:])
    return f"{what}. Last lines of the service log:\n{tail}"


async def run(args: argparse.Namespace) -> dict[str, Any]:
    token = secrets.token_urlsafe(24)
    # Windows may hold the database a moment after the service exits; that is not a failure.
    with tempfile.TemporaryDirectory(prefix="voice-bench-", ignore_cleanup_errors=True) as tmp:
        process, port, pid = start_service(Path(tmp), args, token)
        url = f"ws://127.0.0.1:{port}/v1/session"
        ready = httpx.get(f"http://127.0.0.1:{port}/readyz", trust_env=False).json()
        speaks = ready.get("voice") == "running"
        try:
            report: dict[str, Any] = {"machine": machine(), "service": ready}
            report["service_rss_at_start_mb"] = round(rss_bytes(pid) / 2**20, 1)
            print("rules path ...", flush=True)
            report["rules_path"] = (await rules_latency(url, token, args.turns, speaks)).summary()
            if args.llm_model:
                print("language model path ...", flush=True)
                report["llm_path"] = (
                    await llm_latency(url, token, args.llm_turns, speaks)
                ).summary()
            voice = Path(args.voice_dir)
            if speaks and voice.is_dir():
                print("spoken turns ...", flush=True)
                report["spoken_path"] = await spoken_latency(
                    url, token, args.spoken_turns, speaks, voice
                )
            print("memory per session ...", flush=True)
            report["memory"] = await ram_per_session(url, token, pid, args.sessions, speaks)
            print("saturation ...", flush=True)
            report["saturation"] = await saturation(
                url, token, pid, args.candidates, args.seconds, args.turn_interval, speaks
            )
        finally:
            process.terminate()
            process.wait(10)
    return report


def show(report: dict[str, Any]) -> None:
    m = report["machine"]
    print(f"\n{m['cpu']} | {m['logical_cores']} logical cores | {m['ram_gb']} GB | {m['os']}")
    rows = [("rules path, typed", report["rules_path"], TARGET_RULES_S)]
    if "spoken_path" in report:
        rows.insert(0, ("spoken, from release", report["spoken_path"], TARGET_RULES_S))
    if "llm_path" in report:
        rows.append(("language model path", report["llm_path"], TARGET_LLM_S))
    rows.append(("rules path, saturated", report["saturation"], TARGET_RULES_S))
    heard = "first audio" if "first_audio_p50_ms" in rows[0][1] else "first reply text"
    print(f"\n{'':24}{'turns':>7}{heard + ' p50/p95':>26}{'whole turn p50/p95':>22}  target")
    for name, s, target in rows:
        key = "first_audio" if "first_audio_p50_ms" in s else "first_reply"
        first = f"{s[key + '_p50_ms']:.0f} / {s[key + '_p95_ms']:.0f} ms"
        whole = f"{s['turn_p50_ms']:.0f} / {s['turn_p95_ms']:.0f} ms"
        print(f"{name:24}{s['turns']:>7}{first:>26}{whole:>22}  < {target} s")
    mem, sat = report["memory"], report["saturation"]
    print(
        f"\nservice RAM: {report['service_rss_at_start_mb']} MB at start, "
        f"{mem['service_rss_mb']} MB with {mem['sessions'] + 1} sessions; "
        f"{mem['per_session_kb']} KB per session"
    )
    print(
        f"saturated: {sat['candidates']} candidates, {sat['turns_per_second']} turns/s, "
        f"{sat['service_cpu_cores_busy']} cores busy, "
        f"{sat['turns_per_cpu_second']} turns per CPU-second"
    )
    print(
        f"=> about {sat['sessions_per_core_rules_path']} sessions per core on the rules path "
        f"(one turn every {sat['assumed_seconds_between_turns']:.0f} s per candidate; "
        + (
            "synthesis included, recognition not: "
            if report["service"].get("voice") == "running"
            else "without speech: "
        )
        + "replies are typed here, so phase 3 measures recognition load separately)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--turns", type=int, default=240, help="rules-path turns, one candidate")
    parser.add_argument("--llm-turns", type=int, default=24)
    parser.add_argument("--sessions", type=int, default=50, help="live sessions for RAM")
    parser.add_argument("--candidates", type=int, default=20, help="concurrent, for saturation")
    parser.add_argument("--seconds", type=float, default=15.0, help="saturation run length")
    parser.add_argument(
        "--turn-interval",
        type=float,
        default=10.0,
        help="seconds between a candidate's turns, for sessions per core",
    )
    parser.add_argument("--llm-binary", default="models/llama-server/llama-server")
    parser.add_argument("--llm-model", help="GGUF file; enables the model path")
    parser.add_argument(
        "--llm-threads", type=int, default=pc_llm_threads(), help="default: as in PC mode"
    )
    parser.add_argument("--llm-slots", type=int, default=1)
    parser.add_argument("--no-speech", action="store_true", help="text and keys only")
    parser.add_argument("--spoken-turns", type=int, default=40)
    parser.add_argument(
        "--voice-dir",
        default="models/speech/tts/vits-piper-en_US-lessac-medium",
        help="voice the benchmark speaks with (any shipped voice)",
    )
    parser.add_argument("--out", default="bench-report.json")
    args = parser.parse_args()
    report = asyncio.run(run(args))
    show(report)
    Path(args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nfull report: {args.out}")


if __name__ == "__main__":
    main()
