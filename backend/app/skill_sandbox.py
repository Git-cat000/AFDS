"""Fixed-policy Linux container controller with a pre-execution resource gate."""

import csv
import io
import json
import queue
import re
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict, Field, ValidationError

from app.errors import DomainError
from app.execution.queue import digest
from app.media import Cancelled
from app.sandbox_protocol import (
    CAPTURE_LIMIT,
    MAX_HEADER_BYTES,
    MAX_WIRE_BYTES,
    PROTOCOL_VERSION,
    ProtocolError,
    SandboxResult,
    extract_archive,
)
from app.schemas import Schema
from app.skill_packages import safe_name
from app.skill_process import log_text
from app.storage import contained

LABEL = "io.afds.sandbox"


class ScriptExecutionPolicy(Schema):
    model_config = ConfigDict(frozen=True, revalidate_instances="always")
    mode: Literal["container"] = "container"
    image_id: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    network: Literal["none"] = "none"
    cpu_limit: Literal[1] = 1
    memory_bytes: Literal[536870912] = 536870912
    pids_limit: Literal[64] = 64
    work_bytes: Literal[67108864] = 67108864
    output_bytes: Literal[33554432] = 33554432
    timeout_seconds: int = Field(ge=5, le=300)
    policy_version: Literal["afds-container-v1"] = PROTOCOL_VERSION


class SandboxFailure(Schema):
    code: Literal["SCRIPT_FAILED", "SCRIPT_TIMEOUT", "SCRIPT_OUTPUT_LIMIT", "SCRIPT_OUTPUT_INVALID"]
    message: str = Field(max_length=300)


class ContainerReport(Schema):
    kind: Literal["result"]
    exit_code: int = Field(ge=-255, le=255)
    stdout: str = Field(max_length=CAPTURE_LIMIT)
    stderr: str = Field(max_length=CAPTURE_LIMIT)
    logs_truncated: bool
    error: SandboxFailure | None
    artifacts: list[dict] = Field(max_length=32)


def policy_digest(policy):
    return digest(ScriptExecutionPolicy.model_validate(policy).model_dump(mode="json"))


def image_identity(settings):
    value = settings.sandbox_image_id
    if not value:
        try:
            receipt = json.loads((settings.data_dir / "sandbox" / "image.json").read_bytes())
            if receipt["policy_version"] != PROTOCOL_VERSION:
                raise ValueError("Policy version changed")
            value = receipt["image_id"]
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise DomainError(
                "SANDBOX_IMAGE_MISSING", "Build the fixed sandbox image first", 422
            ) from error
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", value):
        raise DomainError("SANDBOX_IMAGE_MISSING", "Configure an immutable sandbox image ID", 422)
    return value


def docker_command(arguments, timeout=10):
    executable = shutil.which("docker")
    if not executable:
        raise DomainError(
            "SANDBOX_CLIENT_MISSING", "Install Docker before container execution", 422
        )
    try:
        result = subprocess.run(
            [executable, *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise DomainError(
            "SANDBOX_UNAVAILABLE", "Docker command could not complete", 422
        ) from error


def controller_identity():
    try:
        identity = json.loads(docker_command(["info", "--format", "{{json .ID}}"], timeout=5))
    except ValueError as error:
        raise DomainError(
            "SANDBOX_UNAVAILABLE", "Docker controller identity is invalid", 422
        ) from error
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", identity):
        raise DomainError("SANDBOX_UNAVAILABLE", "Docker controller identity is unavailable", 422)
    return identity


def execution_policy(settings, timeout_seconds):
    image = image_identity(settings)
    try:
        info = json.loads(docker_command(["info", "--format", "{{json .OSType}}"]))
    except ValueError as error:
        raise DomainError(
            "SANDBOX_UNAVAILABLE", "Docker returned invalid engine information", 422
        ) from error
    if info != "linux":
        raise DomainError("SANDBOX_PLATFORM_UNSUPPORTED", "Switch Docker to Linux containers", 422)
    try:
        description = json.loads(docker_command(["image", "inspect", image]))[0]
    except (DomainError, ValueError, IndexError, TypeError, KeyError) as error:
        raise DomainError(
            "SANDBOX_IMAGE_MISSING", "The allowlisted sandbox image is unavailable", 422
        ) from error
    if (
        description["Id"] != image
        or description["Os"] != "linux"
        or description["Architecture"] != "amd64"
        or description["Config"].get("Labels", {}).get("io.afds.sandbox.policy") != PROTOCOL_VERSION
    ):
        raise DomainError("SANDBOX_IMAGE_CHANGED", "Build and review the fixed sandbox image", 409)
    return ScriptExecutionPolicy(image_id=image, timeout_seconds=timeout_seconds)


def sandbox_status(settings):
    try:
        policy = execution_policy(settings, 30)
        return {"available": True, "policy": policy.model_dump(mode="json"), "error": None}
    except DomainError as error:
        return {
            "available": False,
            "policy": None,
            "error": {"code": error.code, "message": error.message},
        }


def active_containers(settings):
    raw = docker_command(["ps", "-a", "--filter", f"label={LABEL}=true", "--format", "{{.ID}}"])
    return raw.decode().splitlines()


def read_result_archive(stream, destination, inventory):
    try:
        return extract_archive(stream, destination, inventory)
    except (ProtocolError, ValueError, KeyError, TypeError, OSError, tarfile.TarError) as error:
        raise DomainError(
            "SANDBOX_OUTPUT_INVALID", "Container output archive is invalid", 422
        ) from error


def cleanup_run(run_id):
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise DomainError("INVALID_SANDBOX_RUN", "Invalid sandbox run identity", 422)
    try:
        references = docker_command(
            [
                "ps",
                "--all",
                "--filter",
                f"label={LABEL}=true",
                "--filter",
                f"label={LABEL}.run={run_id}",
                "--format",
                "{{.ID}}",
            ]
        )
        for reference in references.decode().splitlines():
            if not re.fullmatch(r"[a-f0-9]{12,64}", reference):
                raise DomainError("INVALID_SANDBOX_RUN", "Invalid container reference", 422)
            docker_command(["rm", "--force", reference], timeout=5)
        return True
    except DomainError:
        return False


def mount_argument(source, target):
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="").writerow(
        ["type=bind", f"source={source}", f"target={target}", "readonly"]
    )
    return buffer.getvalue()


def prepare_run(settings, package, script, arguments, context, policy, nonce):
    package = Path(package)
    root_candidates = (settings.data_dir.resolve(), settings.cache_root.resolve())
    permitted = next(
        (root for root in root_candidates if package.absolute().is_relative_to(root)), None
    )
    if permitted is None or not package.is_dir():
        raise DomainError("INVALID_PATH", "Sandbox package is outside configured storage", 403)
    current = permitted
    for part in package.absolute().relative_to(permitted).parts:
        current = current / part
        if current.is_symlink() or getattr(current.lstat(), "st_file_attributes", 0) & 0x400:
            raise DomainError("INVALID_PATH", "Sandbox package cannot contain links", 403)
    package = package.resolve()
    if not package.is_relative_to(permitted):
        raise DomainError("INVALID_PATH", "Sandbox package is outside configured storage", 403)
    script = safe_name(script)
    context_data = json.dumps(context, ensure_ascii=False, allow_nan=False).encode()
    if (
        len(context_data) > 512 * 1024
        or len(arguments) > 50
        or any(not isinstance(value, str) or len(value) > 4096 for value in arguments)
    ):
        raise DomainError(
            "SKILL_CONTEXT_LIMIT", "Use bounded context and literal script arguments", 422
        )
    root = contained(settings.cache_root, f"sandbox/{nonce}")
    workspace, control = root / "package", root / "input"
    workspace.mkdir(parents=True)
    control.mkdir()
    size, count = 0, 0
    for path in package.rglob("*"):
        relative = safe_name(path.relative_to(package).as_posix())
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise DomainError(
                "SKILL_PACKAGE_CHANGED", "Sandbox packages require regular files", 409
            )
        if relative.split("/", 1)[0].casefold() in {"input.json", "outputs"}:
            raise DomainError(
                "SCRIPT_WORKSPACE_CONFLICT", "Reserve input.json and outputs/ for runtime", 422
            )
        if path.is_dir():
            continue
        count += 1
        size += path.stat().st_size
        if count > 256 or size > 32 * 1024**2 or not path.resolve().is_relative_to(package):
            raise DomainError(
                "SKILL_PACKAGE_CHANGED", "Sandbox package exceeds its source limits", 422
            )
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    if not (workspace / script).is_file():
        raise DomainError("SCRIPT_NOT_FOUND", "Approved script is missing", 422)
    (workspace / "outputs").mkdir()
    (workspace / "input.json").write_bytes(context_data)
    (control / "run.json").write_text(
        json.dumps(
            {"script": script, "arguments": arguments, "policy": policy.model_dump(mode="json")},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root, workspace, control


def verify_container(container, policy):
    config = container["HostConfig"]
    binds = [item for item in container["Mounts"] if item["Type"] == "bind"]
    if (
        container["Image"] != policy.image_id
        or container["Config"]["User"] != "65532:65532"
        or not config["ReadonlyRootfs"]
        or config["Privileged"]
        or config["NetworkMode"] != "none"
        or config["PidMode"] not in {"", "private"}
        or config["IpcMode"] != "none"
        or config["Memory"] != policy.memory_bytes
        or config["MemorySwap"] != policy.memory_bytes
        or config["NanoCpus"] != 1_000_000_000
        or config["PidsLimit"] != policy.pids_limit
        or set(config["CapDrop"] or []) != {"ALL"}
        or not any(
            value.split(":", 1)[0] == "no-new-privileges" for value in config["SecurityOpt"] or []
        )
        or len(binds) != 2
        or {item["Destination"] for item in binds} != {"/workspace", "/input"}
        or any(item["RW"] for item in binds)
        or set(config["Tmpfs"]) != {"/work", "/workspace/outputs"}
    ):
        raise DomainError("SANDBOX_LIMITS_UNAVAILABLE", "Container policy was not applied", 422)


def run_container(
    settings,
    package,
    script,
    arguments,
    context,
    policy,
    checkpoint,
    *,
    on_created=None,
    before_start=None,
) -> SandboxResult:
    policy = ScriptExecutionPolicy.model_validate(policy)
    current = execution_policy(settings, policy.timeout_seconds)
    if policy_digest(current) != policy_digest(policy):
        raise DomainError(
            "SANDBOX_POLICY_CHANGED", "Preview the current container policy again", 409
        )
    if not checkpoint():
        raise Cancelled()
    nonce = uuid4().hex
    controller = controller_identity()
    root, workspace, control = prepare_run(
        settings, package, script, arguments, context, policy, nonce
    )
    name = f"afds-sbx-{nonce}"
    container_id = None
    process = None
    started = False
    actual = {}
    output_dir = root / "result" / "outputs"
    try:
        if on_created:
            on_created({"container_id": None, "run_id": nonce, "controller_id": controller})
        raw = docker_command(
            [
                "create",
                "--name",
                name,
                "--label",
                f"{LABEL}=true",
                "--label",
                f"{LABEL}.run={nonce}",
                "--interactive",
                "--read-only",
                "--network",
                "none",
                "--ipc",
                "none",
                "--cgroupns",
                "private",
                "--cpus",
                "1",
                "--memory",
                str(policy.memory_bytes),
                "--memory-swap",
                str(policy.memory_bytes),
                "--pids-limit",
                str(policy.pids_limit),
                "--user",
                "65532:65532",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--log-driver",
                "none",
                "--mount",
                mount_argument(workspace, "/workspace"),
                "--mount",
                mount_argument(control, "/input"),
                "--tmpfs",
                f"/work:rw,nosuid,nodev,size={policy.work_bytes},uid=65532,gid=65532,mode=1777",
                "--tmpfs",
                f"/workspace/outputs:rw,nosuid,nodev,size={policy.output_bytes},uid=65532,gid=65532,mode=0700",
                policy.image_id,
            ]
        )
        container_id = raw.decode().strip()
        if not re.fullmatch(r"[a-f0-9]{64}", container_id):
            raise DomainError(
                "SANDBOX_UNAVAILABLE", "Docker returned an invalid container identity", 422
            )
        if on_created:
            on_created({"container_id": container_id, "run_id": nonce, "controller_id": controller})
        description = json.loads(docker_command(["inspect", container_id]))[0]
        verify_container(description, policy)
        events = queue.Queue()
        wire = root / "result.tar"
        cli_errors = bytearray()

        def header(stream):
            line = stream.readline(MAX_HEADER_BYTES + 1)
            if not line.endswith(b"\n") or len(line) > MAX_HEADER_BYTES:
                raise ProtocolError("Container protocol header is invalid")
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ProtocolError("Container protocol requires an object")
            return value

        def receive(stream):
            try:
                events.put(("ready", header(stream)))
                report = header(stream)
                size = 0
                with wire.open("xb") as handle:
                    while chunk := stream.read(64 * 1024):
                        size += len(chunk)
                        if size > MAX_WIRE_BYTES:
                            raise ProtocolError("Container output archive exceeds its limit")
                        handle.write(chunk)
                events.put(("done", report))
            except Exception:
                events.put(("error", None))
            finally:
                stream.close()

        def errors(stream):
            while chunk := stream.read(8192):
                if len(cli_errors) < CAPTURE_LIMIT:
                    cli_errors.extend(chunk[: CAPTURE_LIMIT - len(cli_errors)])
            stream.close()

        def wait_event(seconds):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if not checkpoint():
                    raise Cancelled()
                try:
                    return events.get(timeout=0.1)
                except queue.Empty:
                    continue
            raise TimeoutError("Container protocol deadline exceeded")

        process = subprocess.Popen(
            [shutil.which("docker"), "start", "--attach", "--interactive", container_id],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        readers = [
            threading.Thread(target=receive, args=(process.stdout,), daemon=True),
            threading.Thread(target=errors, args=(process.stderr,), daemon=True),
        ]
        for thread in readers:
            thread.start()
        event, ready = wait_event(20)
        if event != "ready" or ready.get("kind") != "ready":
            raise DomainError(
                "SANDBOX_LIMITS_UNAVAILABLE", "Container resource gate did not pass", 422
            )
        actual = ready.get("actual_policy", {})
        if (
            any(actual.get(key) != value for key, value in policy.model_dump(mode="json").items())
            or not actual.get("resources_verified")
            or actual.get("uid") != 65532
        ):
            raise DomainError("SANDBOX_LIMITS_UNAVAILABLE", "Container resource proof differs", 422)
        if before_start:
            before_start()
        if not checkpoint():
            raise Cancelled()
        process.stdin.write(b"\x01")
        process.stdin.flush()
        process.stdin.close()
        started = True
        event, report = wait_event(policy.timeout_seconds + 10)
        process.wait(timeout=5)
        if event != "done" or process.returncode:
            raise DomainError(
                "SCRIPT_RUN_UNCERTAIN",
                "Container result was lost after launch. Approve a new run",
                409,
            )
        report = ContainerReport.model_validate(report)
        with wire.open("rb") as stream:
            inventory = read_result_archive(stream, output_dir, report.artifacts)
        return {
            "exit_code": report.exit_code,
            "stdout": log_text(report.stdout.encode())
            .encode()[:CAPTURE_LIMIT]
            .decode("utf-8", "ignore"),
            "stderr": log_text(report.stderr.encode())
            .encode()[:CAPTURE_LIMIT]
            .decode("utf-8", "ignore"),
            "logs_truncated": report.logs_truncated,
            "error": report.error.model_dump() if report.error else None,
            "actual_policy": actual,
            "artifacts": inventory,
            "output_dir": output_dir,
        }
    except TimeoutError as error:
        if not started:
            raise DomainError(
                "SANDBOX_LIMITS_UNAVAILABLE", "Container resource gate timed out", 422
            ) from error
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "logs_truncated": False,
            "error": {"code": "SCRIPT_TIMEOUT", "message": "Container exceeded its time limit"},
            "actual_policy": actual,
            "artifacts": [],
            "output_dir": output_dir,
        }
    except (OSError, ValueError, KeyError, TypeError, ValidationError) as error:
        raise DomainError(
            "SCRIPT_RUN_UNCERTAIN" if started else "SANDBOX_UNAVAILABLE",
            "Container execution could not complete. Review its status before a new run",
            422,
        ) from error
    finally:
        if container_id:
            try:
                docker_command(["rm", "--force", container_id], timeout=5)
            except DomainError:
                pass
        else:
            cleanup_run(nonce)
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
