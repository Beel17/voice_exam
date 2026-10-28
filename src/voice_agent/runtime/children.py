"""Child processes that cannot outlive the service.

If the service is killed (a crash, a power-button shutdown, Task Manager), its model and
speech workers must go with it: a leftover worker holds memory and its port, and the
restarted service could then not start its own. On Windows each child joins a job object
that kills its members when the service's handle to it closes, which the operating system
does however the service exits; on Linux each child asks to be signalled when its parent
dies.
"""

import ctypes
import signal
import sys
from collections.abc import Callable

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
        """On Linux the binding is set up in the child itself, by ``before_exec``."""

    def before_exec() -> Callable[[], None] | None:
        """For subprocess ``preexec_fn``: SIGTERM the child when its parent dies."""
        if not sys.platform.startswith("linux"):
            return None
        libc = ctypes.CDLL(None, use_errno=True)
        set_parent_death_signal = 1  # PR_SET_PDEATHSIG

        def ask() -> None:
            libc.prctl(set_parent_death_signal, signal.SIGTERM)

        return ask
