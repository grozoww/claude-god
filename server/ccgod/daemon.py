"""Track the running dashboard so it can be stopped and restarted.

`ccgod serve` writes a pidfile; `ccgod stop` reads it. A pid on its own is not
enough — pids get reused — so liveness is confirmed by asking the recorded
address for /api/health before believing the file.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import ccgod_home


def pid_path() -> Path:
    return ccgod_home() / "serve.pid"


@dataclass
class Server:
    pid: int
    host: str
    port: int
    started_at: float
    cwd: str

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def write(host: str, port: int) -> Path:
    p = pid_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "pid": os.getpid(), "host": host, "port": port,
        "started_at": time.time(), "cwd": os.getcwd(),
    }))
    return p


def clear() -> None:
    pid_path().unlink(missing_ok=True)


def read() -> Server | None:
    p = pid_path()
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text())
        return Server(int(d["pid"]), d.get("host", "127.0.0.1"), int(d["port"]),
                      float(d.get("started_at", 0)), d.get("cwd", ""))
    except (ValueError, KeyError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    return True


def responding(server: Server, timeout: float = 1.0) -> bool:
    try:
        r = httpx.get(f"{server.url}/api/health", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def status() -> tuple[Server | None, str]:
    """(server, state) where state is running | stale | stopped."""
    s = read()
    if s is None:
        return None, "stopped"
    if not _pid_alive(s.pid):
        return s, "stale"
    return s, "running" if responding(s) else "stale"


def stop(timeout: float = 10.0) -> tuple[bool, str]:
    """Terminate the recorded server. Returns (stopped, message)."""
    s, state = status()
    if s is None:
        return False, "no server recorded — nothing to stop"
    if state == "stale":
        clear()
        return False, f"no live server for pid {s.pid}; cleared the stale pidfile"

    try:
        os.kill(s.pid, signal.SIGTERM)
    except ProcessLookupError:
        clear()
        return False, "process had already exited; cleared the pidfile"

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(s.pid):
            clear()
            return True, f"stopped {s.url} (pid {s.pid})"
        time.sleep(0.2)

    os.kill(s.pid, signal.SIGKILL)
    clear()
    return True, f"force-killed pid {s.pid} after {timeout:.0f}s"


def spawn(host: str, port: int) -> Server:
    """Start `ccgod serve` detached and wait for it to answer."""
    # --foreground is essential: `serve` detaches by default, so without it the
    # child would spawn its own child and this would never see a live server.
    argv = [sys.executable, "-m", "ccgod", "serve", "--foreground",
            "--host", host, "--port", str(port)]
    log = ccgod_home() / "serve.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        subprocess.Popen(argv, stdout=fh, stderr=fh, start_new_session=True)

    deadline = time.time() + 30
    while time.time() < deadline:
        s, state = status()
        if s and state == "running":
            return s
        time.sleep(0.3)
    raise RuntimeError(f"server did not come up within 30s — see {log}")
