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

Database(Settings()).migrate()
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
