"""Opt-in Windows/Linux runtime and node-loss acceptance using DEMO media."""

import json
import os
import re
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.config import ROOT
from app.execution.queue import Queue
from app.identity_service import IdentityService
from app.main import create_app
from app.media import Cancelled, probe, run_process
from app.storage import asset_path, file_hash
from app.worker_registry import WorkerRegistry

pytestmark = [pytest.mark.integration_docker, pytest.mark.integration_postgres]


def docker(*arguments, check=True, environment=None):
    return subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        text=True,
        check=check,
        timeout=60,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def wait_for(read, condition, timeout=120):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = read()
        if condition(value):
            return value
        time.sleep(0.05)
    raise AssertionError(f"DEMO worker acceptance timed out with {value}")


def test_linux_worker_reads_shared_media_and_host_recovers_its_lost_render(
    dialect_database, tmp_path_factory
):
    if (
        os.environ.get("AFDS_TEST_REMOTE_WORKER") != "1"
        or os.environ.get("AFDS_TEST_DOCKER") != "1"
    ):
        pytest.skip(
            "Set AFDS_TEST_REMOTE_WORKER=1 and AFDS_TEST_DOCKER=1 for actual two-runtime acceptance"
        )
    if dialect_database.engine.dialect.name != "postgresql":
        pytest.skip("Independent team nodes use PostgreSQL")
    receipt_path = ROOT / "data/worker/image.json"
    assert receipt_path.is_file(), "Build the trusted runtime with scripts/build_worker.py"
    image_id = (
        os.environ.get("AFDS_TEST_WORKER_IMAGE_ID")
        or json.loads(receipt_path.read_text())["image_id"]
    )
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
    labels = json.loads(
        docker("image", "inspect", "--format", "{{json .Config.Labels}}", image_id).stdout
    )
    assert labels["io.afds.runtime"] == "afds-worker-v1"
    database = dialect_database
    settings = database.settings
    settings.mode = "team"
    settings.lease_seconds = 4
    registry = WorkerRegistry(database)
    registry.initialize_store()
    IdentityService(database).create_user("demo-node-owner", "DEMO node owner password")
    host_cache = tmp_path_factory.mktemp("worker-host-cache")
    environment = {
        **os.environ,
        "AFDS_MODE": "team",
        "AFDS_DATA_DIR": str(settings.data_dir),
        "AFDS_DATABASE_URL": settings.database_url,
        "AFDS_LEASE_SECONDS": "4",
        "AFDS_WORKER_CACHE_DIR": str(host_cache),
    }
    processes = []
    container_id = None
    name = "afds-worker-acceptance-" + uuid4().hex
    app = create_app(settings)

    def start_host(operations, log_name, slots=1):
        handle = (settings.data_dir / log_name).open("w", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.execution.worker",
                "--node-id",
                "demo-host-node",
                "--label",
                "DEMO host worker",
                "--slots",
                str(slots),
                "--operations",
                *operations,
            ],
            cwd=ROOT / "backend",
            env=environment,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        processes.append((process, handle))
        return process

    def stop_host(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    try:
        with TestClient(app, base_url="https://testserver") as client:
            signed_in = client.post(
                "/api/v1/auth/login",
                json={"username": "demo-node-owner", "password": "DEMO node owner password"},
            )
            assert signed_in.status_code == 200, signed_in.text
            client.headers["X-CSRF-Token"] = signed_in.json()["csrf_token"]
            created = client.post(
                "/api/v1/projects",
                json={"title": "DEMO cross-runtime film", "brief": "DEMO / MOCK only"},
            )
            assert created.status_code == 201, created.text
            apr = created.json()
            root = f"/api/v1/projects/{apr['project_id']}"
            plan = client.post(
                root + "/director",
                json={"base_revision": apr["revision"], "message": "DEMO four-shot production"},
            ).json()
            approved = client.post(
                root + f"/plans/{plan['id']}/execute",
                json={"base_revision": apr["revision"], "approved": True},
            )
            assert approved.status_code == 200, approved.text
            apr = approved.json()
            host = start_host(["generate_video"], "host-generation.log", slots=2)
            generated = client.post(
                root + "/generate",
                json={
                    "base_revision": apr["revision"],
                    "shot_ids": [shot["id"] for shot in apr["shots"]],
                    "provider_id": "mock-video-local",
                },
            )
            assert generated.status_code == 202, generated.text
            for job in generated.json():
                finished = wait_for(
                    lambda: client.get(root + f"/jobs/{job['id']}").json(),
                    lambda value: value["state"] in {"completed", "failed"},
                )
                assert finished["state"] == "completed", finished
                assert finished["worker_node_id"] == "demo-host-node"
            apr = client.get(root + "/apr").json()
            original_assets = {asset["id"]: asset["sha256"] for asset in apr["assets"]}
            body = {
                "base_revision": apr["revision"],
                "captions": [
                    {
                        "id": str(uuid4()),
                        "start_frame": 0,
                        "duration_frames": 96,
                        "text": "DEMO 跨节点中文字幕",
                    }
                ],
            }
            composition = client.put(root + "/timeline/composition", json=body)
            assert composition.status_code == 200, composition.text
            apr = composition.json()
            remote_url = make_url(settings.database_url)
            network = []
            if remote_url.host in {"localhost", "127.0.0.1"}:
                if sys.platform == "win32":
                    remote_url = remote_url.set(host="host.docker.internal")
                else:
                    network = ["--network", "host"]
            remote_environment = {
                **os.environ,
                "AFDS_DATABASE_URL": remote_url.render_as_string(hide_password=False),
            }
            started = docker(
                "run",
                "--detach",
                "--name",
                name,
                "--label",
                "io.afds.integration=product-completion",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--cpus",
                "2",
                "--memory",
                "1g",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=268435456",
                "--mount",
                f"type=bind,source={settings.data_dir},target=/data",
                "--env",
                "AFDS_DATABASE_URL",
                "--env",
                "AFDS_MODE=team",
                "--env",
                "AFDS_LEASE_SECONDS=4",
                *network,
                image_id,
                "python",
                "-m",
                "app.execution.worker",
                "--node-id",
                "demo-linux-node",
                "--label",
                "DEMO Linux worker",
                "--operations",
                "render_timeline",
                environment=remote_environment,
            )
            container_id = started.stdout.strip()
            assert re.fullmatch(r"[a-f0-9]{64}", container_id)

            def linux_node():
                return next(
                    (node for node in registry.list() if node["id"] == "demo-linux-node"), None
                )

            node = wait_for(linux_node, lambda value: value is not None, timeout=60)
            assert node["storage"]["status"] == "available", node
            assert "Linux" in node["runtime"]["platform"]
            rendered = client.post(root + "/render", json={"base_revision": apr["revision"]})
            assert rendered.status_code == 202, rendered.text
            first_job_id = rendered.json()["id"]
            first = wait_for(
                lambda: client.get(root + f"/jobs/{first_job_id}").json(),
                lambda value: value["state"] in {"completed", "failed"},
            )
            assert first["state"] == "completed", first
            assert first["worker_node_id"] == "demo-linux-node"
            linux_film = asset_path(database, apr["project_id"], first["output_asset_ids"][0])
            metadata = probe(settings, linux_film)
            assert (
                metadata["codec"] == "h264"
                and metadata["width"] == 1280
                and metadata["height"] == 720
            )
            assert metadata["duration_frames"] == 480
            run_process(
                [
                    settings.binary("ffmpeg"),
                    "-y",
                    "-v",
                    "error",
                    "-ss",
                    "1",
                    "-i",
                    str(linux_film),
                    "-frames:v",
                    "1",
                    str(ROOT / "data/worker-linux-caption.png"),
                ],
                60,
                lambda: True,
            )
            apr = client.get(root + "/apr").json()
            body["base_revision"] = apr["revision"]
            body["captions"][0]["text"] = "DEMO 节点故障接管"
            composition = client.put(root + "/timeline/composition", json=body)
            assert composition.status_code == 200, composition.text
            queued = client.post(
                root + "/render", json={"base_revision": composition.json()["revision"]}
            )
            assert queued.status_code == 202, queued.text
            job_id = queued.json()["id"]
            lost = wait_for(
                lambda: client.get(root + f"/jobs/{job_id}").json(),
                lambda value: (
                    value["state"] == "running" and value["worker_node_id"] == "demo-linux-node"
                ),
                timeout=60,
            )
            assert lost["attempt"] == 1
            owned = json.loads(
                docker("inspect", "--format", "{{json .Config.Labels}}", container_id).stdout
            )
            assert owned["io.afds.integration"] == "product-completion"
            docker("kill", container_id)
            stop_host(host)
            start_host(["render_timeline"], "host-recovery.log")
            recovered = wait_for(
                lambda: client.get(root + f"/jobs/{job_id}").json(),
                lambda value: (
                    value["attempt"] == 2
                    and value["state"] in {"running", "verifying", "completed", "failed"}
                ),
            )
            assert recovered["worker_node_id"] == "demo-host-node", recovered
            with database.transaction() as connection, pytest.raises(Cancelled):
                Queue(database, node=node).check_owned(connection, lost, lost["lease_owner"])
            completed = wait_for(
                lambda: client.get(root + f"/jobs/{job_id}").json(),
                lambda value: value["state"] in {"completed", "failed"},
            )
            assert completed["state"] == "completed" and completed["attempt"] == 2, completed
            final_apr = client.get(root + "/apr").json()
            assert (
                len(
                    [
                        asset
                        for asset in final_apr["assets"]
                        if asset["provenance"].get("job_id") == job_id
                    ]
                )
                == 1
            )
            for asset_id, sha in original_assets.items():
                assert file_hash(asset_path(database, apr["project_id"], asset_id)) == sha
            final_film = asset_path(database, apr["project_id"], completed["output_asset_ids"][0])
            assert probe(settings, final_film)["duration_frames"] == 480
            evidence = {
                "image_id": image_id,
                "nodes": [
                    {"id": value["id"], "runtime": value["runtime"], "slots": value["slots"]}
                    for value in registry.list()
                ],
                "linux_render": {"job_id": first_job_id, "metadata": metadata},
                "recovery": {
                    "job_id": job_id,
                    "lost_state": lost["state"],
                    "attempt": completed["attempt"],
                    "node": completed["worker_node_id"],
                },
                "unchanged_source_asset_count": len(original_assets),
                "final_sha256": file_hash(final_film),
                "host_cpu_count": os.cpu_count(),
            }
            (ROOT / "data/worker-nodes-evidence.json").write_text(
                json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        for process, handle in processes:
            stop_host(process)
            handle.close()
        # A timed-out `docker run` may have created the uniquely named container.
        cleanup_identity = container_id or name
        if cleanup_identity:
            labels = docker(
                "inspect", "--format", "{{json .Config.Labels}}", cleanup_identity, check=False
            )
            if (
                labels.returncode == 0
                and json.loads(labels.stdout).get("io.afds.integration") == "product-completion"
            ):
                (settings.data_dir / "linux-worker.log").write_text(
                    docker("logs", cleanup_identity, check=False).stdout, encoding="utf-8"
                )
                docker("rm", "--force", cleanup_identity)
        app.state.database.engine.dispose()
