"""Run both local development servers and stop them together with Ctrl+C."""

import os
import signal
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.environ.setdefault("UV_CACHE_DIR", "/tmp/buildpilot-uv-cache")
processes = []
try:
    processes.append(
        subprocess.Popen(
            [
                "uv",
                "run",
                "--locked",
                "uvicorn",
                "app.main:app",
                "--reload",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            cwd=root / "backend",
            start_new_session=True,
        )
    )
    processes.append(
        subprocess.Popen(["npm", "run", "dev"], cwd=root / "frontend", start_new_session=True)
    )
    while all(process.poll() is None for process in processes):
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
finally:
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
