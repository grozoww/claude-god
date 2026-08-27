"""Objective checks: shell commands that must exit 0.

Gates run before any review agent. They are cheap and cannot be argued with, so
most bad rounds die here without spending a single agent token.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .models import Finding, GateResult

MAX_TAIL = 4000


async def run_gate(
    cmd: str, cwd: Path, lane: str | None = None, timeout_s: int = 1800
) -> GateResult:
    started = time.monotonic()
    proc = await asyncio.create_subprocess_shell(
        cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        code = proc.returncode if proc.returncode is not None else 1
        text = out.decode(errors="replace")
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        code, text = 124, f"gate timed out after {timeout_s}s"
    return GateResult(
        name=cmd.split()[0] if cmd.split() else cmd,
        cmd=cmd,
        lane=lane,
        exit_code=code,
        passed=code == 0,
        duration_s=round(time.monotonic() - started, 2),
        output_tail=text[-MAX_TAIL:],
    )


async def run_gates(
    cmds: list[tuple[str, str | None]], cwd: Path, timeout_s: int = 1800
) -> list[GateResult]:
    """Run gates in order, stopping after the first failure.

    Sequential on purpose: a failing build makes later test output noise, and
    the first failure is the one the implementer must fix.
    """
    results: list[GateResult] = []
    for cmd, lane in cmds:
        res = await run_gate(cmd, cwd, lane, timeout_s)
        results.append(res)
        if not res.passed:
            break
    return results


def findings_from_gates(results: list[GateResult]) -> list[Finding]:
    """Turn a failing gate into the same Finding shape the critic emits.

    One loop, one format — the implementer cannot tell a gate failure from a
    critic complaint from a human note.
    """
    out: list[Finding] = []
    for r in results:
        if r.passed:
            continue
        out.append(
            Finding(
                problem=f"Gate failed (exit {r.exit_code}): `{r.cmd}`\n\n```\n{r.output_tail}\n```",
                required_change=f"Make `{r.cmd}` exit 0 without weakening the tests or the check itself.",
                lane=r.lane,
                source="gate",
            )
        )
    return out
