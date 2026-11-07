"""Start the local API, independent worker and Studio. Ctrl+C stops all children."""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings
from app.db import Database


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="Check dependencies and migrate only"
    )
    args = parser.parse_args()
    settings = Settings()
    for binary in ("ffmpeg", "ffprobe"):
        print(f"{binary}: {settings.binary(binary)}")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm or not (ROOT / "frontend/node_modules/vite").is_dir():
        raise SystemExit("Install Node.js and run npm ci in frontend first.")
    Database(settings).migrate()
    if args.check:
        print("Environment and database ready.")
        return
    commands = [
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        [sys.executable, "-m", "app.execution.worker"],
        [npm, "run", "dev", "--", "--port", "5173", "--strictPort"],
    ]
    directories = [ROOT / "backend", ROOT / "backend", ROOT / "frontend"]
    processes = []
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        for command, directory in zip(commands, directories, strict=True):
            processes.append(
                subprocess.Popen(command, cwd=directory, creationflags=flags)
            )
        print("AFDS Studio: http://127.0.0.1:5173  API: http://127.0.0.1:8000/docs")
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
        raise SystemExit("A service exited. Inspect the preceding error and restart.")
    except KeyboardInterrupt:
        print("Stopping AFDS services.")
    finally:
        for process in processes:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            elif process.poll() is None:
                process.terminate()
        for process in processes:
            process.wait(timeout=10)


if __name__ == "__main__":
    main()
