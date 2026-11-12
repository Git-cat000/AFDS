"""Start the local API, independent worker and Studio. Ctrl+C stops all children."""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings
from app.db import Database


def ensure_ports_available(ports):
    for port in ports:
        with socket.socket() as connection:
            try:
                connection.bind(("127.0.0.1", port))
            except OSError as error:
                raise SystemExit(
                    f"Port {port} is already in use. Open the running Studio or stop its service first."
                ) from error


def wait_ready(urls, processes, timeout):
    pending = set(urls)
    deadline = time.monotonic() + timeout
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while pending and time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise SystemExit(
                "A service exited during startup. Check the preceding error."
            )
        for url in list(pending):
            try:
                with opener.open(url, timeout=0.5) as response:
                    if response.status == 200:
                        pending.remove(url)
            except OSError:
                pass
        if pending:
            time.sleep(0.2)
    if pending:
        raise SystemExit("Services did not become ready before the startup timeout.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="Check dependencies and migrate only"
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open Studio after services are ready",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=45,
        help="Seconds to wait for local services",
    )
    args = parser.parse_args()
    settings = Settings()
    for binary in ("ffmpeg", "ffprobe"):
        print(f"{binary}: {settings.binary(binary)}")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm or not (ROOT / "frontend/node_modules/vite").is_dir():
        raise SystemExit("Install Node.js and run npm ci in frontend first.")
    if not args.check:
        ensure_ports_available([8000, 5173])
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
        wait_ready(
            ["http://127.0.0.1:8000/api/v1/health", "http://127.0.0.1:5173"],
            processes,
            args.startup_timeout,
        )
        if args.open_browser:
            webbrowser.open("http://127.0.0.1:5173")
        print("AFDS Studio: http://127.0.0.1:5173  API: http://127.0.0.1:8000/docs")
        while all(p.poll() is None for p in processes):
            time.sleep(0.5)
        raise SystemExit("A service exited. Inspect the preceding error and restart.")
    except KeyboardInterrupt:
        print("Stopping AFDS services.")
    finally:
        for process in processes:
            if process.poll() is not None:
                continue
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
