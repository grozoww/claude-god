"""Run one pipeline phase as one headless Claude session.

Every phase is a fresh `claude -p` process: it reads the task folder, does its
job, writes files back. Orchestration itself costs zero tokens because it is
plain Python; only these calls burn anything.

Two backends behind one interface:
  * CliRunner  - subprocess `claude -p --output-format json` (default, no extra deps)
  * SdkRunner  - claude-agent-sdk, when installed and selected in config
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import Usage

# Substrings that mean "the plan's rate window is exhausted", not "the task failed".
RATE_LIMIT_MARKERS = (
    "rate limit",
    "usage limit",
    "too many requests",
    "429",
    "quota",
    "overloaded",
)

DEFAULT_TOOLS = [
    "Read", "Write", "Edit", "Glob", "Grep", "Bash", "TodoWrite", "WebFetch", "WebSearch",
]
READONLY_TOOLS = ["Read", "Glob", "Grep", "WebFetch", "WebSearch"]


class RateLimited(RuntimeError):
    """Raised when a phase failed only because the plan's window is exhausted."""


@dataclass
class PhaseRequest:
    prompt: str
    cwd: Path
    phase: str
    task_id: str
    lane: str | None = None
    model: str | None = None
    allowed_tools: list[str] = field(default_factory=lambda: list(DEFAULT_TOOLS))
    resume_session: str | None = None
    timeout_s: int = 3600
    add_dirs: list[Path] = field(default_factory=list)
    append_system_prompt: str | None = None


@dataclass
class PhaseResult:
    ok: bool
    text: str
    session_id: str | None
    usage: Usage
    raw: dict | None = None
    error: str | None = None


def _usage_from_payload(payload: dict, duration_s: float) -> Usage:
    u = payload.get("usage") or {}
    return Usage(
        input_tokens=int(u.get("input_tokens", 0) or 0),
        output_tokens=int(u.get("output_tokens", 0) or 0),
        cache_read_tokens=int(u.get("cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(u.get("cache_creation_input_tokens", 0) or 0),
        cost_usd=float(payload.get("total_cost_usd", 0.0) or 0.0),
        turns=int(payload.get("num_turns", 0) or 0),
        duration_s=duration_s,
    )


def _looks_rate_limited(text: str) -> bool:
    low = (text or "").lower()
    return any(m in low for m in RATE_LIMIT_MARKERS)


class Runner(Protocol):
    async def run(self, req: PhaseRequest) -> PhaseResult: ...


class CliRunner:
    """Drives the `claude` binary. Uses your existing subscription login."""

    def __init__(self, claude_bin: str = "claude") -> None:
        self.claude_bin = claude_bin

    def available(self) -> bool:
        return shutil.which(self.claude_bin) is not None

    def _argv(self, req: PhaseRequest) -> list[str]:
        argv = [self.claude_bin, "-p", "--output-format", "json"]
        if req.model:
            argv += ["--model", req.model]
        if req.resume_session:
            argv += ["--resume", req.resume_session]
        # Headless runs cannot answer a permission prompt. acceptEdits + an explicit
        # tool allowlist, never --dangerously-skip-permissions.
        argv += ["--permission-mode", "acceptEdits"]
        if req.allowed_tools:
            argv += ["--allowed-tools", ",".join(req.allowed_tools)]
        for d in req.add_dirs:
            argv += ["--add-dir", str(d)]
        if req.append_system_prompt:
            argv += ["--append-system-prompt", req.append_system_prompt]
        return argv

    async def run(self, req: PhaseRequest) -> PhaseResult:
        argv = self._argv(req)
        started = time.monotonic()
        env = {**os.environ, "GOD_TASK_ID": req.task_id, "GOD_PHASE": req.phase}
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(req.cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(req.prompt.encode()), timeout=req.timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return PhaseResult(
                ok=False, text="", session_id=None, usage=Usage(),
                error=f"phase timed out after {req.timeout_s}s",
            )

        duration = time.monotonic() - started
        stdout, stderr = out.decode(errors="replace"), err.decode(errors="replace")

        payload: dict | None = None
        try:
            payload = json.loads(stdout)
            if isinstance(payload, list):  # some versions emit a message array
                payload = next(
                    (m for m in reversed(payload) if m.get("type") == "result"), payload[-1]
                )
        except Exception:
            payload = None

        if payload is None:
            blob = stdout + "\n" + stderr
            if _looks_rate_limited(blob):
                raise RateLimited(blob.strip()[-500:])
            return PhaseResult(
                ok=False, text=stdout, session_id=None, usage=Usage(duration_s=duration),
                error=(stderr or stdout or "no output from claude").strip()[-2000:],
            )

        usage = _usage_from_payload(payload, duration)
        text = str(payload.get("result", "") or "")
        session_id = payload.get("session_id")
        is_error = bool(payload.get("is_error")) or payload.get("subtype") not in (
            None, "success",
        )
        if is_error and _looks_rate_limited(text + stderr):
            raise RateLimited((text or stderr).strip()[-500:])
        return PhaseResult(
            ok=not is_error and proc.returncode == 0,
            text=text,
            session_id=session_id,
            usage=usage,
            raw=payload,
            error=None if not is_error else (text or stderr or "claude reported an error")[-2000:],
        )


class SdkRunner:
    """Same contract via claude-agent-sdk. Optional dependency."""

    def __init__(self) -> None:
        from claude_agent_sdk import ClaudeAgentOptions, query  # noqa: F401

        self._query = query
        self._options = ClaudeAgentOptions

    async def run(self, req: PhaseRequest) -> PhaseResult:
        started = time.monotonic()
        opts_kwargs = dict(
            cwd=str(req.cwd),
            allowed_tools=req.allowed_tools,
            permission_mode="acceptEdits",
        )
        if req.model:
            opts_kwargs["model"] = req.model
        if req.resume_session:
            opts_kwargs["resume"] = req.resume_session
        if req.append_system_prompt:
            opts_kwargs["append_system_prompt"] = req.append_system_prompt

        text, session_id, usage = "", None, Usage()
        try:
            async for msg in self._query(prompt=req.prompt, options=self._options(**opts_kwargs)):
                session_id = getattr(msg, "session_id", None) or session_id
                if type(msg).__name__ == "ResultMessage":
                    text = getattr(msg, "result", "") or ""
                    payload = {
                        "usage": getattr(msg, "usage", {}) or {},
                        "total_cost_usd": getattr(msg, "total_cost_usd", 0.0) or 0.0,
                        "num_turns": getattr(msg, "num_turns", 0) or 0,
                    }
                    usage = _usage_from_payload(payload, time.monotonic() - started)
        except Exception as exc:  # noqa: BLE001
            if _looks_rate_limited(str(exc)):
                raise RateLimited(str(exc)) from exc
            return PhaseResult(
                ok=False, text=text, session_id=session_id,
                usage=Usage(duration_s=time.monotonic() - started), error=str(exc),
            )
        return PhaseResult(ok=True, text=text, session_id=session_id, usage=usage)


def make_runner(kind: str = "cli", claude_bin: str = "claude") -> Runner:
    if kind == "sdk":
        try:
            return SdkRunner()
        except Exception:
            pass  # SDK missing -> fall back to the CLI, which is always there
    return CliRunner(claude_bin)
