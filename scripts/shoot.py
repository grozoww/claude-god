"""Capture dashboard screenshots for the README.

Drives headless Chrome over the DevTools protocol. `chrome --screenshot` cannot
be used here: the dashboard holds an SSE connection open, so the page never goes
network-idle and Chrome waits forever.

    uv run god serve --port 8799
    uv run python scripts/seed-screenshots.py
    uv run python scripts/shoot.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
from pathlib import Path

import httpx
import websockets

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9222
BASE = "http://localhost:8799"
OUT = Path(__file__).resolve().parents[1] / "docs" / "img"

WIDTH = 1440
MIN_H, MAX_H = 720, 1500

# name, url suffix
SHOTS = [
    ("dashboard", "/?task=t-20260827-0857-history"),
    ("findings",  "/?task=t-20260827-0803-ratelimit"),
    ("spec",      "/?task=t-20260827-0914-refund&tab=spec"),
    ("review",    "/?task=t-20260826-1740-dupsku"),
    ("plan",      "/?task=t-20260827-0857-history&tab=plan"),
    ("log",       "/?task=t-20260827-0857-history&tab=log"),
]


class Tab:
    def __init__(self, ws) -> None:
        self.ws, self._id = ws, 0

    async def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})


async def metrics(tab: Tab, width: int, height: int) -> None:
    await tab.send(
        "Emulation.setDeviceMetricsOverride",
        width=width, height=height, deviceScaleFactor=2, mobile=False,
    )


# `.detail` and `.list` are grid tracks that stretch to the viewport, so their own
# scrollHeight always equals the viewport. Measure the content inside them instead.
FIT_JS = """
(() => {
  const head = document.querySelector('header')?.offsetHeight ?? 0;
  const body = document.querySelector('.detail > *');
  const rows = document.querySelectorAll('.task-row');
  const last = rows[rows.length - 1];
  const listH = last ? last.offsetTop + last.offsetHeight : 0;
  const detailH = body ? body.getBoundingClientRect().height + 34 : 0;
  return Math.round(head + Math.max(listH, detailH));
})()
"""


async def shoot(tab: Tab, name: str, url: str) -> Path:
    await metrics(tab, WIDTH, MAX_H)
    await tab.send("Page.navigate", url=BASE + url)
    await asyncio.sleep(2.5)

    # Fit the viewport to whichever column is taller, so shots carry no dead space.
    res = await tab.send("Runtime.evaluate", returnByValue=True, expression=FIT_JS)
    height = max(MIN_H, min(MAX_H, int(res["result"].get("value") or MIN_H)))
    await metrics(tab, WIDTH, height)
    await asyncio.sleep(0.6)

    shot = await tab.send("Page.captureScreenshot", format="png", captureBeyondViewport=False)
    path = OUT / f"{name}.png"
    path.write_bytes(base64.b64decode(shot["data"]))
    return path


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        httpx.get(f"{BASE}/api/health", timeout=3)
    except Exception:
        print(f"dashboard not running at {BASE} — start `god serve --port 8799`")
        return 1

    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", "--user-data-dir=/tmp/god-shoot-profile",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(60):
            try:
                targets = httpx.get(f"http://127.0.0.1:{PORT}/json", timeout=1).json()
                page = next((t for t in targets if t["type"] == "page"), None)
                if page:
                    ws_url = page["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        if not ws_url:
            print("could not reach Chrome devtools")
            return 1

        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            tab = Tab(ws)
            await tab.send("Page.enable")
            await tab.send("Runtime.enable")
            for name, url in SHOTS:
                path = await shoot(tab, name, url)
                print(f"{path.relative_to(OUT.parents[1])}  {path.stat().st_size // 1024} kB")
    finally:
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
