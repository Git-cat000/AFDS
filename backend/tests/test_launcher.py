import importlib.util
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from app.config import ROOT


def launcher():
    specification = importlib.util.spec_from_file_location("afds_launcher", ROOT / "scripts/dev.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_launcher_rejects_occupied_port_without_touching_owner():
    module = launcher()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        reserved.listen()
        port = reserved.getsockname()[1]
        with pytest.raises(SystemExit, match=str(port)):
            module.ensure_ports_available([port])
        assert reserved.fileno() >= 0


def test_launcher_waits_for_real_http_readiness_and_detects_child_failure():
    module = launcher()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"DEMO ready")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        module.wait_ready(
            [f"http://127.0.0.1:{server.server_port}"], [SimpleNamespace(poll=lambda: None)], 2
        )
        with pytest.raises(SystemExit, match="exited"):
            module.wait_ready(
                [f"http://127.0.0.1:{server.server_port}"], [SimpleNamespace(poll=lambda: 1)], 2
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
