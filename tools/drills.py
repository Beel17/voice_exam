"""Failure drills: break the real service the ways exam centres will, and check it recovers.

    uv run python -m tools.drills                 # about two minutes
    uv run python -m tools.drills --with-model    # also kills the language model server

Each drill runs the service as its own process, as in a centre, with the sample platform
keeping its state on disk (as a real platform on another machine would), talks to it
over real WebSockets, breaks something, and checks what a candidate would care about:
no confirmed answer lost, none saved twice, the exam resumes where it was, and the
platform ends up with exactly what the candidate confirmed.
"""

import argparse
import asyncio
import hashlib
import json
import os
import random
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from contextlib import closing
from pathlib import Path
from typing import Any, Final

import httpx

from tools.bench import Candidate

LANGUAGE_MODEL: Final = Path("models/qwen2.5-1.5b-instruct-q4_k_m.gguf")
OUTBOX_WAIT_S: Final = 20.0  # the outbox retries every 5 s; generous for a slow PC


class Service:
    """The voice service as a separate process, started and killed like a real one."""

    def __init__(self, work: Path, *, with_model: bool) -> None:
        self.work = work
        self.token = secrets.token_urlsafe(24)
        self.port = _free_port()
        self.platform = work / "platform"
        self.data = work / "data"
        digest = hashlib.sha256(self.token.encode()).hexdigest()
        llm = (
            f'enabled = true\nmodel_file = "{LANGUAGE_MODEL.resolve().as_posix()}"\n'
            f'server_binary = "{Path("models/llama-server/llama-server").resolve().as_posix()}"\n'
            f"port = {_free_port()}"
            if with_model
            else "enabled = false"
        )
        self.config = work / "centre.toml"
        self.config.write_text(
            f'mode = "pc"\nport = {self.port}\ndata_dir = "{self.data.as_posix()}"\n'
            f'models_dir = "{Path("models").resolve().as_posix()}"\n'
            f'device_token_sha256 = ["{digest}"]\nadmin_token_sha256 = ["{digest}"]\n'
            f"[limits]\nmax_messages_per_10s = 10000\n"
            f'[adapter]\nload_candidates = 40\nstate_dir = "{self.platform.as_posix()}"\n'
            f"[llm]\n{llm}\n[speech]\nenabled = false\n",
            encoding="utf-8",
        )
        self.process: subprocess.Popen[bytes] | None = None
        self.url = f"ws://127.0.0.1:{self.port}/v1/session"

    def start(self) -> float:
        """Start and wait until ready; seconds it took."""
        began = time.monotonic()
        log = (self.work / "service.log").open("ab")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "voice_agent"],
            env={**os.environ, "VOICE_AGENT_CONFIG": str(self.config)},
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        log.close()
        while time.monotonic() - began < 180:
            if self.process.poll() is not None:
                raise RuntimeError("the service exited at start; see service.log")
            try:
                if self.get("/readyz").status_code == 200:
                    return time.monotonic() - began
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError("the service did not become ready")

    def kill(self) -> None:
        """Hard kill, as a crash or a pulled plug: nothing is shut down cleanly."""
        assert self.process is not None
        self.process.kill()
        self.process.wait()

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.kill()

    def get(self, path: str) -> httpx.Response:
        return httpx.get(
            f"http://127.0.0.1:{self.port}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=5,
            trust_env=False,
        )

    def pid(self) -> int:
        return int(self.get("/v1/status").json()["process_id"])

    # The platform's side: what it holds, and its failure switches.
    def held(self, candidate_id: str) -> dict[str, Any]:
        path = self.platform / "platform.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        held: dict[str, Any] = state.get(
            candidate_id, {"answers": {}, "submitted": False, "operations": {}}
        )
        return held

    def platform_down(self, down: bool) -> None:
        flag = self.platform / "platform-down"
        flag.touch() if down else flag.unlink(missing_ok=True)

    def platform_slow(self, seconds: float) -> None:
        flag = self.platform / "platform-slow"
        flag.write_text(str(seconds), encoding="ascii") if seconds else flag.unlink(missing_ok=True)


async def until(check: Callable[[], bool], within_s: float) -> float | None:
    """Seconds until ``check`` holds, or None if it never did."""
    began = time.monotonic()
    while time.monotonic() - began < within_s:
        if check():
            return time.monotonic() - began
        await asyncio.sleep(0.25)
    return None


def candidate_id(n: int) -> str:
    return f"load-{n:05d}"


async def sign_in(s: Service, n: int) -> Candidate:
    return await Candidate.sign_in(s.url, s.token, f"LOAD{n:05d}", False)


async def answer(c: Candidate, choice: str) -> None:
    await c.turn(choice)
    await c.turn("yes")


# --- The drills ----------------------------------------------------------------------------
# Each returns (passed, what happened). They run in order on one service.


async def killed_mid_exam(s: Service) -> tuple[bool, str]:
    """The service dies (crash, or the PC restarts) with an answer waiting for its yes.
    An unconfirmed answer is never saved: after the restart it is dropped and the question
    read again (design doc), and the candidate answers it again."""
    c = await sign_in(s, 1)
    await c.turn("start")
    await answer(c, "A")
    await answer(c, "B")
    await c.turn("C")  # question 3, waiting for yes
    s.kill()
    took = s.start()
    c = await sign_in(s, 1)
    resumed, reread = dict(c.state), "Question 3 of 60." in " ".join(c.said)
    held_unconfirmed = dict(s.held(candidate_id(1))["answers"])
    await answer(c, "C")
    await c.close()
    held = s.held(candidate_id(1))["answers"]
    ok = (
        resumed.get("question") == 3
        and resumed.get("answered") == 2
        and resumed.get("pending_confirm") is None
        and reread
        and held_unconfirmed == {"1": "A", "2": "B"}
        and held == {"1": "A", "2": "B", "3": "C"}
    )
    return ok, (
        f"back in {took:.1f} s at question {resumed.get('question')} with "
        f"{resumed.get('answered')} answered; the unconfirmed answer was dropped and the "
        f"question re-read; answered again, the platform holds {held}"
    )


async def killed_while_saving(s: Service) -> tuple[bool, str]:
    """The service dies while the platform is still saving a confirmed answer."""
    c = await sign_in(s, 2)
    await c.turn("start")
    await c.turn("D")
    s.platform_slow(1.5)
    await c.ws.send(json.dumps({"type": "text", "text": "yes"}))
    await asyncio.sleep(0.5)  # the save is under way
    s.kill()
    s.platform_slow(0)
    s.start()
    delivered = await until(lambda: s.held(candidate_id(2))["answers"] == {"1": "D"}, OUTBOX_WAIT_S)
    ops = [op for op in s.held(candidate_id(2))["operations"].values() if op[0] == "save"]
    c = await sign_in(s, 2)
    answered = c.state.get("answered")
    await c.close()
    ok = delivered is not None and len(ops) == 1 and answered == 1
    return ok, (
        f"the confirmed answer reached the platform {delivered:.1f} s after restart, "
        f"saved {len(ops)} time(s); the candidate sees {answered} answered"
        if delivered is not None
        else "the confirmed answer never reached the platform"
    )


async def platform_down_at_save(s: Service) -> tuple[bool, str]:
    """The platform is unreachable when a candidate confirms an answer."""
    c = await sign_in(s, 3)
    await c.turn("start")
    s.platform_down(True)
    await c.turn("C")
    before = len(c.said)
    await c.turn("yes")
    told = next((t for t in c.said[before:] if "waiting to sync" in t), "")
    s.platform_down(False)
    delivered = await until(lambda: s.held(candidate_id(3))["answers"] == {"1": "C"}, OUTBOX_WAIT_S)
    await c.close()
    ok = bool(told) and delivered is not None
    return ok, (
        f'candidate told: "{told[:70]}"; delivered '
        + (f"{delivered:.1f} s after the platform came back" if delivered is not None else "never")
    )


async def platform_down_at_submit(s: Service) -> tuple[bool, str]:
    """The platform is unreachable when a candidate submits the exam."""
    c = await sign_in(s, 4)
    await c.turn("start")
    await answer(c, "A")
    s.platform_down(True)
    for text in ("submit", "yes", "yes"):
        await c.turn(text)
    told = " ".join(c.said[-2:])
    s.platform_down(False)
    submitted = await until(lambda: bool(s.held(candidate_id(4))["submitted"]), OUTBOX_WAIT_S)
    await c.close()
    ok = "waiting to sync" in told and submitted is not None
    return ok, (
        "candidate told it is submitted on this computer and waiting to sync; reached the "
        + (
            f"platform {submitted:.1f} s after it came back"
            if submitted is not None
            else "platform never"
        )
    )


async def connection_dropped_mid_turn(s: Service) -> tuple[bool, str]:
    """The PC's connection drops just after the candidate speaks; it reconnects."""
    c = await sign_in(s, 5)
    await c.turn("start")
    await c.ws.send(json.dumps({"type": "text", "text": "next"}))
    await c.ws.close()
    c = await sign_in(s, 5)
    at = c.state.get("question")
    await c.close()
    return at == 2, f"after reconnecting, at question {at} (moved on exactly once)"


async def power_cut_while_writing(s: Service, rounds: int = 5) -> tuple[bool, str]:
    """The plug is pulled at a random moment while candidates answer as fast as they can."""
    rng = random.Random(7)
    problems: list[str] = []
    for r in range(rounds):
        n = 10 + r

        async def keep_answering(n: int = n) -> None:
            c = await sign_in(s, n)
            await c.turn("start")
            for _ in range(60):
                await answer(c, "ABCD"[rng.randrange(4)])

        task = asyncio.create_task(keep_answering())
        await asyncio.sleep(rng.uniform(0.5, 2.5))
        s.kill()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        with closing(sqlite3.connect(s.data / "sessions.db")) as db:
            if (check := db.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
                problems.append(f"round {r + 1}: database {check}")
        s.start()
        await until(lambda n=n: _settled(s, n), OUTBOX_WAIT_S)  # type: ignore[misc]
        c = await sign_in(s, n)
        answered = c.state.get("answered")
        await c.close()
        held = len(s.held(candidate_id(n))["answers"])
        if answered != held:
            problems.append(f"round {r + 1}: candidate sees {answered}, platform holds {held}")
    return not problems, (
        "; ".join(problems)
        or f"{rounds} pulled plugs: database intact every time, candidate and platform agree"
    )


def _settled(s: Service, n: int) -> bool:
    """No save of candidate n still waiting in the outbox."""
    with closing(sqlite3.connect(s.data / "sessions.db")) as db:
        (pending,) = db.execute(
            "SELECT COUNT(*) FROM outbox WHERE candidate_id = ? AND status = 'pending'",
            (candidate_id(n),),
        ).fetchone()
    return bool(pending == 0)


async def model_server_killed(s: Service) -> tuple[bool, str]:
    """The language model's server dies mid-exam."""
    # A child of the service, or (under the warden, on macOS) a grandchild.
    servers = descendants(s.pid(), "llama-server")
    if not servers:
        return False, "no language model server was running"
    kill_hard(servers[0])
    c = await sign_in(s, 20)
    await c.turn("start")
    await c.turn("next")  # the rules carry on meanwhile
    moved = c.state.get("question") == 2
    await c.close()
    back = await until(lambda: s.get("/readyz").json()["language_model"] == "running", 120)
    ok = moved and back is not None
    return ok, (
        f"rules kept working; model back {back:.0f} s after it died"
        if back is not None
        else "the model did not come back within 2 minutes"
    )


def descendants(pid: int, name: str) -> list[int]:
    """Processes below ``pid``, at any depth, whose program name starts with ``name``."""
    if sys.platform == "win32":
        listing = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | ForEach-Object "
             "{ '{0} {1} {2}' -f $_.ProcessId, $_.ParentProcessId, $_.Name }"],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
    else:
        listing = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,comm="], capture_output=True, text=True, check=True
        ).stdout
    children: dict[int, list[tuple[int, str]]] = {}
    for line in listing.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            program = Path(parts[2].strip()).name
            children.setdefault(int(parts[1]), []).append((int(parts[0]), program))
    found, waiting = [], [pid]
    while waiting:
        for child, program in children.get(waiting.pop(), []):
            waiting.append(child)
            if program.lower().startswith(name.lower()):
                found.append(child)
    return found


def kill_hard(pid: int) -> None:
    """As a crash would: no chance to clean up."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
    else:
        os.kill(pid, signal.SIGKILL)


Drill = Callable[[Service], Awaitable[tuple[bool, str]]]
DRILLS: Final[list[tuple[str, Drill]]] = [
    ("Service killed mid-exam", killed_mid_exam),
    ("Service killed while saving", killed_while_saving),
    ("Platform down at a save", platform_down_at_save),
    ("Platform down at submit", platform_down_at_submit),
    ("Connection dropped mid-turn", connection_dropped_mid_turn),
    ("Plug pulled while writing", power_cut_while_writing),
]


async def run(with_model: bool) -> list[tuple[str, bool, str]]:
    drills = DRILLS + ([("Model server killed", model_server_killed)] if with_model else [])
    results = []
    with tempfile.TemporaryDirectory(prefix="voice-drills-", ignore_cleanup_errors=True) as tmp:
        s = Service(Path(tmp), with_model=with_model)
        s.start()
        try:
            for title, drill in drills:
                print(f"{title} ...", flush=True)
                try:
                    ok, detail = await drill(s)
                except Exception as e:  # a drill that breaks is a failed drill
                    ok, detail = False, f"{type(e).__name__}: {e}"
                    if s.process is None or s.process.poll() is not None:
                        s.start()
                results.append((title, ok, detail))
                print(f"  {'PASS' if ok else 'FAIL'}: {detail}", flush=True)
        finally:
            s.stop()
    return results


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--with-model", action="store_true", help="also kill the model server")
    args = parser.parse_args()
    results = asyncio.run(run(args.with_model))
    passed = sum(ok for _, ok, _ in results)
    print(f"\n{passed}/{len(results)} drills passed")
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
