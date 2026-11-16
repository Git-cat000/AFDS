import base64
import json
import os
import time

import pytest
from conftest import project
from sqlalchemy import update
from test_skills import package_files

from app import db
from app.execution.worker import Worker

SCRIPT = """import json, os, pathlib, sys
context = json.load(sys.stdin)
out = pathlib.Path('outputs')
out.mkdir(exist_ok=True)
(out / 'notes.md').write_text('DEMO approved result', encoding='utf-8')
print(json.dumps({'stage': context['stage'], 'args': sys.argv[1:],
                  'secret': os.getenv('AFDS_TEST_SECRET')}))
"""


def script_preview(client, apr, code=SCRIPT, **changes):
    files = package_files()
    files[-1]["content_base64"] = base64.b64encode(code.encode()).decode()
    files[-1]["path"] = changes.get("script_path", "scripts/inspect.py")
    imported = client.post("/api/v1/skills/import", json={"files": files, "stages": ["shots"]})
    assert imported.status_code == 201, imported.text
    root = f"/api/v1/projects/{apr['project_id']}/skills"
    response = client.post(
        f"{root}/scripts/preview",
        json={
            "base_revision": apr["revision"],
            "package_id": imported.json()["id"],
            "script_path": "scripts/inspect.py",
            "stage": "shots",
            "arguments": ["literal; & $value", " leading and trailing "],
            "timeout_seconds": 5,
            **changes,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def execute_script(client, apr, plan, approved=True):
    return client.post(
        f"/api/v1/projects/{apr['project_id']}/skills/scripts/{plan['id']}/execute",
        json={
            "base_revision": apr["revision"],
            "approved": approved,
            "fingerprint": plan["fingerprint"],
        },
    )


def read_job(client, apr, job):
    return client.get(f"/api/v1/projects/{apr['project_id']}/jobs/{job['id']}").json()


def test_manual_approval_runs_exact_script_once_and_preserves_literal_arguments(
    client, monkeypatch
):
    monkeypatch.setenv("AFDS_TEST_SECRET", "test-secret-must-not-be-inherited")
    apr = project(client)
    plan = script_preview(client, apr)
    assert plan["source_code"] == SCRIPT
    assert execute_script(client, apr, plan, False).status_code == 409
    response = execute_script(client, apr, plan)
    assert response.status_code == 202, response.text
    job = response.json()
    assert execute_script(client, apr, plan).json()["id"] == job["id"]
    Worker(client.app.state.database).run_once()
    result = read_job(client, apr, job)
    assert result["state"] == "completed", result
    assert result["actual_cost"]["status"] == "unknown"
    output = json.loads(result["skill_result"]["stdout"])
    assert output == {
        "stage": "shots",
        "args": ["literal; & $value", " leading and trailing "],
        "secret": None,
    }
    artifact = result["skill_result"]["artifacts"][0]
    path = f"/api/v1/projects/{apr['project_id']}/skills/runs/{job['id']}/files/{artifact['id']}"
    assert client.get(path).text == "DEMO approved result"
    other = project(client)
    assert client.get(path.replace(apr["project_id"], other["project_id"])).status_code == 404
    assert (
        client.get(f"/api/v1/projects/{apr['project_id']}/apr").json()["revision"]
        == apr["revision"]
    )


def test_stale_script_preview_cannot_be_approved(client):
    apr = project(client)
    plan = script_preview(client, apr)
    changed = client.post(
        f"/api/v1/projects/{apr['project_id']}/changesets/commit",
        json={
            "base_revision": apr["revision"],
            "operations": [
                {
                    "op": "set",
                    "entity_type": "project",
                    "entity_id": apr["project_id"],
                    "field": "title",
                    "value": "Updated",
                }
            ],
        },
    )
    assert changed.status_code == 200, changed.text
    assert execute_script(client, apr, plan).status_code == 409


def test_failed_script_retains_diagnostics_and_requires_new_approval(client):
    apr = project(client)
    plan = script_preview(
        client, apr, "import sys\nprint('DEMO failure', file=sys.stderr)\nsys.exit(7)\n"
    )
    job = execute_script(client, apr, plan).json()
    Worker(client.app.state.database).run_once()
    result = read_job(client, apr, job)
    assert result["state"] == "failed", result
    assert result["skill_result"]["exit_code"] == 7
    assert "DEMO failure" in result["skill_result"]["stderr"]
    assert (
        client.post(f"/api/v1/projects/{apr['project_id']}/jobs/{job['id']}/retry").status_code
        == 409
    )


def test_launched_script_is_never_relaunched_after_worker_lease_loss(client):
    apr = project(client)
    plan = script_preview(client, apr)
    job = execute_script(client, apr, plan).json()
    database = client.app.state.database
    from app.execution.queue import Queue

    claimed = Queue(database).claim("lost-worker")
    with database.transaction() as connection:
        payload = (
            connection.execute(db.jobs.select().where(db.jobs.c.id == job["id"]))
            .mappings()
            .one()["payload"]
        )
        payload["script_started"] = True
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.id == job["id"])
            .values(payload=payload, state="running", lease_expires_at=time.time() - 1)
        )
    Worker(database).run_once()
    result = read_job(client, apr, job)
    assert claimed["id"] == job["id"]
    assert result["state"] == "failed"
    assert result["error"]["code"] == "SCRIPT_RUN_UNCERTAIN"
    assert result["output_asset_ids"] == []


def test_builtin_stage_check_runs_as_worker_job(client):
    apr = project(client)
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/skills/checks",
        json={
            "base_revision": apr["revision"],
            "stage": "story",
        },
    )
    assert response.status_code == 202, response.text
    Worker(client.app.state.database).run_once()
    result = read_job(client, apr, response.json())
    assert result["state"] == "completed", result
    assert result["skill_result"]["checks"]
    assert any(item["status"] == "warning" for item in result["skill_result"]["checks"])


def test_node_script_runs_with_the_reviewed_source_and_context(client):
    apr = project(client)
    code = """const fs = require('fs');
const context = JSON.parse(fs.readFileSync(process.env.AFDS_CONTEXT_FILE));
console.log(context.stage);
"""
    plan = script_preview(client, apr, code, script_path="scripts/inspect.js")
    assert plan["runtime"]["available"]
    job = execute_script(client, apr, plan).json()
    Worker(client.app.state.database).run_once()
    result = read_job(client, apr, job)
    assert result["state"] == "completed", result
    assert result["skill_result"]["stdout"].strip() == "shots"


def test_large_script_output_is_bounded_and_fails_visibly(client):
    apr = project(client)
    plan = script_preview(client, apr, "import sys\nsys.stdout.write('x' * (2 * 1024 * 1024))\n")
    job = execute_script(client, apr, plan).json()
    Worker(client.app.state.database).run_once()
    result = read_job(client, apr, job)
    assert result["state"] == "failed", result
    assert result["error"]["code"] == "SCRIPT_OUTPUT_LIMIT"
    assert len(result["skill_result"]["stdout"]) <= 64 * 1024


def test_script_timeout_kills_its_child_process(client):
    apr = project(client)
    code = """import json, pathlib, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
pathlib.Path('outputs/child.json').write_text(json.dumps({'pid': child.pid}))
time.sleep(60)
"""
    plan = script_preview(client, apr, code)
    job = execute_script(client, apr, plan).json()
    Worker(client.app.state.database).run_once()
    result = read_job(client, apr, job)
    assert result["error"]["code"] == "SCRIPT_TIMEOUT", result
    artifact = result["skill_result"]["artifacts"][0]
    pid = client.get(
        f"/api/v1/projects/{apr['project_id']}/skills/runs/{job['id']}/files/{artifact['id']}"
    ).json()["pid"]
    if os.name == "nt":
        import ctypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            code = ctypes.c_uint32()
            try:
                assert kernel.GetExitCodeProcess(handle, ctypes.byref(code))
                assert code.value != 259
            finally:
                kernel.CloseHandle(handle)


def test_running_script_cancellation_does_not_publish_results(client, monkeypatch):
    from app.execution.queue import Queue

    apr = project(client)
    code = (
        "import pathlib,time\npathlib.Path('outputs/started').write_text('yes')\ntime.sleep(60)\n"
    )
    plan = script_preview(client, apr, code, timeout_seconds=60)
    job = execute_script(client, apr, plan).json()
    heartbeat = Queue.heartbeat
    root = client.app.state.settings.data_dir / "cache" / job["id"]

    def cancel_after_launch(queue, identifier, owner):
        if list(root.glob("*/outputs/started")):
            queue.cancel(apr["project_id"], job["id"])
        return heartbeat(queue, identifier, owner)

    monkeypatch.setattr(Queue, "heartbeat", cancel_after_launch)
    Worker(client.app.state.database).run_once()
    result = read_job(client, apr, job)
    assert result["state"] == "cancelled", result
    assert "skill_result" not in result


def test_approval_fingerprint_must_match_and_outputs_never_leak_storage_paths(client):
    apr = project(client)
    plan = script_preview(client, apr)
    request = {"base_revision": apr["revision"], "approved": True, "fingerprint": "0" * 64}
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/skills/scripts/{plan['id']}/execute", json=request
    )
    assert response.status_code == 409
    job = execute_script(client, apr, plan).json()
    Worker(client.app.state.database).run_once()
    assert "relative_path" not in json.dumps(read_job(client, apr, job)["skill_result"])


def test_recognized_credentials_are_redacted_from_logs_and_rejected_as_artifacts(client):
    apr = project(client)
    code = """import pathlib
value = 'sk-' + 'X' * 32
print(value)
pathlib.Path('outputs/private.txt').write_text(value)
"""
    plan = script_preview(client, apr, code)
    job = execute_script(client, apr, plan).json()
    Worker(client.app.state.database).run_once()
    result = read_job(client, apr, job)
    assert result["error"]["code"] == "SCRIPT_OUTPUT_CREDENTIAL", result
    assert result["skill_result"]["stdout"].strip() == "[REDACTED]"
    assert result["skill_result"]["artifacts"] == []


@pytest.mark.parametrize(
    "reserved", ["input.json/reference.txt", "outputs", "Input.JSON", "Outputs/result.txt"]
)
def test_workspace_reserved_roots_are_rejected_before_approval(client, reserved):
    apr = project(client)
    imported = client.post(
        "/api/v1/skills/import",
        json={"files": package_files({reserved: "DEMO"}), "stages": ["shots"]},
    )
    assert imported.status_code == 201, imported.text
    response = client.post(
        f"/api/v1/projects/{apr['project_id']}/skills/scripts/preview",
        json={
            "base_revision": apr["revision"],
            "package_id": imported.json()["id"],
            "script_path": "scripts/inspect.py",
            "stage": "shots",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "SCRIPT_WORKSPACE_CONFLICT"


@pytest.mark.skipif(os.name != "nt", reason="Windows native Bash resolution")
@pytest.mark.parametrize("native", [False, True])
def test_windows_bash_uses_native_git_bash_or_reports_unavailable(tmp_path, monkeypatch, native):
    import shutil

    from app.skill_process import interpreter

    git = tmp_path / "Git" / "cmd" / "git.exe"
    bash = tmp_path / "Git" / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.touch()
    shim = os.path.join(os.environ["SYSTEMROOT"], "System32", "bash.exe")
    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: str(git) if name == "git" and native else shim if name == "bash" else None,
    )
    result = interpreter("scripts/inspect.sh")
    assert result["executable"] == (str(bash) if native else None)


@pytest.mark.parametrize(
    "suffix,code,expected",
    [
        (".ps1", "Write-Output 'DEMO native PowerShell'\n", "DEMO native PowerShell"),
        (".sh", "printf 'DEMO native Bash\\n'\n", "DEMO native Bash"),
    ],
)
def test_available_native_shell_scripts_run_through_approved_jobs(client, suffix, code, expected):
    from app.skill_process import interpreter

    if not interpreter("inspect" + suffix)["executable"]:
        pytest.skip("Native interpreter unavailable on this host")
    apr = project(client)
    plan = script_preview(client, apr, code, script_path="scripts/inspect" + suffix)
    job = execute_script(client, apr, plan).json()
    Worker(client.app.state.database).run_once()
    result = read_job(client, apr, job)
    assert result["state"] == "completed", result
    assert result["skill_result"]["stdout"].strip() == expected


def test_concurrent_approvals_return_one_script_job(client):
    from concurrent.futures import ThreadPoolExecutor

    apr = project(client)
    plan = script_preview(client, apr)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: execute_script(client, apr, plan), range(2)))
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 1
