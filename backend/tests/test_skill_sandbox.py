import hashlib
import importlib
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.config import Settings
from app.errors import DomainError
from app.media import Cancelled


def sandbox():
    return importlib.import_module("app.skill_sandbox")


@pytest.mark.parametrize("kind", ["symlink", "traversal", "oversize", "case_collision"])
def test_container_result_archive_rejects_unsafe_members(tmp_path, kind):
    module = sandbox()
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        item = tarfile.TarInfo("result.txt")
        if kind == "symlink":
            item.type, item.linkname = tarfile.SYMTYPE, "/host/private"
        elif kind == "traversal":
            item.name = "../escape.txt"
        elif kind == "oversize":
            item.size = 9 * 1024**2
        archive.addfile(item)
        if kind == "case_collision":
            archive.addfile(tarfile.TarInfo("RESULT.TXT"))
    with pytest.raises(DomainError, match="archive|output|Output|Archive"):
        module.read_result_archive(io.BytesIO(content.getvalue()), tmp_path / "output", [])
    assert not (tmp_path / "escape.txt").exists()


def test_container_policy_is_fixed_and_hashes_every_resource_boundary(tmp_path):
    module = sandbox()
    policy = module.ScriptExecutionPolicy(image_id="sha256:" + "a" * 64, timeout_seconds=5)
    assert policy.cpu_limit == 1 and policy.memory_bytes == 512 * 1024**2
    assert policy.network == "none" and policy.pids_limit == 64
    assert policy.work_bytes == 64 * 1024**2 and policy.output_bytes == 32 * 1024**2
    assert module.policy_digest(policy) != module.policy_digest(
        policy.model_copy(update={"timeout_seconds": 6})
    )
    with pytest.raises(ValueError):
        module.ScriptExecutionPolicy(image_id="latest", timeout_seconds=5)
    with pytest.raises(ValueError):
        module.ScriptExecutionPolicy(
            image_id="sha256:" + "a" * 64, timeout_seconds=5, network="host"
        )


def test_result_archive_accepts_regular_hashed_files_and_rejects_corruption(tmp_path):
    module = sandbox()
    data = b"DEMO trusted transport, untrusted output"
    inventory = [
        {"name": "nested/result.txt", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    ]
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        info = tarfile.TarInfo("nested/result.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    received = module.read_result_archive(
        io.BytesIO(stream.getvalue()), tmp_path / "good", inventory
    )
    assert received == inventory and (tmp_path / "good/nested/result.txt").read_bytes() == data
    with pytest.raises(DomainError):
        module.read_result_archive(
            io.BytesIO(stream.getvalue()),
            tmp_path / "changed",
            [{**inventory[0], "sha256": "0" * 64}],
        )


def test_invalid_tar_transport_returns_a_typed_error(tmp_path):
    with pytest.raises(DomainError) as result:
        sandbox().read_result_archive(io.BytesIO(b"invalid archive"), tmp_path / "bad", [])
    assert result.value.code == "SANDBOX_OUTPUT_INVALID"


def test_docker_preflight_reports_missing_client_and_unreachable_engine(monkeypatch):
    module = sandbox()
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    with pytest.raises(DomainError) as result:
        module.docker_command(["info"])
    assert result.value.code == "SANDBOX_CLIENT_MISSING"
    monkeypatch.setattr(module.shutil, "which", lambda name: "docker")

    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired("docker", 10)

    monkeypatch.setattr(module.subprocess, "run", unavailable)
    with pytest.raises(DomainError) as result:
        module.docker_command(["info"])
    assert result.value.code == "SANDBOX_UNAVAILABLE"


def test_malformed_engine_information_is_a_typed_preflight_error(tmp_path, monkeypatch):
    module = sandbox()
    settings = Settings(data_dir=tmp_path, sandbox_image_id="sha256:" + "a" * 64)
    monkeypatch.setattr(module, "docker_command", lambda *args: b"bad-json")
    assert module.sandbox_status(settings)["error"]["code"] == "SANDBOX_UNAVAILABLE"


@pytest.mark.integration_docker
def test_ineffective_resource_controls_prevent_user_code_start(container_context, monkeypatch):
    module, settings, package, policy = container_context
    (package / "run.py").write_text(
        'raise RuntimeError("USER CODE STARTED")\n', encoding="utf-8", newline="\n"
    )
    original = module.docker_command

    def weaker_memory(arguments, timeout=10):
        arguments = list(arguments)
        if arguments[0] == "create":
            arguments[arguments.index("--memory") + 1] = str(1024 * 1024**2)
            arguments[arguments.index("--memory-swap") + 1] = str(1024 * 1024**2)
        return original(arguments, timeout)

    monkeypatch.setattr(module, "docker_command", weaker_memory)
    # Exercise the independent trusted in-container check as well as host inspection.
    monkeypatch.setattr(module, "verify_container", lambda *args: None)
    began = []
    with pytest.raises(DomainError) as result:
        module.run_container(
            settings,
            package,
            "run.py",
            [],
            {},
            policy,
            lambda: True,
            before_start=lambda: began.append(True),
        )
    assert result.value.code == "SANDBOX_LIMITS_UNAVAILABLE"
    assert began == [] and module.active_containers(settings) == []


@pytest.mark.integration_docker
def test_allowlist_change_rejects_a_pinned_policy_before_launch(container_context):
    module, settings, package, policy = container_context
    settings.sandbox_image_id = "sha256:" + "0" * 64
    with pytest.raises(DomainError) as result:
        module.run_container(settings, package, "run.py", [], {}, policy, lambda: True)
    assert result.value.code in {"SANDBOX_IMAGE_MISSING", "SANDBOX_POLICY_CHANGED"}


@pytest.fixture
def container_context(tmp_path):
    if os.environ.get("AFDS_TEST_DOCKER") != "1":
        pytest.skip(
            "Set AFDS_TEST_DOCKER=1 with the fixed sandbox image for actual Docker acceptance"
        )
    module = sandbox()
    settings = Settings(
        data_dir=tmp_path, sandbox_image_id=os.environ.get("AFDS_TEST_SANDBOX_IMAGE_ID", "")
    )
    package = tmp_path / "package"
    package.mkdir()
    (package / "SKILL.md").write_text("# DEMO container skill\n", encoding="utf-8", newline="\n")
    policy = module.execution_policy(settings, 5)
    return module, settings, package, policy


@pytest.mark.integration_docker
@pytest.mark.parametrize(
    "suffix,source",
    [
        (
            ".py",
            "from pathlib import Path\n"
            'Path("outputs/result.txt").write_text("DEMO Python")\n'
            'print("python-ready")\n',
        ),
        (
            ".js",
            'const fs = require("node:fs");\n'
            'fs.writeFileSync("outputs/result.txt", "DEMO Node");\n'
            'console.log("node-ready");\n',
        ),
        (
            ".ps1",
            'Set-Content -LiteralPath outputs/result.txt -Value "DEMO PowerShell"\n'
            'Write-Output "pwsh-ready"\n',
        ),
        (".sh", 'printf "DEMO Bash" > outputs/result.txt\nprintf "bash-ready\\n"\n'),
    ],
)
def test_four_container_interpreters_return_bounded_owned_outputs(
    container_context, suffix, source
):
    module, settings, package, policy = container_context
    name = "run" + suffix
    (package / name).write_text(source, encoding="utf-8", newline="\n")
    result = module.run_container(
        settings, package, name, [], {"stage": "story"}, policy, lambda: True
    )
    assert result["exit_code"] == 0, result
    assert result["error"] is None and "ready" in result["stdout"]
    assert (result["output_dir"] / "result.txt").read_text("utf-8").strip().startswith("DEMO")
    assert result["actual_policy"]["image_id"] == policy.image_id
    assert result["actual_policy"]["uid"] != 0
    (settings.data_dir / "DEMO-verified-policy.json").write_text(
        json.dumps(result["actual_policy"]), encoding="utf-8"
    )
    assert result["artifacts"][0]["sha256"]
    assert module.active_containers(settings) == []


@pytest.mark.integration_docker
def test_container_cannot_read_host_or_connect_and_package_is_readonly(container_context, tmp_path):
    module, settings, package, policy = container_context
    probe = tmp_path / "host-private-probe.txt"
    probe.write_text("DEMO host-only probe")
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = """import json, os, socket
from pathlib import Path
context = json.loads(Path("input.json").read_text())
assert not Path(context["probe"]).exists()
assert os.getuid() != 0
try:
    Path("SKILL.md").write_text("overwritten")
    raise AssertionError("package writable")
except OSError:
    pass
try:
    socket.create_connection(("host.docker.internal", context["port"]), timeout=1)
    raise AssertionError("network accessible")
except OSError:
    pass
Path("outputs/isolation.json").write_text(json.dumps({"isolated": True}))
"""
    (package / "run.py").write_text(source, encoding="utf-8", newline="\n")
    try:
        result = module.run_container(
            settings,
            package,
            "run.py",
            [],
            {"probe": str(probe), "port": server.server_port},
            policy,
            lambda: True,
        )
        assert result["error"] is None, result
        assert json.loads((result["output_dir"] / "isolation.json").read_text())["isolated"]
        assert calls == [] and probe.read_text() == "DEMO host-only probe"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.integration_docker
def test_container_limits_output_and_children_and_cleans_after_timeout(container_context):
    module, settings, package, policy = container_context
    source = """import subprocess, time
from pathlib import Path
children = []
try:
    for _ in range(100):
        children.append(subprocess.Popen(["sleep", "60"]))
except OSError:
    pass
assert len(children) < 64
Path("outputs/child-count.txt").write_text(str(len(children)))
time.sleep(60)
"""
    (package / "run.py").write_text(source, encoding="utf-8", newline="\n")
    started = time.monotonic()
    result = module.run_container(settings, package, "run.py", [], {}, policy, lambda: True)
    assert time.monotonic() - started < 30
    assert result["error"]["code"] == "SCRIPT_TIMEOUT"
    assert module.active_containers(settings) == []


@pytest.mark.integration_docker
def test_container_caps_tmpfs_and_log_growth(container_context):
    module, settings, package, policy = container_context
    source = """from pathlib import Path
try:
    with Path("outputs/full.bin").open("wb") as output:
        for _ in range(40):
            output.write(b"x" * 1024 * 1024)
    raise AssertionError("output tmpfs is unbounded")
except OSError:
    pass
print("x" * (2 * 1024 * 1024), flush=True)
"""
    (package / "run.py").write_text(source, encoding="utf-8", newline="\n")
    result = module.run_container(settings, package, "run.py", [], {}, policy, lambda: True)
    assert len(result["stdout"].encode()) <= 64 * 1024
    assert result["logs_truncated"]
    assert result["error"]["code"] in {"SCRIPT_OUTPUT_LIMIT", "SCRIPT_OUTPUT_INVALID"}
    assert module.active_containers(settings) == []


@pytest.mark.integration_docker
def test_container_cancellation_stops_all_processes(container_context):
    module, settings, package, policy = container_context
    (package / "run.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8", newline="\n")
    started = time.monotonic()
    with pytest.raises(Cancelled):
        module.run_container(
            settings, package, "run.py", [], {}, policy, lambda: time.monotonic() - started < 2
        )
    assert module.active_containers(settings) == []


@pytest.mark.integration_docker
def test_cpu_quota_bounds_parallel_process_usage(container_context):
    module, settings, package, policy = container_context
    source = """import json, subprocess, time
from pathlib import Path
roots = Path("/sys/fs/cgroup")
if (roots / "cpu.stat").is_file():
    def usage():
        values = dict(line.split() for line in (roots / "cpu.stat").read_text().splitlines())
        return int(values["usage_usec"]) / 1e6
else:
    source = next(roots.glob("*/cpuacct.usage"))
    def usage():
        return int(source.read_text()) / 1e9
children = [subprocess.Popen(["python3", "-c", "while True: pass"]) for _ in range(4)]
started, before = time.monotonic(), usage()
try:
    time.sleep(2)
    report = {"wall_seconds": time.monotonic() - started, "cpu_seconds": usage() - before}
finally:
    for child in children:
        child.kill()
        child.wait()
Path("outputs/cpu.json").write_text(json.dumps(report))
"""
    (package / "run.py").write_text(source, encoding="utf-8", newline="\n")
    result = module.run_container(settings, package, "run.py", [], {}, policy, lambda: True)
    assert result["error"] is None, result
    measured = json.loads((result["output_dir"] / "cpu.json").read_text())
    assert 0.1 < measured["cpu_seconds"] <= measured["wall_seconds"] * 1.3 + 0.25


@pytest.mark.integration_docker
def test_memory_limit_stops_an_oversized_allocation(container_context):
    module, settings, package, policy = container_context
    (package / "run.py").write_text(
        "value = bytearray(700 * 1024**2)\n", encoding="utf-8", newline="\n"
    )
    result = module.run_container(settings, package, "run.py", [], {}, policy, lambda: True)
    assert result["error"]["code"] == "SCRIPT_FAILED"
    assert result["exit_code"] != 0 and result["actual_policy"]["memory_bytes"] == 512 * 1024**2
    assert module.active_containers(settings) == []


@pytest.mark.integration_docker
def test_abandoned_controller_can_cleanup_its_exact_labeled_run(container_context, tmp_path):
    module, settings, package, policy = container_context
    (package / "run.py").write_text("import time\ntime.sleep(60)\n", encoding="utf-8", newline="\n")
    receipt = tmp_path / "DEMO-controller-run.json"
    ready = tmp_path / "DEMO-controller-ready.txt"
    helper = tmp_path / "DEMO-controller.py"
    helper.write_text(
        "import json\nfrom pathlib import Path\nfrom app.config import Settings\n"
        "from app.skill_sandbox import ScriptExecutionPolicy, run_container\n"
        f"settings = Settings(data_dir=Path({str(settings.data_dir)!r}), "
        f"sandbox_image_id={policy.image_id!r})\n"
        f"policy = ScriptExecutionPolicy(image_id={policy.image_id!r}, timeout_seconds=30)\n"
        f"run_container(settings, Path({str(package)!r}), 'run.py', [], {{}}, "
        "policy, lambda: True, "
        f"on_created=lambda value: Path({str(receipt)!r}).write_text(json.dumps(value)), "
        f"before_start=lambda: Path({str(ready)!r}).write_text('ready'))\n",
        encoding="utf-8",
        newline="\n",
    )
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    process = subprocess.Popen(
        [sys.executable, str(helper)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 25
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert ready.exists(), process.communicate(timeout=5)
        run = json.loads(receipt.read_text())
        process.kill()
        process.wait(timeout=5)
        assert module.cleanup_run(run["run_id"])
        assert module.active_containers(settings) == []
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if receipt.exists():
            module.cleanup_run(json.loads(receipt.read_text())["run_id"])
