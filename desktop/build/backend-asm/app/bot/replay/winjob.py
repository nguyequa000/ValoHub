"""Windows Job Object helper: make this process's descendants die with it.

``assign_self_to_kill_on_close_job()`` puts the current process into a new job with
KILL_ON_JOB_CLOSE.  When this process dies (even via ``taskkill /F``) the OS closes the last
handle and kills every process that is still in the job (e.g. the dotnet decoder).  Nested jobs
(Win8+) are supported, so this works under a parent (Node/libuv) job.  Failure logs and returns
False; it never raises.
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

_JOB_HANDLE = None  # keep alive for the process lifetime


def assign_self_to_kill_on_close_job() -> bool:
    global _JOB_HANDLE
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            log.warning("CreateJobObjectW failed: %s", ctypes.get_last_error())
            return False
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            log.warning("SetInformationJobObject failed: %s", ctypes.get_last_error())
            k32.CloseHandle(job)
            return False
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            log.warning("AssignProcessToJobObject failed: %s", ctypes.get_last_error())
            k32.CloseHandle(job)
            return False
        _JOB_HANDLE = job
        return True
    except Exception:  # noqa: BLE001 - must never crash the host
        log.warning("job object setup failed", exc_info=True)
        return False
