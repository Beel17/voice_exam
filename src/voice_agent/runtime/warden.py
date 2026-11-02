"""Run a command that must not outlive the service, where the system cannot promise that.

    python -m voice_agent.runtime.warden -- <command> [args...]

Windows ends a service's children with a job object and Linux with a parent-death signal;
macOS has neither. The warden stands in between: the service starts it with a pipe on its
standard input and never writes to it. However the service ends (a crash, a kill, the
power button) the system closes that pipe, the warden reads end-of-file, and it kills its
command at once. Asked to stop (SIGTERM, SIGINT), it passes that on; it exits with the
command's own exit code, so the service can say why its command stopped.
"""

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Sequence


def exit_code(returncode: int) -> int:
    """A child's return code as an exit code: killed by signal n (-n) is 128 + n."""
    return 128 - returncode if returncode < 0 else returncode


def run(command: Sequence[str]) -> int:
    # The command's output goes where the warden's does: the service's log for it. Its
    # input is nothing: the lifeline is the warden's alone.
    child = subprocess.Popen(list(command), stdin=subprocess.DEVNULL)

    def forward(signum: int, _frame: object) -> None:
        if sys.platform == "win32":
            child.terminate()  # Windows has no signals to pass on, only ending
        else:
            child.send_signal(signum)

    for name in ("SIGTERM", "SIGINT"):
        signal.signal(getattr(signal, name), forward)

    def lifeline() -> None:
        sys.stdin.buffer.read()  # returns only at end-of-file: the service is gone
        if child.poll() is None:
            child.kill()

    if not sys.stdin.isatty():  # by hand, at a terminal, there is no lifeline to watch
        threading.Thread(target=lifeline, name="lifeline", daemon=True).start()
    return exit_code(child.wait())


def main(argv: Sequence[str]) -> int:
    if "--" not in argv or argv.index("--") == len(argv) - 1:
        print("usage: python -m voice_agent.runtime.warden -- <command> [args...]", file=sys.stderr)
        return 2
    return run(argv[argv.index("--") + 1 :])


if __name__ == "__main__":
    code = main(sys.argv[1:])
    # Straight out, without Python's shutdown: the lifeline thread may still be blocked
    # reading standard input, and finalising that under it can crash (seen on Windows).
    sys.stderr.flush()
    os._exit(code)
