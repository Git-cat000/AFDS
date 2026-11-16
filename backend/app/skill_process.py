"""Approved local-user script execution with bounded capture and process cleanup."""

import ctypes
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.errors import DomainError
from app.media import Cancelled
from app.skill_packages import SECRET

CAPTURE_LIMIT = 64 * 1024
OUTPUT_LIMIT = 1024 * 1024
BOOTSTRAP = """
import os
import subprocess
import sys
if os.read(0, 1) != b"\\x01":
    sys.exit(125)
sys.exit(subprocess.call(sys.argv[1:]))
"""


def log_text(value):
    data = re.sub(
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*",
        b"[REDACTED]",
        bytes(value),
        flags=re.DOTALL,
    )
    return SECRET.sub(b"[REDACTED]", data).decode("utf-8", errors="replace")


def interpreter(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return {"name": "Python", "executable": sys.executable, "options": ["-B"]}
    if suffix in {".js", ".mjs", ".cjs"}:
        return {"name": "Node.js", "executable": shutil.which("node"), "options": []}
    if suffix == ".ps1":
        return {
            "name": "PowerShell",
            "executable": shutil.which("pwsh") or shutil.which("powershell"),
            "options": ["-NoProfile", "-NonInteractive", "-File"],
        }
    if suffix == ".sh":
        executable = shutil.which("bash")
        if os.name == "nt":
            git = shutil.which("git")
            if git:
                root = Path(git).resolve().parent.parent
                executable = next(
                    (
                        str(value)
                        for value in (root / "bin/bash.exe", root / "usr/bin/bash.exe")
                        if value.is_file()
                    ),
                    executable,
                )
            windows = Path(os.environ.get("SYSTEMROOT", "C:/Windows")).resolve()
            if executable and Path(executable).resolve().is_relative_to(windows):
                executable = None
        return {"name": "Bash", "executable": executable, "options": ["--"]}
    raise DomainError(
        "SCRIPT_UNSUPPORTED", "Select a Python, JavaScript, PowerShell or Bash script", 422
    )


class BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_processes", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority", ctypes.c_uint32),
        ("scheduling", ctypes.c_uint32),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicLimits),
        ("io", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class ProcessTree:
    def __init__(self, process):
        self.process = process
        self.handle = None
        if os.name == "nt":
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
            self.kernel.CreateJobObjectW.restype = ctypes.c_void_p
            self.kernel.SetInformationJobObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            self.kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            self.handle = self.kernel.CreateJobObjectW(None, None)
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (
                not self.handle
                or not self.kernel.SetInformationJobObject(
                    self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
                )
                or not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle))
            ):
                self.close()
                process.kill()
                raise DomainError(
                    "SCRIPT_PROCESS_UNAVAILABLE",
                    "Cannot establish script process-tree cleanup",
                    503,
                )

    def close(self):
        if os.name == "nt":
            if self.handle:
                self.kernel.CloseHandle(self.handle)
                self.handle = None
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def run_script(arguments, cwd, context, timeout, checkpoint):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "PATH",
            "PATHEXT",
            "LANG",
            "LC_ALL",
        }
    }
    environment.update(
        PYTHONUTF8="1", PYTHONIOENCODING="utf-8", AFDS_CONTEXT_FILE=str(cwd / "input.json")
    )
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    sizes = {"stdout": 0, "stderr": 0}
    overflow = threading.Event()

    def read(stream, name):
        try:
            while data := stream.read(8192):
                sizes[name] += len(data)
                captured[name].extend(data[: max(0, CAPTURE_LIMIT - len(captured[name]))])
                if sizes[name] > OUTPUT_LIMIT:
                    overflow.set()
        finally:
            stream.close()

    def write(stream):
        try:
            stream.write(b"\x01" + context)
            stream.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            stream.close()

    started = time.monotonic()
    problem = None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with subprocess.Popen(
        [sys.executable, "-I", "-c", BOOTSTRAP, *arguments],
        cwd=cwd,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        creationflags=flags,
        start_new_session=os.name != "nt",
    ) as process:
        tree = None
        threads = []
        try:
            tree = ProcessTree(process)
            threads = [
                threading.Thread(target=read, args=(process.stdout, "stdout"), daemon=True),
                threading.Thread(target=read, args=(process.stderr, "stderr"), daemon=True),
                threading.Thread(target=write, args=(process.stdin,), daemon=True),
            ]
            for thread in threads:
                thread.start()
            while process.poll() is None:
                if not checkpoint():
                    raise Cancelled()
                if overflow.is_set():
                    problem = {
                        "code": "SCRIPT_OUTPUT_LIMIT",
                        "message": "Script output exceeded1MiB per stream",
                    }
                    break
                if time.monotonic() - started >= timeout:
                    problem = {
                        "code": "SCRIPT_TIMEOUT",
                        "message": "Script reached its approved time limit",
                    }
                    break
                time.sleep(0.1)
        finally:
            if tree:
                tree.close()
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for thread in threads:
                thread.join(timeout=5)
        if overflow.is_set():
            problem = {
                "code": "SCRIPT_OUTPUT_LIMIT",
                "message": "Script output exceeded1MiB per stream",
            }
        if process.returncode and problem is None:
            problem = {
                "code": "SCRIPT_FAILED",
                "message": f"Script exited with code{process.returncode}",
            }
        return {
            "exit_code": process.returncode,
            "stdout": log_text(captured["stdout"]),
            "stderr": log_text(captured["stderr"]),
            "logs_truncated": any(size > CAPTURE_LIMIT for size in sizes.values()),
            "error": problem,
        }
