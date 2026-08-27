"""Render the CLI run as an image for the README.

Replays a real task's `events.jsonl` and final state through the same formatting
`ccgod run` uses, so the numbers in the picture are the ones that run produced.
Rich exports SVG; Chrome turns that into a PNG, since GitHub renders PNG most
reliably.

    uv run python scripts/shoot-cli.py <task-id>
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import subprocess
import sys
from math import ceil
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from ccgod.cli import _summary
from ccgod.service import Service
from rich.console import Console
from rich.panel import Panel

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PORT = 9223
OUT = ROOT / "docs" / "img"


def render_svg(task_id: str) -> Path:
    svc = Service()
    store = svc.store(task_id)
    state = store.load()

    console = Console(record=True, width=96, force_terminal=True)
    console.print(f'[green]$[/] ccgod new [white]"{state.title}"[/] -r toy-store\n')
    console.print(f"[green]created[/] {state.id}  [dim]{store.dir}[/]")
    for e in store.read_events():
        colour = {"warn": "yellow", "error": "red"}.get(e.level, "cyan")
        lane = f"[{e.lane}] " if e.lane else ""
        console.print(
            f"[dim]{e.ts:%H:%M:%S}[/] [{colour}]{e.kind:11}[/] {lane}{e.message}"
        )
    console.print()
    console.print(Panel.fit(_summary(state), title=f"task {state.id}"))

    OUT.mkdir(parents=True, exist_ok=True)
    svg = OUT / "cli.svg"
    svg.write_text(console.export_svg(title="ccgod run", font_aspect_ratio=0.61))
    return svg


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


async def svg_to_png(svg: Path) -> Path:
    raw = svg.read_text()

    # Rich emits a viewBox but no width/height, so an inlined <svg> collapses.
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', raw)
    if not m:
        raise RuntimeError("no viewBox in the rich SVG")
    w, h = ceil(float(m.group(1))), ceil(float(m.group(2)))

    # Drop the CDN webfont: local monospace renders identically and offline.
    raw = re.sub(r'url\("https://[^"]+"\) format\("woff2?"\),?\s*', "", raw)
    raw = raw.replace('<svg class="rich-terminal"', f'<svg class="rich-terminal" width="{w}" height="{h}"', 1)

    page = OUT / "_cli.html"
    page.write_text(
        "<style>html,body{margin:0;padding:0;background:#0e1116}svg{display:block}</style>"
        + raw
    )
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", "--user-data-dir=/tmp/god-shoot-cli",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(60):
            try:
                targets = httpx.get(f"http://127.0.0.1:{PORT}/json", timeout=1).json()
                pg = next((t for t in targets if t["type"] == "page"), None)
                if pg:
                    ws_url = pg["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        if not ws_url:
            raise RuntimeError("could not reach Chrome devtools")

        async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
            tab = Tab(ws)
            await tab.send("Page.enable")
            await tab.send("Emulation.setDeviceMetricsOverride",
                           width=w, height=h, deviceScaleFactor=2, mobile=False)
            await tab.send("Page.navigate", url=f"file://{page}")
            await asyncio.sleep(1.5)
            shot = await tab.send("Page.captureScreenshot", format="png")
            png = OUT / "cli.png"
            png.write_bytes(base64.b64decode(shot["data"]))
            return png
    finally:
        proc.terminate()
        page.unlink(missing_ok=True)


async def main() -> int:
    task_id = sys.argv[1] if len(sys.argv) > 1 else None
    if not task_id:
        print("usage: shoot-cli.py <task-id>")
        return 1
    svg = render_svg(task_id)
    png = await svg_to_png(svg)
    svg.unlink()
    print(f"{png.relative_to(ROOT)}  {png.stat().st_size // 1024} kB")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
