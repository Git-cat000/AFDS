import copy
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import project
from sqlalchemy import select, update
from test_skill_execution import execute_script, read_job, script_preview

from app import db, skill_sandbox
from app.errors import DomainError
from app.execution.queue import Queue, digest
from app.execution.worker import Worker
from app.skill_execution import SkillExecution


@pytest.fixture
def fixed_policy(monkeypatch):
    def policy(settings, timeout_seconds):
        return skill_sandbox.ScriptExecutionPolicy(
            image_id="sha256:" + "a" * 64, timeout_seconds=timeout_seconds
        )

    monkeypatch.setattr(skill_sandbox, "execution_policy", policy)
    return policy


def test_script_approval_pins_container_policy(client, fixed_policy):
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container")
    policy = fixed_policy(None, 5).model_dump(mode="json")
    assert plan["request"]["execution_policy"] == policy
    assert plan["request"]["execution_policy_sha256"] == digest(policy)
    assert plan["runtime"]["available"]
    job = execute_script(client, apr, plan).json()
    assert job["request"]["execution_policy"] == policy
    assert job["request"]["execution_mode"] == "container"


def test_repeated_container_approval_reuses_job(client, fixed_policy):
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: execute_script(client, apr, plan), range(2)))
    assert all(response.status_code == 202 for response in responses)
    assert len({response.json()["id"] for response in responses}) == 1


def test_unavailable_container_preserves_proposal(client, monkeypatch):
    def unavailable(*args):
        raise DomainError("SANDBOX_UNAVAILABLE", "Docker engine is offline", 422)

    monkeypatch.setattr(skill_sandbox, "execution_policy", unavailable)
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container")
    assert not plan["runtime"]["available"]
    assert plan["runtime"]["error"]["code"] == "SANDBOX_UNAVAILABLE"
    assert execute_script(client, apr, plan).status_code == 422
    with client.app.state.database.engine.connect() as connection:
        saved = connection.execute(select(db.plans.c.payload)).scalar_one()
        assert saved["state"] == "proposed" and not saved.get("job_id")


def test_changed_image_invalidates_container_approval(client, fixed_policy, monkeypatch):
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container")
    monkeypatch.setattr(
        skill_sandbox,
        "execution_policy",
        lambda settings, timeout: fixed_policy(settings, timeout).model_copy(
            update={"image_id": "sha256:" + "b" * 64}
        ),
    )
    response = execute_script(client, apr, plan)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "SANDBOX_POLICY_CHANGED"


def test_old_unexecuted_preview_requires_refresh(client):
    apr = project(client)
    plan = script_preview(client, apr)
    old = copy.deepcopy(plan)
    old["request"].pop("execution_mode", None)
    old["request"].pop("execution_policy", None)
    old["request"].pop("execution_policy_sha256", None)
    old["fingerprint"] = digest(old["request"])
    with client.app.state.database.transaction() as connection:
        connection.execute(update(db.plans).where(db.plans.c.id == plan["id"]).values(payload=old))
    response = execute_script(client, apr, old)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "SCRIPT_PREVIEW_EXPIRED"


def test_worker_persists_gate_and_uses_owned_container_outputs(client, fixed_policy, monkeypatch):
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container")
    job = execute_script(client, apr, plan).json()
    database = client.app.state.database

    def runner(settings, package, script, arguments, context, policy, checkpoint, **callbacks):
        callbacks["on_created"]({"run_id": "1" * 32, "container_id": "2" * 64})
        with database.engine.connect() as connection:
            saved = Queue(database).get(connection, apr["project_id"], job["id"])
            assert saved["sandbox_cleanup_pending"] and not saved.get("script_started")
        callbacks["before_start"]()
        with database.engine.connect() as connection:
            assert Queue(database).get(connection, apr["project_id"], job["id"])["script_started"]
        outputs = settings.data_dir / "DEMO-sandbox-result" / "outputs"
        outputs.mkdir(parents=True)
        (outputs / "result.txt").write_text("DEMO container output")
        return {
            "exit_code": 0,
            "stdout": "DEMO sandbox",
            "stderr": "",
            "logs_truncated": False,
            "error": None,
            "output_dir": outputs,
            "artifacts": [],
            "actual_policy": {**policy.model_dump(), "resources_verified": True},
        }

    monkeypatch.setattr(skill_sandbox, "run_container", runner)
    monkeypatch.setattr(skill_sandbox, "cleanup_run", lambda run_id: True)
    Worker(database).run_once()
    result = read_job(client, apr, job)
    assert result["state"] == "completed", result
    assert result["skill_result"]["execution_mode"] == "container"
    assert result["skill_result"]["actual_policy"]["resources_verified"]
    assert "output_dir" not in result["skill_result"]
    artifact = result["skill_result"]["artifacts"][0]
    url = f"/api/v1/projects/{apr['project_id']}/skills/runs/{job['id']}/files/{artifact['id']}"
    assert client.get(url).text == "DEMO container output"


def test_reaper_keeps_live_container_and_retries_failed_cleanup(client, fixed_policy, monkeypatch):
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container")
    job = execute_script(client, apr, plan).json()
    database = client.app.state.database
    Queue(database).claim("DEMO-lost-worker")
    with database.transaction() as connection:
        payload = connection.execute(select(db.jobs.c.payload)).scalar_one()
        payload.update(
            script_started=True, sandbox_run={"run_id": "1" * 32}, sandbox_cleanup_pending=True
        )
        connection.execute(update(db.jobs).values(state="running", payload=payload))
    calls = []
    monkeypatch.setattr(skill_sandbox, "cleanup_run", lambda run_id: calls.append(run_id) or False)
    service = SkillExecution(database)
    service.reap_containers()
    assert calls == []
    with database.transaction() as connection:
        connection.execute(update(db.jobs).values(lease_expires_at=time.time() - 1))
    service.reap_containers()
    assert calls == ["1" * 32]
    Worker(database).run_once()
    assert read_job(client, apr, job)["error"]["code"] == "SCRIPT_RUN_UNCERTAIN"
    monkeypatch.setattr(skill_sandbox, "cleanup_run", lambda run_id: True)
    service.reap_containers()
    with database.engine.connect() as connection:
        assert not connection.execute(select(db.jobs.c.payload)).scalar_one()[
            "sandbox_cleanup_pending"
        ]


def test_history_labels_old_completed_script_as_local_user(client):
    apr = project(client)
    plan = script_preview(client, apr)
    job = execute_script(client, apr, plan).json()
    Worker(client.app.state.database).run_once()
    database = client.app.state.database
    with database.transaction() as connection:
        payload = connection.execute(select(db.jobs.c.payload)).scalar_one()
        payload["request"].pop("execution_mode", None)
        payload["skill_result"].pop("execution_mode", None)
        connection.execute(update(db.jobs).values(payload=payload))
    history = SkillExecution(database).list(apr["project_id"])["items"]
    assert history[0]["id"] == job["id"]
    assert history[0]["skill_result"]["execution_mode"] == "local-user"


def test_expired_precreate_intent_stays_pending_until_recovery_finishes(
    client, fixed_policy, monkeypatch
):
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container")
    execute_script(client, apr, plan)
    database = client.app.state.database
    Queue(database).claim("DEMO-expired-worker")
    with database.transaction() as connection:
        payload = connection.execute(select(db.jobs.c.payload)).scalar_one()
        payload.update(
            sandbox_run={"run_id": "1" * 32, "container_id": None},
            sandbox_cleanup_pending=True,
            sandbox_create_intent_at=time.time(),
        )
        connection.execute(
            update(db.jobs).values(
                state="running", payload=payload, lease_expires_at=time.time() - 1
            )
        )
    monkeypatch.setattr(skill_sandbox, "cleanup_run", lambda run_id: True)
    SkillExecution(database).reap_containers()
    with database.engine.connect() as connection:
        assert connection.execute(select(db.jobs.c.payload)).scalar_one()["sandbox_cleanup_pending"]
    Worker(database).run_once()
    with database.transaction() as connection:
        saved = connection.execute(select(db.jobs.c.payload)).scalar_one()
        assert saved["error"]["code"] == "SCRIPT_RUN_UNCERTAIN"
        assert saved["sandbox_cleanup_pending"]
        saved["sandbox_create_intent_at"] -= 31
        connection.execute(update(db.jobs).values(payload=saved))
    SkillExecution(database).reap_containers()
    with database.engine.connect() as connection:
        assert not connection.execute(select(db.jobs.c.payload)).scalar_one()[
            "sandbox_cleanup_pending"
        ]


@pytest.mark.integration_docker
def test_actual_container_job_runs_through_approval_worker_and_owned_download(client):
    if os.environ.get("AFDS_TEST_DOCKER") != "1":
        pytest.skip("Opt in to real Docker acceptance")
    database = client.app.state.database
    database.settings.sandbox_image_id = os.environ["AFDS_TEST_SANDBOX_IMAGE_ID"]
    apr = project(client)
    plan = script_preview(client, apr, execution_mode="container", timeout_seconds=10)
    assert plan["runtime"]["available"], plan
    job = execute_script(client, apr, plan).json()
    Worker(database).run_once()
    result = read_job(client, apr, job)
    assert result["state"] == "completed", result
    assert result["skill_result"]["actual_policy"]["resources_verified"]
    assert set(result["skill_result"]["actual_policy"]["runtimes"]) == {
        "python",
        "node",
        "powershell",
        "bash",
    }
    assert not result["sandbox_cleanup_pending"]
    artifact = result["skill_result"]["artifacts"][0]
    url = f"/api/v1/projects/{apr['project_id']}/skills/runs/{job['id']}/files/{artifact['id']}"
    assert client.get(url).text == "DEMO approved result"
    assert skill_sandbox.active_containers(database.settings) == []


@pytest.mark.integration_docker
def test_actual_lost_worker_container_is_reaped_without_relaunch(client):
    if os.environ.get("AFDS_TEST_DOCKER") != "1":
        pytest.skip("Opt in to real Docker acceptance")
    database = client.app.state.database
    settings = database.settings
    settings.sandbox_image_id = os.environ["AFDS_TEST_SANDBOX_IMAGE_ID"]
    apr = project(client)
    plan = script_preview(
        client, apr, "import time\ntime.sleep(60)\n", execution_mode="container", timeout_seconds=60
    )
    job = execute_script(client, apr, plan).json()
    code = (
        "import sys\nfrom pathlib import Path\nfrom app.config import Settings\n"
        "from app.db import Database\nfrom app.execution.worker import Worker\n"
        "Worker(Database(Settings(data_dir=Path(sys.argv[1]), "
        "database_url=sys.argv[2], sandbox_image_id=sys.argv[3]))).run_once()\n"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            code,
            str(settings.data_dir),
            settings.database_url,
            settings.sandbox_image_id,
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    handle = None
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and process.poll() is None:
            with database.engine.connect() as connection:
                saved = Queue(database).get(connection, apr["project_id"], job["id"])
            handle = saved.get("sandbox_run")
            if saved.get("script_started"):
                break
            time.sleep(0.1)
        assert saved.get("script_started"), saved
        process.kill()
        process.wait(timeout=5)
        with database.transaction() as connection:
            connection.execute(
                update(db.jobs)
                .where(db.jobs.c.id == job["id"])
                .values(lease_expires_at=time.time() - 1)
            )
        Worker(database).run_once()
        result = read_job(client, apr, job)
        assert result["state"] == "failed"
        assert result["error"]["code"] == "SCRIPT_RUN_UNCERTAIN"
        assert not result["sandbox_cleanup_pending"]
        assert "skill_result" not in result
        assert skill_sandbox.active_containers(settings) == []
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if handle:
            skill_sandbox.cleanup_run(handle["run_id"])
