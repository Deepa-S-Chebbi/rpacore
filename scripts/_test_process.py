"""Run disposable test processes and reap their owned descendants."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _stop_windows_job(kernel, job):
    import ctypes

    capacity = 16
    while True:
        buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
        success = kernel.QueryInformationJobObject(job, 3, buffer, len(buffer), None)
        if not success and ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
            raise ctypes.WinError(ctypes.get_last_error())
        assigned = ctypes.c_uint32.from_buffer(buffer).value
        listed = ctypes.c_uint32.from_buffer(buffer, 4).value
        if assigned <= listed:
            break
        capacity = max(capacity * 2, assigned)
    handles = []
    try:
        for pid in (ctypes.c_size_t * listed).from_buffer(buffer, 8):
            handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
            if handle:
                handles.append(handle)
            elif ctypes.get_last_error() != 87:  # Already exited: ERROR_INVALID_PARAMETER
                raise ctypes.WinError(ctypes.get_last_error())
        if not kernel.TerminateJobObject(job, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 10
        for handle in handles:
            # Job accounting reaches zero before Windows releases cwd handles.
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            result = kernel.WaitForSingleObject(handle, remaining)
            if result == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            if result != 0:
                raise TimeoutError("SQLite test process did not terminate")
    finally:
        for handle in handles:
            kernel.CloseHandle(handle)


def run_tests(command: list[str], *, cwd: Path, env: dict[str, str], timeout: float) -> None:
    job = None
    process = None
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.restype = ctypes.c_void_p
        kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
        ]
        kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.WaitForSingleObject.restype = ctypes.c_uint
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        launch = command
        if job:
            # Attach before the bootstrap can launch the command or descendants.
            launch = [sys.executable, "-c",
                      "import subprocess, sys; assert sys.stdin.buffer.read(1) == b'1'; "
                      "raise SystemExit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL))",
                      *command]
        process = subprocess.Popen(
            launch, cwd=cwd, env=env, stdin=subprocess.PIPE if job else subprocess.DEVNULL,
            start_new_session=os.name != "nt",
        )
        if job:
            if not kernel.AssignProcessToJobObject(job, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            process.stdin.write(b"1")
            process.stdin.close()
        returncode = process.wait(timeout=timeout)
        if returncode:
            raise subprocess.CalledProcessError(returncode, command)
    finally:
        error = sys.exception()
        try:
            if job:
                _stop_windows_job(kernel, job)
            elif process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except (OSError, subprocess.SubprocessError) as cleanup_error:
            if error is None:
                raise
            error.add_note(f"SQLite test process-tree cleanup failed: {cleanup_error}")
        finally:
            if process is not None:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
            if job:
                kernel.CloseHandle(job)
