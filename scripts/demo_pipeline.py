"""Four real five-second Mock MP4 shots, independent worker, validated twenty-second film."""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings
from app.db import Database
from app.main import create_app
from app.media import verify
from app.storage import asset_path
from fastapi.testclient import TestClient


def main():
    settings = Settings()
    database = Database(settings)
    database.migrate()
    story = json.loads(
        (ROOT / "docs/examples/demo_story.json").read_text(encoding="utf-8")
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "demo-worker.log").open("w", encoding="utf-8") as log:
        worker = subprocess.Popen(
            [sys.executable, "-m", "app.execution.worker"],
            cwd=ROOT / "backend",
            stdout=log,
            stderr=log,
            creationflags=flags,
        )
        try:
            with TestClient(create_app(settings)) as client:

                def call(path, body=None):
                    response = (
                        client.get("/api/v1" + path)
                        if body is None
                        else client.post("/api/v1" + path, json=body)
                    )
                    response.raise_for_status()
                    return response.json()

                def wait_jobs(pid, job_ids):
                    deadline = time.monotonic() + 240
                    while time.monotonic() < deadline:
                        jobs = [call(f"/projects/{pid}/jobs/{jid}") for jid in job_ids]
                        if all(j["state"] == "completed" for j in jobs):
                            return
                        if any(
                            j["state"] in {"failed", "cancelled", "needs_review"}
                            for j in jobs
                        ):
                            raise RuntimeError(json.dumps(jobs, ensure_ascii=False))
                        if worker.poll() is not None:
                            raise RuntimeError(
                                "Worker stopped. Read data/logs/demo-worker.log"
                            )
                        time.sleep(0.25)
                    raise TimeoutError("Demo jobs exceeded four minutes")

                apr = call("/projects", story)
                pid = apr["project_id"]
                plan = call(
                    f"/projects/{pid}/director",
                    {
                        "base_revision": apr["revision"],
                        "message": "四镜头，每镜头五秒。",
                        "provider_id": "mock-director",
                    },
                )
                apr = call(
                    f"/projects/{pid}/plans/{plan['id']}/execute",
                    {
                        "base_revision": apr["revision"],
                        "approved": True,
                    },
                )
                jobs = call(
                    f"/projects/{pid}/generate",
                    {
                        "base_revision": apr["revision"],
                        "shot_ids": [s["id"] for s in apr["shots"]],
                        "provider_id": "mock-video-local",
                    },
                )
                wait_jobs(pid, [j["id"] for j in jobs])
                apr = call(f"/projects/{pid}/apr")
                render = call(
                    f"/projects/{pid}/render", {"base_revision": apr["revision"]}
                )
                wait_jobs(pid, [render["id"]])
                apr = call(f"/projects/{pid}/apr")
                source = asset_path(database, pid, apr["render_asset_id"])
                metadata = verify(settings, source, apr["format"], 480)
                output = settings.data_dir / "demo" / pid
                output.mkdir(parents=True)
                shutil.copyfile(source, output / "output.mp4")
                for filename, content in {
                    "project.apr.json": apr,
                    "manifest.json": call(f"/projects/{pid}/manifest"),
                    "validation.json": {
                        "project_id": pid,
                        "media": metadata,
                        "result": "pass",
                        "is_mock": True,
                    },
                }.items():
                    (output / filename).write_text(
                        json.dumps(content, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                (settings.data_dir / "demo" / "latest.json").write_text(
                    json.dumps(
                        {
                            "project_id": pid,
                            "output_dir": str(output),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                print(
                    json.dumps(
                        {"output": str(output / "output.mp4"), **metadata}, indent=2
                    )
                )
        finally:
            worker.terminate()
            worker.wait(timeout=10)


if __name__ == "__main__":
    main()
