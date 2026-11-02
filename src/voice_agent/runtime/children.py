"""Child processes that cannot outlive the service.

If the service is killed (a crash, a power-button shutdown, Task Manager), its model and
speech workers must go with it: a leftover worker holds memory and its port, and the
restarted service could then not start its own. On Windows each child joins a job object
that kills its members when the service's handle to it closes, which the operating system
does however the service exits; on Linux each child asks to be signalled when its parent
dies. macOS has neither, so there each child runs under a warden (runtime/warden.py) that
holds a pipe from the service and kills the child when the pipe closes.

Setting VOICE_AGENT_WARDEN=1 uses the warden on any system instead, so that it can be
tested where the system mechanisms would otherwise make it unnecessary.
"""

import ctypes
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence

WARDEN_ENV = "VOICE_AGENT_WARDEN"


def uses_warden() -> bool:
    return sys.platform == "darwin" or os.environ.get(WARDEN_ENV) == "1"


def command_for(command: Sequence[str]) -> list[str]:
    """How to start ``command`` so that it cannot outlive this process."""
    if not uses_warden():
        return list(command)
    return [sys.executable, "-m", "voice_agent.runtime.warden", "--", *command]


def stdin_for() -> int:
    """The child's standard input: the warden's lifeline pipe, or nothing at all. The
    pipe must stay open for the life of the child; asyncio's process object holds it."""
    return subprocess.PIPE if uses_warden() else subprocess.DEVNULL


if sys.platform == "win32":
    from ctypes import wintypes

    _KILL_ON_JOB_CLOSE = 0x2000
    _EXTENDED_LIMIT_INFORMATION = 9
    _SET_QUOTA_AND_TERMINATE = 0x0100 | 0x0001

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _job: int | None = None

    def _the_job() -> int:
        """One job for all children, kept open (deliberately) for the life of the process."""
        global _job
        if _job is None:
            job = _kernel32.CreateJobObjectW(None, None)
            if not job:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE
            if not _kernel32.SetInformationJobObject(
                job, _EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            _job = int(job)
        return _job

    def bind_to_this_process(pid: int) -> None:
        """Make process ``pid`` end when this process ends, however it ends."""
        if uses_warden():
            return  # the warden does it (and must be seen to, in its tests)
        process = _kernel32.OpenProcess(_SET_QUOTA_AND_TERMINATE, False, pid)
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _kernel32.AssignProcessToJobObject(_the_job(), process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _kernel32.CloseHandle(process)

    def before_exec() -> Callable[[], None] | None:
        return None

else:

    def bind_to_this_process(pid: int) -> None:
        """Elsewhere the binding is in the child itself: ``before_exec`` on Linux, the
        warden on macOS."""

    def before_exec() -> Callable[[], None] | None:
        """For subprocess ``preexec_fn``: SIGTERM the child when its parent dies."""
        if not sys.platform.startswith("linux") or uses_warden():
            return None
        libc = ctypes.CDLL(None, use_errno=True)
        set_parent_death_signal = 1  # PR_SET_PDEATHSIG

        def ask() -> None:
            libc.prctl(set_parent_death_signal, signal.SIGTERM)

        return ask
