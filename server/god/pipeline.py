r"""The pipeline. A state machine in plain Python driving headless Claude phases.

    spec -> (human approves) -> plan -> implement (lanes in parallel)
         -> integrate -> gates -> review -> done
                              \-> revise -> implement ... (max_rounds) -> escalated

Orchestration costs zero tokens. Only the phase calls burn anything, and gates
run before any review agent so most bad rounds die cheaply.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from datetime import timedelta
from pathlib import Path

from . import state_machine, worktree as wt
from .bus import EventBus
from .config import ProjectConfig, ServerConfig, load_project_config
from .gates import findings_from_gates, run_gates
from .models import (
    CheckKind,
    CritStatus,
    Event,
    Finding,
    LaneState,
    Phase,
    Status,
    TaskState,
    Usage,
    Verdict,
    utcnow,
)
from .prompts import (
    implement_prompt,
    integrate_prompt,
    plan_prompt,
    review_prompt,
    spec_prompt,
)
from .runner import DEFAULT_TOOLS, READONLY_TOOLS, PhaseRequest, RateLimited, make_runner
from .specparse import parse_criteria
from .store import TaskStore

RATE_LIMIT_PARK = timedelta(minutes=20)

#: Phases worth interrupting a human for.
NOTIFY_PHASES = {Phase.DONE, Phase.ESCALATED, Phase.FAILED, Phase.AWAITING_SPEC_APPROVAL}


def worktrees_root() -> Path:
    from .config import god_home

    return god_home() / "worktrees"


class PipelineError(RuntimeError):
    pass


class Pipeline:
    def __init__(self, server_cfg: ServerConfig, bus: EventBus) -> None:
        self.cfg = server_cfg
        self.bus = bus
        self.runner = make_runner(server_cfg.runner, server_cfg.claude_bin)
        self._slots = asyncio.Semaphore(max(1, server_cfg.max_parallel))
        self._running: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()
        # One runner per task, whoever calls. Without this, a CLI `run` racing a
        # background `start` would drive the same state machine twice.
        self._locks: dict[str, asyncio.Lock] = {}

    # ---- plumbing ----------------------------------------------------
    def emit(
        self,
        store: TaskStore,
        kind: str,
        message: str = "",
        *,
        phase: Phase | None = None,
        lane: str | None = None,
        level: str = "info",
        **data,
    ) -> None:
        ev = Event(
            task_id=store.task_id, kind=kind, message=message, phase=phase,
            lane=lane, level=level, data=data,
        )
        store.append_event(ev)
        self.bus.publish(ev)

    def _set_phase(self, store: TaskStore, state: TaskState, dst: Phase, status: Status) -> None:
        state_machine.check(state.phase, dst)
        src = state.phase
        state.phase, state.status = dst, status
        store.save(state)
        self.emit(store, "state", f"{src.value} -> {dst.value}", phase=dst, status=status.value)
        if dst in NOTIFY_PHASES:
            self._notify(state, dst)

    async def _call(
        self, store: TaskStore, state: TaskState, req: PhaseRequest, label: str
    ) -> str:
        """Run one headless Claude phase, accounting usage and rate limits."""
        self.emit(
            store, "phase_start", f"{label} started", phase=state.phase, lane=req.lane,
            model=req.model,
        )
        async with self._slots:
            if store.task_id in self._cancelled:
                raise asyncio.CancelledError()
            try:
                res = await self.runner.run(req)
            except RateLimited as exc:
                state.status = Status.BLOCKED
                state.parked_until = utcnow() + RATE_LIMIT_PARK
                state.error = f"rate limited: {exc}"
                store.save(state)
                self.emit(
                    store, "error", f"rate limited, parked until {state.parked_until:%H:%M}",
                    phase=state.phase, level="warn",
                )
                raise

        state.usage = state.usage.add(res.usage)
        store.save(state)
        self.emit(
            store, "phase_end", f"{label} finished", phase=state.phase, lane=req.lane,
            ok=res.ok,
            usage=res.usage.model_dump(),
            session_id=res.session_id,
        )
        if res.text:
            store.write_text(f"artifacts/{label.replace(' ', '_')}.md", res.text)
        if not res.ok:
            raise PipelineError(f"{label} failed: {res.error or 'unknown error'}")
        if req.lane and req.lane in state.lanes:
            state.lanes[req.lane].session_id = res.session_id
            store.save(state)
        if state.budget_usd and state.usage.cost_usd > state.budget_usd:
            raise PipelineError(
                f"budget exceeded: ${state.usage.cost_usd:.2f} > ${state.budget_usd:.2f}"
            )
        return res.text

    def _notify(self, state: TaskState, phase: Phase) -> None:
        """Fire the configured notify command. Never let it break a run."""
        cmd = self.cfg.notify_cmd
        if not cmd:
            return
        env = {
            **os.environ,
            "GOD_TASK_ID": state.id,
            "GOD_TASK_TITLE": state.title,
            "GOD_PHASE": phase.value,
            "GOD_REPO": state.repo,
        }
        try:
            subprocess.Popen(
                cmd, shell=True, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:  # noqa: BLE001
            pass

    # ---- git helpers -------------------------------------------------
    async def _resolve_base(self, repo: Path, cfg: ProjectConfig) -> str:
        if not await wt.is_repo(repo):
            raise PipelineError(f"{repo} is not a git repository")
        if not await wt.has_commits(repo):
            raise PipelineError(
                f"{repo} has no commits yet — make an initial commit first"
            )
        branches = await wt.git("branch", "--format=%(refname:short)", cwd=repo)
        names = set(branches.splitlines())
        if cfg.base_branch in names:
            return cfg.base_branch
        return await wt.current_branch(repo)

    # ---- phases ------------------------------------------------------
    async def phase_spec(self, store: TaskStore, state: TaskState, cfg: ProjectConfig) -> None:
        repo = store.repo
        note = None
        fb = sorted((store.dir / "feedback").glob("*.md")) if (store.dir / "feedback").exists() else []
        if fb:
            note = fb[-1].read_text()
        await self._call(
            store, state,
            PhaseRequest(
                prompt=spec_prompt(state, store.dir, repo, cfg, note),
                cwd=repo, phase="spec", task_id=state.id,
                model=cfg.models.get("spec"),
                allowed_tools=[*READONLY_TOOLS, "Write", "Bash"],
                timeout_s=self.cfg.phase_timeout_s,
            ),
            "spec",
        )
        if not store.spec_path.exists():
            raise PipelineError("spec phase did not write spec.md")
        state.criteria = parse_criteria(store.spec_path.read_text())
        if not state.criteria:
            raise PipelineError(
                "spec.md has no parseable acceptance criteria — "
                "expected `- [ ] AC-1: ...` items under an 'Acceptance criteria' heading"
            )
        store.save(state)
        self.emit(store, "log", f"{len(state.criteria)} acceptance criteria parsed",
                  phase=Phase.SPEC, criteria=[c.model_dump(mode="json") for c in state.criteria])

        if state.auto_approve_spec or cfg.auto_approve_spec:
            self._set_phase(store, state, Phase.PLAN, Status.IDLE)
        else:
            self._set_phase(store, state, Phase.AWAITING_SPEC_APPROVAL, Status.BLOCKED)

    async def phase_plan(self, store: TaskStore, state: TaskState, cfg: ProjectConfig) -> None:
        repo = store.repo
        base = await self._resolve_base(repo, cfg)
        state.base_branch = base
        state.branch = f"{cfg.branch_prefix}{state.id}"
        store.save(state)

        await self._call(
            store, state,
            PhaseRequest(
                prompt=plan_prompt(state, store.dir, repo, cfg),
                cwd=repo, phase="plan", task_id=state.id,
                model=cfg.models.get("plan"),
                allowed_tools=[*READONLY_TOOLS, "Write", "Bash"],
                timeout_s=self.cfg.phase_timeout_s,
            ),
            "plan",
        )
        if not store.plan_path.exists():
            raise PipelineError("plan phase did not write plan.md")

        # Lanes actually used = those the planner wrote a brief for, intersected
        # with the repo config. Falls back to every configured lane.
        written = {p.stem for p in (store.dir / "lanes").glob("*.md")}
        used = [n for n in cfg.lane_names if n in written] or cfg.lane_names or ["main"]
        state.lanes = {n: LaneState(name=n) for n in used}
        store.save(state)
        self.emit(store, "log", f"lanes: {', '.join(used)}", phase=Phase.PLAN)

        await wt.ensure_branch(repo, state.branch, base)
        await wt.commit_all(repo, f"god({state.id}): plan and contract")
        self._set_phase(store, state, Phase.IMPLEMENT, Status.IDLE)

    def _lane_criteria(self, state: TaskState, lane: str) -> list:
        """Criteria this lane must satisfy.

        A single-lane task owns everything. Otherwise the lane gets what it was
        assigned plus anything unassigned — and if that comes to nothing (a spec
        that named lanes which do not exist), it gets the whole list rather than
        an empty contract.
        """
        if len(state.lanes) <= 1:
            return list(state.criteria)
        owned = [c for c in state.criteria if c.lane in (lane, None)]
        return owned or list(state.criteria)

    def _lane_findings(self, state: TaskState, lane: str, single: bool) -> list[Finding]:
        if single:
            return list(state.pending_findings)
        return [f for f in state.pending_findings if f.lane in (lane, None)]

    async def phase_implement(self, store: TaskStore, state: TaskState, cfg: ProjectConfig) -> None:
        repo = store.repo
        lanes = list(state.lanes.keys()) or ["main"]
        single = len(lanes) == 1
        if not (state.branch and state.base_branch):
            raise PipelineError(
                "no task branch yet — the plan phase must run before implement"
            )

        await wt.git("checkout", state.branch, cwd=repo)

        # Git operations on one repo cannot run concurrently (index.lock races),
        # so every worktree is prepared first; only the agents run in parallel.
        cwds: dict[str, Path] = {}
        for lane in lanes:
            ls = state.lanes.setdefault(lane, LaneState(name=lane))
            if single:
                cwds[lane] = repo  # nobody else is touching the tree
                continue
            # NOT "<task-branch>/<lane>": git cannot hold both refs/heads/god/x
            # and refs/heads/god/x/be at once.
            lane_branch = f"{state.branch}--{lane}"
            path = worktrees_root() / state.id / lane
            await wt.worktree_add(repo, path, lane_branch, state.branch)
            ls.worktree, ls.branch = str(path), lane_branch
            cwds[lane] = path
            if cfg.setup_cmd:
                self.emit(store, "log", f"setup: {cfg.setup_cmd}", lane=lane)
                await run_gates([(cfg.setup_cmd, lane)], path)
        store.save(state)

        async def do_lane(lane: str) -> None:
            ls = state.lanes[lane]
            cwd = cwds[lane]
            ls.status = Status.RUNNING
            store.save(state)

            try:
                await self._call(
                    store, state,
                    PhaseRequest(
                        prompt=implement_prompt(
                            state, store.dir, lane,
                            self._lane_criteria(state, lane),
                            self._lane_findings(state, lane, single),
                            cfg, cwd,
                        ),
                        cwd=cwd, phase="implement", task_id=state.id, lane=lane,
                        model=cfg.models.get("implement"),
                        allowed_tools=list(DEFAULT_TOOLS),
                        add_dirs=[store.dir],
                        timeout_s=self.cfg.phase_timeout_s,
                    ),
                    f"implement {lane}",
                )
                ls.status = Status.DONE
            except Exception as exc:
                ls.status = Status.FAILED
                ls.last_error = str(exc)[-500:]
                store.save(state)
                raise
            finally:
                store.save(state)

        results = await asyncio.gather(*(do_lane(l) for l in lanes), return_exceptions=True)
        errs = [r for r in results if isinstance(r, BaseException)]
        if errs:
            if any(isinstance(e, (RateLimited, asyncio.CancelledError)) for e in errs):
                raise errs[0]
            raise PipelineError("; ".join(str(e) or type(e).__name__ for e in errs)[:1500])

        for lane in lanes:
            changed = await wt.commit_all(
                cwds[lane], f"god({state.id}): {lane} round {state.round}"
            )
            if not changed:
                self.emit(store, "log", f"lane {lane} made no changes",
                          lane=lane, level="warn")

        state.pending_findings = []
        store.save(state)
        nxt = Phase.GATES if single else Phase.INTEGRATE
        self._set_phase(store, state, nxt, Status.IDLE)

    async def phase_integrate(self, store: TaskStore, state: TaskState, cfg: ProjectConfig) -> None:
        repo = store.repo
        if not state.branch:
            raise PipelineError("no task branch to integrate onto")
        await wt.git("checkout", state.branch, cwd=repo)

        reports: list[str] = []
        conflicted = False
        for lane, ls in state.lanes.items():
            if not ls.branch:
                continue
            ok, out = await wt.merge_branch(repo, ls.branch)
            reports.append(f"### lane {lane} ({'ok' if ok else 'CONFLICT'})\n{out}")
            if not ok:
                conflicted = True
        report = "\n\n".join(reports) or "(nothing to merge)"
        store.write_text("artifacts/merge-report.md", report)
        self.emit(store, "log", "lanes merged" + (" with conflicts" if conflicted else ""),
                  phase=Phase.INTEGRATE, level="warn" if conflicted else "info")

        await self._call(
            store, state,
            PhaseRequest(
                prompt=integrate_prompt(state, store.dir, repo, report),
                cwd=repo, phase="integrate", task_id=state.id,
                model=cfg.models.get("integrate"),
                allowed_tools=list(DEFAULT_TOOLS),
                add_dirs=[store.dir],
                timeout_s=self.cfg.phase_timeout_s,
            ),
            "integrate",
        )
        await wt.commit_all(repo, f"god({state.id}): integrate round {state.round}")
        self._set_phase(store, state, Phase.GATES, Status.IDLE)

    def _gate_cmds(self, state: TaskState, cfg: ProjectConfig) -> list[tuple[str, str | None]]:
        cmds: list[tuple[str, str | None]] = []
        seen: set[str] = set()
        for lane in state.lanes:
            for g in (cfg.lanes.get(lane).gates if cfg.lanes.get(lane) else []):
                if g not in seen:
                    seen.add(g)
                    cmds.append((g, lane))
        for g in cfg.gates:
            if g not in seen:
                seen.add(g)
                cmds.append((g, None))
        # Criteria can carry their own command checks.
        for c in state.criteria:
            if c.check.kind == CheckKind.CMD and c.check.cmd and c.check.cmd not in seen:
                seen.add(c.check.cmd)
                cmds.append((c.check.cmd, c.lane))
        return cmds

    async def phase_gates(self, store: TaskStore, state: TaskState, cfg: ProjectConfig) -> None:
        repo = store.repo
        cmds = self._gate_cmds(state, cfg)
        if not cmds:
            self.emit(store, "log", "no gates configured — going straight to review",
                      phase=Phase.GATES, level="warn")
            state.gate_results = []
            store.save(state)
            self._set_phase(store, state, Phase.REVIEW, Status.IDLE)
            return

        self.emit(store, "log", f"running {len(cmds)} gates", phase=Phase.GATES)
        results = await run_gates(cmds, repo)
        state.gate_results = results
        for r in results:
            self.emit(store, "gate", f"{'PASS' if r.passed else 'FAIL'}: {r.cmd}",
                      phase=Phase.GATES, lane=r.lane,
                      level="info" if r.passed else "warn",
                      **r.model_dump(exclude={"lane"}))

        # Mark cmd-checked criteria from their own gate.
        by_cmd = {r.cmd: r for r in results}
        for c in state.criteria:
            if c.check.kind == CheckKind.CMD and c.check.cmd in by_cmd:
                c.status = CritStatus.PASS if by_cmd[c.check.cmd].passed else CritStatus.FAIL
        store.save(state)

        failed = [r for r in results if not r.passed]
        if failed:
            state.pending_findings = findings_from_gates(results)
            store.save(state)
            self._set_phase(store, state, Phase.REVISE, Status.IDLE)
        else:
            self._set_phase(store, state, Phase.REVIEW, Status.IDLE)

    async def phase_review(self, store: TaskStore, state: TaskState, cfg: ProjectConfig) -> None:
        repo = store.repo
        if not (state.branch and state.base_branch):
            raise PipelineError("no task branch to review")
        diff = await wt.diff_against(repo, state.base_branch)
        if not diff.strip():
            state.pending_findings = [
                Finding(problem="No code changes were produced on the task branch.",
                        required_change="Implement the spec.", source="gate")
            ]
            store.save(state)
            self._set_phase(store, state, Phase.REVISE, Status.IDLE)
            return

        gate_summary = "\n".join(
            f"- {'PASS' if r.passed else 'FAIL'} `{r.cmd}` (exit {r.exit_code})"
            for r in state.gate_results
        ) or "(no gates configured)"

        vpath = store.review_path(state.round)
        vpath.parent.mkdir(parents=True, exist_ok=True)
        vpath.unlink(missing_ok=True)

        text = await self._call(
            store, state,
            PhaseRequest(
                prompt=review_prompt(state, store.dir, repo, diff, gate_summary, vpath),
                cwd=repo, phase="review", task_id=state.id,
                model=cfg.models.get("review"),
                # Read-only: the critic must never be able to "fix" what it reviews.
                allowed_tools=[*READONLY_TOOLS, "Write"],
                add_dirs=[store.dir],
                timeout_s=self.cfg.phase_timeout_s,
            ),
            f"review round {state.round}",
        )

        verdict = self._read_verdict(store, state, text)
        state.verdict = verdict
        store.save_verdict(verdict)
        for cid, st in verdict.criteria_status.items():
            for c in state.criteria:
                if c.id == cid and c.check.kind != CheckKind.CMD:
                    c.status = st
        store.save(state)
        self.emit(store, "verdict", f"round {state.round}: {verdict.verdict}",
                  phase=Phase.REVIEW,
                  level="info" if verdict.verdict == "pass" else "warn",
                  **verdict.model_dump(mode="json"))

        if verdict.verdict == "pass":
            self._set_phase(store, state, Phase.DONE, Status.DONE)
        else:
            state.pending_findings = verdict.findings or [
                Finding(problem=verdict.summary or "critic asked for changes",
                        required_change="Address the review summary.", source="critic")
            ]
            store.save(state)
            self._set_phase(store, state, Phase.REVISE, Status.IDLE)

    def _read_verdict(self, store: TaskStore, state: TaskState, text: str) -> Verdict:
        """Prefer the JSON file; fall back to a fenced block in the reply."""
        vpath = store.review_path(state.round)
        raw: str | None = None
        if vpath.exists():
            raw = vpath.read_text()
        else:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
            if m:
                raw = m.group(1)
        if not raw:
            raise PipelineError("review phase produced no verdict JSON")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PipelineError(f"verdict JSON is invalid: {exc}") from exc
        data.setdefault("round", state.round)
        data["round"] = state.round
        return Verdict.model_validate(data)

    async def phase_revise(self, store: TaskStore, state: TaskState, cfg: ProjectConfig) -> None:
        # Criteria lock the moment we enter revision: from here the critic may
        # only judge the existing list, never invent new complaints.
        state.criteria_locked = True
        state.round += 1
        store.save(state)
        if state.round > state.max_rounds:
            self.emit(store, "log",
                      f"round {state.round} would exceed max_rounds={state.max_rounds}",
                      phase=Phase.REVISE, level="warn")
            self._set_phase(store, state, Phase.ESCALATED, Status.BLOCKED)
            return
        self.emit(store, "log", f"revision round {state.round}", phase=Phase.REVISE,
                  findings=[f.model_dump(mode="json") for f in state.pending_findings])
        self._set_phase(store, state, Phase.IMPLEMENT, Status.IDLE)

    # ---- driver ------------------------------------------------------
    async def step(self, store: TaskStore) -> TaskState:
        """Execute exactly one phase."""
        state = store.load()
        cfg = load_project_config(store.repo)
        state.max_rounds = state.max_rounds or cfg.max_rounds
        if state.budget_usd is None:
            state.budget_usd = cfg.budget_usd

        handlers = {
            Phase.NEW: None,
            Phase.SPEC: self.phase_spec,
            Phase.PLAN: self.phase_plan,
            Phase.IMPLEMENT: self.phase_implement,
            Phase.INTEGRATE: self.phase_integrate,
            Phase.GATES: self.phase_gates,
            Phase.REVIEW: self.phase_review,
            Phase.REVISE: self.phase_revise,
        }
        if state.phase is Phase.NEW:
            self._set_phase(store, state, Phase.SPEC, Status.RUNNING)
            state = store.load()

        handler = handlers.get(state.phase)
        if handler is None:
            return state

        state.status = Status.RUNNING
        state.error = None
        store.save(state)
        await handler(store, state, cfg)
        return store.load()

    async def run(self, store: TaskStore, max_steps: int = 40) -> TaskState:
        """Advance until the task finishes, blocks on a human, or errors."""
        lock = self._locks.setdefault(store.task_id, asyncio.Lock())
        async with lock:
            return await self._run(store, max_steps)

    async def _run(self, store: TaskStore, max_steps: int) -> TaskState:
        state = store.load()
        for _ in range(max_steps):
            state = store.load()
            if store.task_id in self._cancelled:
                state.status, state.phase = Status.IDLE, Phase.CANCELLED
                store.save(state)
                return state
            if state.phase in {Phase.DONE, Phase.FAILED, Phase.CANCELLED}:
                return state
            if state.phase in {Phase.AWAITING_SPEC_APPROVAL, Phase.ESCALATED}:
                state.status = Status.BLOCKED
                store.save(state)
                return state
            try:
                state = await self.step(store)
            except RateLimited:
                return store.load()
            except asyncio.CancelledError:
                state = store.load()
                state.status = Status.IDLE
                store.save(state)
                raise
            except Exception as exc:  # noqa: BLE001
                state = store.load()
                state.error = str(exc)[-2000:]
                state.status = Status.FAILED
                state.phase = Phase.FAILED
                store.save(state)
                self.emit(store, "error", state.error, phase=Phase.FAILED, level="error")
                return state
        self.emit(store, "error", f"stopped after {max_steps} steps", level="error")
        return store.load()

    # ---- external control -------------------------------------------
    def start(self, store: TaskStore) -> asyncio.Task:
        """Kick a task in the background, one runner per task id."""
        existing = self._running.get(store.task_id)
        if existing and not existing.done():
            return existing
        self._cancelled.discard(store.task_id)

        async def _go():
            try:
                await self.run(store)
            finally:
                self._running.pop(store.task_id, None)

        task = asyncio.create_task(_go(), name=f"god:{store.task_id}")
        self._running[store.task_id] = task
        return task

    def is_running(self, task_id: str) -> bool:
        t = self._running.get(task_id)
        return bool(t and not t.done())

    async def cancel(self, store: TaskStore) -> TaskState:
        self._cancelled.add(store.task_id)
        t = self._running.get(store.task_id)
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        state = store.load()
        if state.phase not in {Phase.DONE, Phase.CANCELLED}:
            state.phase, state.status = Phase.CANCELLED, Status.IDLE
            store.save(state)
        self.emit(store, "state", "cancelled", phase=Phase.CANCELLED, level="warn")
        return state

    async def cleanup_worktrees(self, store: TaskStore) -> None:
        state = store.load()
        for ls in state.lanes.values():
            if ls.worktree:
                await wt.worktree_remove(store.repo, Path(ls.worktree))
                ls.worktree = None
        store.save(state)
