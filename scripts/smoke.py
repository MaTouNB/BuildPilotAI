"""Probe the real API and configured PostgreSQL; requires a running database."""

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ.setdefault("UV_CACHE_DIR", "/tmp/buildpilot-uv-cache")
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
process = subprocess.Popen(
    [
        "uv",
        "run",
        "--locked",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ],
    cwd=root / "backend",
)
try:
    deadline = time.monotonic() + 20
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                health = json.load(response)
            assert health["status"] == "ok"
            break
        except (urllib.error.URLError, TimeoutError):
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("API did not start") from None
            time.sleep(0.2)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=6) as response:
        ready = json.load(response)
    assert ready == {"status": "ready", "database": "connected"}
    print("PASS: real HTTP /health=200, /ready=200, PostgreSQL SELECT 1 succeeded")
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
