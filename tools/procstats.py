"""Memory and CPU time of another process, and a description of this machine.

Reads the operating system directly (Windows API, /proc on Linux, ps and sysctl on
macOS) so the benchmark needs no extra dependency on the exam machines it runs on.
"""

import ctypes
import os
import platform
import subprocess
import sys
from pathlib import Path


def rss_bytes(pid: int) -> int:
    """Resident memory (working set on Windows) of process ``pid``."""
    if sys.platform == "win32":
        return _win_memory(pid)
    if sys.platform == "darwin":
        return int(_ps(pid, "rss")) * 1024  # kilobytes
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError(f"no VmRSS for {pid}")


def cpu_seconds(pid: int) -> float:
    """User plus system CPU time used so far by process ``pid``."""
    if sys.platform == "win32":
        return _win_cpu(pid)
    if sys.platform == "darwin":
        return parse_cputime(_ps(pid, "cputime"))
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    ticks = int(fields[11]) + int(fields[12])  # utime, stime
    return ticks / os.sysconf("SC_CLK_TCK")


def total_ram_bytes() -> int:
    if sys.platform == "win32":
        return _win_total_ram()
    if sys.platform == "darwin":
        return int(_sysctl("hw.memsize"))
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("no MemTotal")


def cpu_name() -> str:
    if sys.platform == "win32":
        import winreg

        key = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    elif sys.platform == "darwin":
        return _sysctl("machdep.cpu.brand_string")  # "Apple M1", or an Intel model
    else:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine()


def parse_cputime(text: str) -> float:
    """ps's CPU time, [[days-]hours:]minutes:seconds, as seconds."""
    days, _, clock = text.strip().rpartition("-")
    seconds = 0.0
    for part in clock.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds + (int(days) * 86_400 if days else 0)


def _ps(pid: int, field: str) -> str:
    out = subprocess.run(
        ["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True, check=True
    ).stdout.strip()
    if not out:
        raise RuntimeError(f"no process {pid}")
    return out


def _sysctl(name: str) -> str:
    return subprocess.run(
        ["sysctl", "-n", name], capture_output=True, text=True, check=True
    ).stdout.strip()


def machine() -> dict[str, object]:
    return {
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu_name(),
        "logical_cores": os.cpu_count(),
        "ram_gb": round(total_ram_bytes() / 2**30, 1),
        "python": platform.python_version(),
    }


if sys.platform == "win32":
    from ctypes import wintypes

    _QUERY = 0x1000 | 0x0010  # PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE

    class _MemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    class _MemoryStatus(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    def _open(pid: int) -> int:
        handle: int | None = _kernel32.OpenProcess(_QUERY, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle

    def _win_memory(pid: int) -> int:
        handle = _open(pid)
        try:
            counters = _MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            if not _psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                raise ctypes.WinError(ctypes.get_last_error())
            return int(counters.WorkingSetSize)
        finally:
            _kernel32.CloseHandle(handle)

    def _win_cpu(pid: int) -> float:
        handle = _open(pid)
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not _kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            def seconds(t: wintypes.FILETIME) -> float:
                return ((t.dwHighDateTime << 32) | t.dwLowDateTime) / 1e7  # 100 ns units

            return seconds(kernel) + seconds(user)
        finally:
            _kernel32.CloseHandle(handle)

    def _win_total_ram() -> int:
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(status.ullTotalPhys)
