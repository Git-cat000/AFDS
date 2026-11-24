"""Serve the installed Windows Studio and supervise its independent Worker."""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from app.config import ROOT

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
WORKER = "windows-desktop"


def configure(data_dir: Path, port: int):
    data_dir.mkdir(parents=True, exist_ok=True)
    # A developer shell's AFDS settings must not redirect installed user data.
    for key in tuple(os.environ):
        if key.startswith("AFDS_"):
            del os.environ[key]
    origin = f"http://{HOST}:{port}"
    os.environ.update(
        AFDS_DATA_DIR=str(data_dir),
        AFDS_DATABASE_URL="sqlite:///" + (data_dir / "afds.db").as_posix(),
        AFDS_CORS_ORIGINS=origin,
        AFDS_MODE="local",
        AFDS_DESKTOP_RUNTIME="1",
    )
    for name in ("ffmpeg", "ffprobe"):
        candidates = sorted((ROOT / "media").glob(f"*/bin/{name}.exe"))
        if candidates:
            os.environ[f"AFDS_{name.upper()}"] = str(candidates[0])
    return origin


def desktop_app(settings, frontend: Path):
    """Attach static files after the API routes on the trusted local origin."""
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from app.main import create_app

    application = create_app(settings)
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=[HOST])

    @application.middleware("http")
    async def prevent_embedding(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    application.mount("/", StaticFiles(directory=frontend, html=True), name="studio")
    return application


def monitor_parent(stop: threading.Event):
    # The Windows launcher owns the write end. EOF also handles launcher crashes.
    sys.stdin.buffer.read(1)
    stop.set()


def serve(data_dir: Path, port: int):
    from uvicorn import Config, Server

    from app.config import Settings
    from app.skill_process import ProcessTree
    from app.worker_registry import WorkerRegistry

    origin = configure(data_dir, port)
    settings = Settings(_env_file=None)
    frontend = ROOT / "frontend" / "dist"
    application = desktop_app(settings, frontend)
    database = application.state.database
    stop = threading.Event()
    threading.Thread(target=monitor_parent, args=(stop,), daemon=True).start()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((HOST, port))
        except OSError as error:
            raise RuntimeError("PORT_IN_USE") from error
        database.migrate()
        registry = WorkerRegistry(database)
        registry.initialize_store()
        previous = next((node for node in registry.list() if node["id"] == WORKER), {})
        with (data_dir / "worker.log").open("wb") as log:
            worker = subprocess.Popen(
                [sys.executable, "-B", "-m", "app.execution.worker", "--node-id", WORKER],
                cwd=ROOT / "backend",
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=os.name != "nt",
            )
            tree = ProcessTree(worker)
            server = Server(
                Config(
                    application, proxy_headers=False, access_log=False, timeout_graceful_shutdown=2
                )
            )
            server_thread = threading.Thread(
                target=server.run, kwargs={"sockets": [listener]}, daemon=True
            )
            server_thread.start()
            try:
                deadline = time.monotonic() + 40
                while not stop.wait(0.2):
                    if worker.poll() is not None or not server_thread.is_alive():
                        raise RuntimeError("SERVICE_EXITED")
                    if server.started and any(
                        node["id"] == WORKER
                        and node["status"] in {"online", "draining"}
                        and node["generation"] != previous.get("generation")
                        for node in registry.list()
                    ):
                        missing = [
                            name
                            for name in ("ffmpeg", "ffprobe")
                            if not shutil.which(getattr(settings, name))
                        ]
                        if missing:
                            print("AFDS_MEDIA_MISSING", flush=True)
                        print(f"AFDS_READY {origin}", flush=True)
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("STARTUP_TIMEOUT")
                while not stop.wait(0.3):
                    if worker.poll() is not None or not server_thread.is_alive():
                        raise RuntimeError("SERVICE_EXITED")
            finally:
                server.should_exit = True
                server_thread.join(timeout=4)
                tree.close()
                worker.wait(timeout=5)
                database.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    try:
        serve(args.data_dir.resolve(), args.port)
    except RuntimeError as error:
        print(f"AFDS_ERROR {error}", flush=True)
        raise


if __name__ == "__main__":
    main()
