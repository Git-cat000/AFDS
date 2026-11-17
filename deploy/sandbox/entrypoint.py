"""Trusted PID1 resource gate and bounded output supervisor."""

import json
import os
import platform
import signal
import subprocess
import sys
import tarfile
import threading
import time
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, "/opt/afds")
from sandbox_protocol import (
    CAPTURE_LIMIT,
    LOG_LIMIT,
    PROTOCOL_VERSION,
    ProtocolError,
    output_inventory,
    output_name,
    write_header,
)


def cgroup_value(controller, filename):
    root = Path("/sys/fs/cgroup")
    groups = Path("/proc/self/cgroup").read_text().splitlines()
    for line in groups:
        _, controllers, location = line.split(":", 2)
        if not controllers:
            candidates = [root / location.lstrip("/") / filename, root / filename]
        elif controller in controllers.split(","):
            candidates = [
                base / location.lstrip("/") / filename
                for base in root.iterdir()
                if controller in base.name.split(",")
            ] + [
                base / filename
                for base in root.iterdir()
                if controller in base.name.split(",")
            ]
        else:
            continue
        for path in candidates:
            if path.is_file():
                return path.read_text().strip()
    raise ProtocolError("Required resource controller is unavailable")


def actual_policy(policy):
    if os.getpid() != 1:
        raise ProtocolError("Supervisor requires a private PID namespace")
    unified = (Path("/sys/fs/cgroup") / "cgroup.controllers").exists()
    memory = int(
        cgroup_value("memory", "memory.max" if unified else "memory.limit_in_bytes")
    )
    pids = int(cgroup_value("pids", "pids.max"))
    if unified:
        quota, period = cgroup_value("cpu", "cpu.max").split()
    else:
        quota = cgroup_value("cpu", "cpu.cfs_quota_us")
        period = cgroup_value("cpu", "cpu.cfs_period_us")
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    readonly = bool(os.statvfs("/").f_flag & os.ST_RDONLY) and bool(
        os.statvfs("/workspace").f_flag & os.ST_RDONLY
    )
    if (
        os.getuid() == 0
        or not readonly
        or int(status["CapEff"].strip(), 16) != 0
        or status["NoNewPrivs"].strip() != "1"
        or set(os.listdir("/sys/class/net")) != {"lo"}
        or memory != policy["memory_bytes"]
        or pids != policy["pids_limit"]
        or Fraction(int(quota), int(period)) != policy["cpu_limit"]
    ):
        raise ProtocolError("Container isolation or resource controls are ineffective")
    for path, bound in (
        ("/work", policy["work_bytes"]),
        ("/workspace/outputs", policy["output_bytes"]),
    ):
        stats = os.statvfs(path)
        if stats.f_blocks * stats.f_frsize != bound or stats.f_flag & os.ST_RDONLY:
            raise ProtocolError("Container temporary storage is not bounded")
    return {
        **policy,
        "uid": os.getuid(),
        "resources_verified": True,
        "readonly_root": True,
        "no_new_privileges": True,
        "capabilities": [],
        "network_interfaces": ["lo"],
    }


def runtime(script):
    suffix = Path(script).suffix.lower()
    commands = {
        ".py": ["/usr/bin/python3", "-B"],
        ".js": ["/usr/local/bin/node"],
        ".mjs": ["/usr/local/bin/node"],
        ".cjs": ["/usr/local/bin/node"],
        ".ps1": ["/usr/bin/pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File"],
        ".sh": ["/bin/bash", "--noprofile", "--norc", "--"],
    }
    if suffix not in commands:
        raise ProtocolError("Unsupported script interpreter")
    return commands[suffix]


def supervise(options):
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    sizes = {"stdout": 0, "stderr": 0}
    overflow = threading.Event()

    def drain(stream, name):
        try:
            while chunk := stream.read(8192):
                sizes[name] += len(chunk)
                remaining = CAPTURE_LIMIT - len(captured[name])
                if remaining > 0:
                    captured[name].extend(chunk[:remaining])
                if sizes[name] > LOG_LIMIT:
                    overflow.set()
        finally:
            stream.close()

    context = Path("/workspace/input.json").read_bytes()
    script = output_name(options["script"])
    source = (Path("/workspace") / script).resolve()
    if not source.is_relative_to(Path("/workspace")) or not source.is_file():
        raise ProtocolError("Script is outside its package")
    command = [*runtime(script), str(source), *options["arguments"]]
    error = None
    with subprocess.Popen(
        command,
        cwd="/workspace",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as child:
        readers = [
            threading.Thread(target=drain, args=(child.stdout, "stdout"), daemon=True),
            threading.Thread(target=drain, args=(child.stderr, "stderr"), daemon=True),
        ]
        for thread in readers:
            thread.start()

        def feed():
            try:
                child.stdin.write(context)
                child.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
            finally:
                child.stdin.close()

        writer = threading.Thread(target=feed, daemon=True)
        writer.start()
        started = time.monotonic()
        while child.poll() is None:
            if overflow.is_set():
                error = {
                    "code": "SCRIPT_OUTPUT_LIMIT",
                    "message": "Script output exceeded1MiB per stream",
                }
                break
            if time.monotonic() - started >= options["policy"]["timeout_seconds"]:
                error = {
                    "code": "SCRIPT_TIMEOUT",
                    "message": "Script reached its approved time limit",
                }
                break
            time.sleep(0.05)
        # PID1 is excluded by kill(-1). This also stops descendants that created
        # another session, before inspecting the now-quiescent output directory.
        try:
            os.kill(-1, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=3)
        for thread in [*readers, writer]:
            thread.join(timeout=3)
        if overflow.is_set():
            error = {
                "code": "SCRIPT_OUTPUT_LIMIT",
                "message": "Script output exceeded1MiB per stream",
            }
        if child.returncode and not error:
            error = {
                "code": "SCRIPT_FAILED",
                "message": f"Script exited with code{child.returncode}",
            }
        return {
            "exit_code": child.returncode,
            "stdout": captured["stdout"].decode("utf-8", "replace"),
            "stderr": captured["stderr"].decode("utf-8", "replace"),
            "logs_truncated": any(size > CAPTURE_LIMIT for size in sizes.values()),
            "error": error,
        }


def main():
    output = sys.stdout.buffer
    try:
        options = json.loads(Path("/input/run.json").read_bytes())
        policy = options["policy"]
        if policy["policy_version"] != PROTOCOL_VERSION:
            raise ProtocolError("Unsupported container policy")
        verified = actual_policy(policy)
        verified["runtimes"] = {"python": platform.python_version()}
        for name, command in (
            ("node", ["/usr/local/bin/node", "--version"]),
            (
                "powershell",
                [
                    "/usr/bin/pwsh",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "$PSVersionTable.PSVersion.ToString()",
                ],
            ),
            ("bash", ["/bin/bash", "--version"]),
        ):
            verified["runtimes"][name] = (
                subprocess.check_output(command, timeout=5)
                .decode()
                .splitlines()[0][:100]
            )
        write_header(output, {"kind": "ready", "actual_policy": verified})
    except Exception:
        write_header(
            output,
            {"kind": "rejected", "error": "Container policy or resource gate failed"},
        )
        return 125
    if os.read(0, 1) != b"\x01":
        return 125
    try:
        result = supervise(options)
        try:
            inventory = output_inventory(Path("/workspace/outputs"))
        except (ProtocolError, OSError):
            inventory = []
            result["error"] = {
                "code": "SCRIPT_OUTPUT_INVALID",
                "message": "Output archive violates file or size limits",
            }
        write_header(output, {"kind": "result", **result, "artifacts": inventory})
        with tarfile.open(
            fileobj=output, mode="w|", format=tarfile.PAX_FORMAT
        ) as archive:
            for item in inventory:
                archive.add(
                    Path("/workspace/outputs") / item["name"],
                    arcname=item["name"],
                    recursive=False,
                )
        output.flush()
        return 0
    except Exception:
        return 125


if __name__ == "__main__":
    sys.exit(main())
