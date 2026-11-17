"""Isolated API and real worker for browser acceptance tests."""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
data = ROOT / "data" / "browser-tests" / uuid.uuid4().hex
data.mkdir(parents=True)
os.environ["AFDS_DATA_DIR"] = str(data)
os.environ["AFDS_DATABASE_URL"] = "sqlite:///" + (data / "afds.db").as_posix()
os.environ["AFDS_CORS_ORIGINS"] = "http://127.0.0.1:5174,http://localhost:5174"
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings
from app.db import Database

postgres_admin = None
namespace = None
if os.environ.get("AFDS_TEST_TEAM_BROWSER") == "1":
    os.environ["AFDS_MODE"] = "team"
    os.environ["AFDS_SECURE_COOKIES"] = "false"
    address = os.environ.get("AFDS_TEST_POSTGRES_URL")
    if address:
        from sqlalchemy import create_engine
        from sqlalchemy.engine import make_url

        namespace = "afds_browser_" + uuid.uuid4().hex
        postgres_admin = create_engine(address, isolation_level="AUTOCOMMIT")
        with postgres_admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{namespace}"')
        os.environ["AFDS_DATABASE_URL"] = (
            make_url(address)
            .update_query_dict({"options": f"-csearch_path={namespace}"})
            .render_as_string(hide_password=False)
        )

database = Database(Settings())
database.migrate()
if os.environ.get("AFDS_TEST_TEAM_BROWSER") == "1":
    from app.identity_service import IdentityService

    for name in ("owner", "editor", "reviewer", "viewer", "outsider"):
        IdentityService(database).create_user(
            f"demo-{name}", f"DEMO-{name}-password-2026", is_admin=name == "owner"
        )
database.engine.dispose()
flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
processes = [
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8011",
        ],
        cwd=ROOT / "backend",
        creationflags=flags,
    ),
    subprocess.Popen(
        [sys.executable, "-m", "app.execution.worker"],
        cwd=ROOT / "backend",
        creationflags=flags,
    ),
]
try:
    while all(p.poll() is None for p in processes):
        time.sleep(0.5)
finally:
    for process in processes:
        process.terminate()
    for process in processes:
        process.wait(timeout=10)
    if postgres_admin is not None:
        with postgres_admin.connect() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{namespace}" CASCADE')
        postgres_admin.dispose()
