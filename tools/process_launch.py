"""Start the build, test, PIT and suite processes of a run and stop their whole process tree on a timeout.

The only module of the package that starts such a process (git has its own, ``git_command``).  The host delivery
profile leaves it out: there a person runs every command (``execution.mode: human``) and ``auto`` is unavailable.
Callers import it lazily, inside the function that launches.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from tools.run_tests import _PROCESS_STOP_ATTESTATION, _SCOPE_MARKER_NAME, _TIMEOUT, PROCESS_OUTPUT_TAIL_BYTES, ProcessOutcome


def run_completed(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """``subprocess.run`` for the suite command (``suite_run.RUNNER`` is the seam tests replace)."""
    return subprocess.run(*args, **kwargs)


def run_subprocess(command: list[str], project: Path, environment: Mapping[str, str] | None = None, *, timeout: int = _TIMEOUT) -> ProcessOutcome:
    # ponytail: temporary files bound RAM; add a disk quota if noisy runners require it.
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        return _run_subprocess(command, project, environment, timeout, stdout_file, stderr_file)


def _windows_console_encodings() -> tuple[str, ...]:
    """Code pages a Windows console child writes when its output is not UTF-8."""
    if os.name != "nt":
        return ()
    names: list[str] = []
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        for reader in (kernel32.GetConsoleOutputCP, kernel32.GetOEMCP):
            code_page = int(reader())
            if code_page and code_page != 65001 and f"cp{code_page}" not in names:
                names.append(f"cp{code_page}")
    except (AttributeError, OSError, ValueError):
        pass
    return tuple(names)


def _decode_process_output(data: bytes, fallback_encodings: Sequence[str] | None = None) -> str:
    """Decode as UTF-8, then as the Windows console code page, never failing."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for encoding in (_windows_console_encodings() if fallback_encodings is None else fallback_encodings):
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _output_tail(stream: Any) -> str:
    size = stream.seek(0, os.SEEK_END)
    stream.seek(max(0, size - PROCESS_OUTPUT_TAIL_BYTES))
    data = stream.read(PROCESS_OUTPUT_TAIL_BYTES)
    if size > PROCESS_OUTPUT_TAIL_BYTES:
        # Never publish the suffix of a truncated credential-bearing line.
        data = data.partition(b"\n")[2]
    return _decode_process_output(data)


def _run_subprocess(command: list[str], project: Path, environment: Mapping[str, str] | None, timeout: int, stdout_file: Any, stderr_file: Any) -> ProcessOutcome:
    scope_marker = uuid.uuid4().hex
    from tools.child_environment import build_environment

    # The build, the tests and PIT never see a secret-like variable of the operator's shell (S1d).
    child_environment = build_environment(environment)
    # The marker is inherited by every descendant, including one that leaves the
    # process group with setsid, so a POSIX stop proof can account for it.
    child_environment[_SCOPE_MARKER_NAME] = scope_marker
    kwargs: dict[str, Any] = {
        "cwd": project,
        "stdout": stdout_file,
        "stderr": stderr_file,
        "env": child_environment,
        "shell": False,
    }
    windows_job: int | None = None
    try:
        if os.name == "nt":
            process, windows_job = _start_windows_scoped_process(command, kwargs)
        else:
            kwargs["start_new_session"] = True
            process = subprocess.Popen(command, **kwargs)
    except OSError:
        return ProcessOutcome(127, "", "", "OS_ERROR")
    try:
        process.wait(timeout=timeout)
        return ProcessOutcome(process.returncode, _output_tail(stdout_file), _output_tail(stderr_file))
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            stop_proof = _stop_windows_scope(process, windows_job)
            windows_job = None
        else:
            stop_proof = _stop_posix_scope(process, scope_marker)
        return ProcessOutcome(
            124, _output_tail(stdout_file), _output_tail(stderr_file), "TIMEOUT",
            process_scope_stopped=stop_proof is not None, stop_proof=stop_proof,
            _stop_attestation=_PROCESS_STOP_ATTESTATION if stop_proof is not None else None,
        )
    finally:
        if windows_job is not None:
            _close_windows_handle(windows_job)


# --- POSIX process scope -----------------------------------------------------

_STOP_WAIT_SECONDS = 5.0
_STOP_POLL_SECONDS = 0.05


def _posix_process_table(scope_marker: str | None = None) -> list[tuple[int, int, int, bool]] | None:
    """Return ``(pid, ppid, pgid, carries_marker)`` for every live process.

    Zombies are not live.  ``None`` means the host cannot enumerate processes, in
    which case no stop proof may be issued.
    """
    proc = Path("/proc")
    rows: list[tuple[int, int, int, bool]] = []
    if (proc / "self" / "stat").is_file():
        needle = f"{_SCOPE_MARKER_NAME}={scope_marker}".encode("ascii") if scope_marker else None
        try:
            entries = [entry for entry in os.listdir(proc) if entry.isdigit()]
        except OSError:
            return None
        for entry in entries:
            try:
                stat = (proc / entry / "stat").read_bytes().decode("ascii", errors="replace")
                fields = stat.rpartition(")")[2].split()
                state, ppid, pgid = fields[0], int(fields[1]), int(fields[2])
            except (OSError, ValueError, IndexError):
                continue  # the process exited while the table was being read
            if state in {"Z", "X", "x"}:
                continue
            marked = False
            if needle is not None:
                try:
                    marked = needle in (proc / entry / "environ").read_bytes().split(b"\0")
                except OSError:
                    marked = False
            rows.append((int(entry), ppid, pgid, marked))
        return rows
    try:
        listing = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,pgid=,stat="], stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listing.returncode != 0:
        return None
    for line in listing.stdout.decode("ascii", errors="replace").splitlines():
        parts = line.split()
        try:
            pid, ppid, pgid = int(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            continue
        if len(parts) > 3 and parts[3].startswith("Z"):
            continue
        rows.append((pid, ppid, pgid, False))
    return rows


def _posix_scope_pids(table: Sequence[tuple[int, int, int, bool]], pgid: int, known: Sequence[int] = ()) -> set[int]:
    """Live members of the launched scope: the group, marked processes, and their descendants."""
    remembered = set(known)
    scope = {pid for pid, _ppid, group, marked in table if group == pgid or marked or pid in remembered}
    changed = True
    while changed:
        changed = False
        for pid, ppid, _group, _marked in table:
            if ppid in scope and pid not in scope:
                scope.add(pid)
                changed = True
    scope.discard(os.getpid())
    return scope


def _stop_posix_scope(process: Any, scope_marker: str) -> Literal["POSIX_PROCESS_GROUP"] | None:
    """Kill the launched scope and prove that nothing of it is still alive.

    The process group alone is not the scope: a daemon that called ``setsid``
    leaves the group and keeps running.  The proof is issued only after a fresh
    process table shows no live member of the group, no process carrying this
    launch's marker, and no descendant of either.
    """
    pgid = process.pid
    seen: set[int] = set()
    table = _posix_process_table(scope_marker)
    enumerable = table is not None
    if table is not None:
        seen |= _posix_scope_pids(table, pgid)
    deadline = time.monotonic() + _STOP_WAIT_SECONDS
    while True:
        try:
            os.killpg(pgid, 9)
        except OSError:
            pass
        for pid in sorted(seen):
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        try:
            process.wait(timeout=_STOP_POLL_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            return None
        table = _posix_process_table(scope_marker)
        if table is None:
            enumerable = False
            break
        alive = _posix_scope_pids(table, pgid, tuple(seen))
        seen |= alive
        if not alive and process.poll() is not None:
            break
        if time.monotonic() >= deadline:
            return None
    if not enumerable or process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
        return None
    # Only unreaped zombies may remain in the group; they hold no resources
    # and cannot run, which is exactly what the process table just proved.
    return "POSIX_PROCESS_GROUP"


# --- Windows Job Object scope ------------------------------------------------
#
# The process is created suspended, assigned to a kill-on-close Job Object and
# only then resumed, so no child can exist outside the job.  Every kernel call
# lives in its own small function; tests replace them to exercise the proof
# logic on any host.

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002
_WAIT_OBJECT_0 = 0


def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenThread.restype = wintypes.HANDLE
    kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel32.ResumeThread.restype = wintypes.DWORD
    return kernel32


def _close_windows_handle(handle: int) -> bool:
    if os.name != "nt":
        return False
    return bool(_kernel32().CloseHandle(handle))


def _windows_create_kill_job() -> int | None:
    """Create an anonymous Job Object whose processes die when its handle closes."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = _kernel32()
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info),
    ):
        kernel32.CloseHandle(job)
        return None
    return int(job)


def _windows_assign_job(job: int, process: Any) -> bool:
    if os.name != "nt":
        return False
    return bool(_kernel32().AssignProcessToJobObject(job, int(process._handle)))


def _assign_windows_kill_job(process: Any) -> int | None:
    """Put one still-suspended process in a new kill-on-close job; ``None`` if that failed."""
    job = _windows_create_kill_job()
    if job is None:
        return None
    if not _windows_assign_job(job, process):
        _close_windows_handle(job)
        return None
    return job


def _windows_resume_process(process: Any) -> bool:
    """Resume a process created with ``CREATE_SUSPENDED``.

    ``subprocess.Popen`` closes the primary thread handle, so the thread is
    found again through a Toolhelp thread snapshot filtered by the owning
    process ID and resumed with the documented ``OpenThread``/``ResumeThread``
    pair.  A suspended process has exactly its primary thread.  If no thread
    could be resumed that way, ``NtResumeProcess`` on the process handle is the
    fallback.
    """
    if os.name != "nt":
        return False
    import ctypes
    from ctypes import wintypes

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    kernel32 = _kernel32()
    invalid_handle = ctypes.c_void_p(-1).value
    failed = 0xFFFFFFFF
    resumed = 0
    snapshot = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    if snapshot and snapshot != invalid_handle:
        try:
            kernel32.Thread32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry32))
            kernel32.Thread32First.restype = wintypes.BOOL
            kernel32.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(ThreadEntry32))
            kernel32.Thread32Next.restype = wintypes.BOOL
            entry = ThreadEntry32()
            entry.dwSize = ctypes.sizeof(ThreadEntry32)
            more = kernel32.Thread32First(snapshot, ctypes.byref(entry))
            while more:
                if entry.th32OwnerProcessID == process.pid:
                    thread = kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                    if thread:
                        try:
                            if kernel32.ResumeThread(thread) != failed:
                                resumed += 1
                        finally:
                            kernel32.CloseHandle(thread)
                more = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    if resumed:
        return True
    try:
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        ntdll.NtResumeProcess.restype = ctypes.c_long
        return ntdll.NtResumeProcess(int(process._handle)) == 0
    except (AttributeError, OSError):
        return False


def _windows_terminate_job(job: int) -> bool:
    if os.name != "nt":
        return False
    return bool(_kernel32().TerminateJobObject(job, 1))


def _windows_job_active_processes(job: int) -> int | None:
    """Number of live processes in the job, or ``None`` when it cannot be queried."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class BasicAccountingInformation(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    info = BasicAccountingInformation()
    if not _kernel32().QueryInformationJobObject(
        job, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info), ctypes.sizeof(info), None,
    ):
        return None
    return int(info.ActiveProcesses)


def _windows_wait_process(process: Any, milliseconds: int) -> bool:
    if os.name != "nt":
        return False
    return _kernel32().WaitForSingleObject(int(process._handle), milliseconds) == _WAIT_OBJECT_0


def _start_windows_scoped_process(command: list[str], kwargs: Mapping[str, Any]) -> tuple[Any, int | None]:
    """Start suspended, contain in a Job Object, then resume.

    Returns the process and its job handle.  A failed job assignment still lets
    the process run, but without a job there can be no stop proof later.  A
    process that cannot be resumed is killed and reported as a launch failure.
    """
    options = dict(kwargs)
    options["creationflags"] = (
        int(options.get("creationflags", 0))
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | _CREATE_SUSPENDED
    )
    process = subprocess.Popen(command, **options)
    job = _assign_windows_kill_job(process)
    if not _windows_resume_process(process):
        if job is not None:
            _windows_terminate_job(job)
            _close_windows_handle(job)
        try:
            process.kill()
            process.wait(timeout=_STOP_WAIT_SECONDS)
        except (OSError, subprocess.TimeoutExpired):
            pass
        raise OSError("suspended test process could not be resumed")
    return process, job


def _stop_windows_scope(process: Any, job: int | None) -> Literal["WINDOWS_JOB_TERMINATED"] | None:
    """Terminate the whole job and prove it is empty; without a job only best-effort kill."""
    if job is None:
        _terminate_process_tree(process)
        return None
    proof: Literal["WINDOWS_JOB_TERMINATED"] | None = None
    try:
        terminated = _windows_terminate_job(job)
        _windows_wait_process(process, int(_STOP_WAIT_SECONDS * 1000))
        try:
            process.wait(timeout=_STOP_WAIT_SECONDS)
            exited = True
        except (OSError, subprocess.TimeoutExpired):
            exited = False
        active: int | None = None
        deadline = time.monotonic() + _STOP_WAIT_SECONDS
        while True:
            active = _windows_job_active_processes(job)
            if active is None or active == 0 or time.monotonic() >= deadline:
                break
            time.sleep(_STOP_POLL_SECONDS)
        if terminated and exited and active == 0:
            proof = "WINDOWS_JOB_TERMINATED"
    finally:
        _close_windows_handle(job)  # KILL_ON_JOB_CLOSE reaps anything still inside
    if proof is None:
        _terminate_process_tree(process)
    return proof


def _terminate_process_tree(process: Any) -> None:
    """Best-effort kill without any proof (used when no Job Object contains the tree)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        process.terminate()
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
