"""M3 acceptance for the loop itself, proved with a fake runner (zero tokens).

The fake stands in for `claude -p`: it writes the same files a real agent would,
so every transition, the gate->revise path, the criteria lock and escalation are
exercised for real.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from ccgod.bus import EventBus
from ccgod.config import LaneConfig, ProjectConfig, ServerConfig, save_project_config
from ccgod.models import Phase, Status, TaskState
from ccgod.pipeline import Pipeline
from ccgod.runner import PhaseRequest, PhaseResult
from ccgod.store import TaskStore

SPEC = """# Spec: add marker

## Goal

Write a marker file.

## Acceptance criteria

- [ ] AC-1: marker.txt exists with the right content
      check: cmd `test -f marker.txt`
- [ ] AC-2: the code reads well
      check: review
"""


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    (r / "README.md").write_text("toy\n")
    git("init", "-q", "-b", "main", cwd=r)
    git("config", "user.email", "t@t", cwd=r)
    git("config", "user.name", "t", cwd=r)
    git("add", "-A", cwd=r)
    git("commit", "-qm", "init", cwd=r)
    save_project_config(
        r,
        ProjectConfig(
            name="toy", base_branch="main", gates=["test -f marker.txt"],
            lanes={"main": LaneConfig(paths=["**"], gates=[])},
        ),
    )
    git("add", "-A", cwd=r)
    git("commit", "-qm", "agents", cwd=r)
    return r


class FakeRunner:
    """Scripted stand-in for a headless Claude session."""

    def __init__(self, repo: Path, task_dir: Path) -> None:
        self.repo, self.task_dir = repo, task_dir
        self.calls: list[str] = []
        self.implement_writes = True     # flip to simulate a lazy implementer
        self.verdicts: list[str] = []    # queued critic verdicts

    async def run(self, req: PhaseRequest) -> PhaseResult:
        from ccgod.models import Usage

        self.calls.append(req.phase)
        if req.phase == "spec":
            (self.task_dir / "spec.md").write_text(SPEC)
        elif req.phase == "plan":
            (self.task_dir / "plan.md").write_text("# Plan\n\n## Lanes\n\n### main\n")
            (self.task_dir / "lanes").mkdir(exist_ok=True)
            (self.task_dir / "lanes" / "main.md").write_text("# Lane: main\n")
        elif req.phase == "implement":
            if self.implement_writes:
                (Path(req.cwd) / "marker.txt").write_text("hello\n")
        elif req.phase == "review":
            verdict = self.verdicts.pop(0) if self.verdicts else "pass"
            path = self.task_dir / "reviews" / f"round-{self._round()}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            body = {
                "verdict": verdict,
                "summary": f"fake {verdict}",
                "criteria_status": {"AC-1": "pass", "AC-2": "pass" if verdict == "pass" else "fail"},
                "findings": (
                    []
                    if verdict == "pass"
                    else [{"criterion": "AC-2", "problem": "not good", "required_change": "make it good"}]
                ),
            }
            path.write_text(json.dumps(body))
        return PhaseResult(
            ok=True, text=f"{req.phase} done", session_id=f"sess-{req.phase}",
            usage=Usage(input_tokens=10, output_tokens=5, cost_usd=0.01, turns=1),
        )

    def _round(self) -> int:
        state = TaskState.model_validate_json((self.task_dir / "state.json").read_text())
        return state.round


def build(repo: Path, **task_kw):
    task_kw.setdefault("auto_approve_spec", True)
    state = TaskState(title="add marker", repo=str(repo), **task_kw)
    store = TaskStore(repo, state.id)
    store.init_dirs()
    store.save(state)
    pipe = Pipeline(ServerConfig(max_parallel=2), EventBus())
    fake = FakeRunner(repo, store.dir)
    pipe.runner = fake
    return store, pipe, fake


async def test_happy_path_reaches_done(repo):
    store, pipe, fake = build(repo)
    state = await pipe.run(store)
    assert state.phase is Phase.DONE, state.error
    assert state.status is Status.DONE
    assert fake.calls == ["spec", "plan", "implement", "review"]
    assert (repo / "marker.txt").exists()
    assert state.usage.cost_usd == pytest.approx(0.04)
    assert state.verdict and state.verdict.verdict == "pass"


async def test_spec_approval_blocks_until_human(repo):
    store, pipe, _ = build(repo, auto_approve_spec=False)
    state = await pipe.run(store)
    assert state.phase is Phase.AWAITING_SPEC_APPROVAL
    assert state.status is Status.BLOCKED
    assert len(state.criteria) == 2          # parsed from the spec the agent wrote


async def test_failing_gate_sends_it_back_without_calling_the_critic(repo):
    """Gates run before any review agent — a bad round dies cheap."""
    store, pipe, fake = build(repo)
    fake.implement_writes = False            # marker.txt never created -> gate fails
    state = await pipe.run(store)
    assert "review" not in fake.calls
    assert state.phase is Phase.ESCALATED    # burned its rounds on the gate
    assert any(f.source == "gate" for f in state.pending_findings)
    assert fake.calls.count("implement") == state.max_rounds + 1


async def test_critic_revise_then_pass(repo):
    store, pipe, fake = build(repo)
    fake.verdicts = ["revise", "pass"]
    state = await pipe.run(store)
    assert state.phase is Phase.DONE
    assert state.round == 1
    assert fake.calls.count("implement") == 2
    assert store.load_verdict(0).verdict == "revise"
    assert store.load_verdict(1).verdict == "pass"


async def test_criteria_lock_after_first_revision(repo):
    store, pipe, fake = build(repo)
    fake.verdicts = ["revise", "pass"]
    await pipe.run(store)
    assert store.load().criteria_locked is True


async def test_escalates_instead_of_looping_forever(repo):
    store, pipe, fake = build(repo)
    fake.verdicts = ["revise"] * 10
    state = await pipe.run(store)
    assert state.phase is Phase.ESCALATED
    assert state.status is Status.BLOCKED
    assert state.round == state.max_rounds + 1
    assert fake.calls.count("review") == state.max_rounds + 1


async def test_findings_reach_the_implementer(repo):
    """The critic's findings become the next round's prompt."""
    store, pipe, fake = build(repo)
    fake.verdicts = ["revise", "pass"]
    seen: list[str] = []
    original = fake.run

    async def spy(req):
        if req.phase == "implement":
            seen.append(req.prompt)
        return await original(req)

    pipe.runner = type("R", (), {"run": staticmethod(spy)})()
    await pipe.run(store)
    assert "## This is revision round 1" in seen[1]
    assert "make it good" in seen[1]
    assert "## This is revision round" not in seen[0]


async def test_resume_after_a_crash(repo):
    """State lives on disk, so a fresh Pipeline picks the task back up."""
    store, pipe, _ = build(repo)
    await pipe.step(store)                    # spec only
    await pipe.step(store)                    # plan only
    assert store.load().phase is Phase.IMPLEMENT

    pipe2 = Pipeline(ServerConfig(), EventBus())
    pipe2.runner = FakeRunner(repo, store.dir)
    state = await pipe2.run(store)
    assert state.phase is Phase.DONE


async def test_human_redo_reopens_a_done_task(repo):
    from ccgod.service import Service

    store, pipe, fake = build(repo)
    await pipe.run(store)
    assert store.load().phase is Phase.DONE

    svc = Service(ServerConfig(repos=[]), pipe.bus)
    svc.cfg.repos = []
    svc.pipeline = pipe
    svc.store = lambda _id: store            # skip the registry for the test
    fake.verdicts = ["pass"]
    state = svc.redo(store.task_id, "the error message is wrong")
    assert state.phase is Phase.REVISE
    assert state.pending_findings[-1].source == "human"
    assert (store.dir / "feedback" / "1.md").read_text().startswith("the error")
    await pipe.run(store)
    assert store.load().phase is Phase.DONE


async def test_illegal_transition_is_recorded_as_failure(repo):
    store, pipe, _ = build(repo)
    state = store.load()
    state.phase = Phase.GATES                 # nothing implemented yet
    store.save(state)

    async def boom(req):
        raise AssertionError("should not run an agent")

    pipe.runner = type("R", (), {"run": staticmethod(boom)})()
    out = await pipe.run(store)
    # No gates can pass on an empty tree -> revise -> implement, which needs an agent.
    assert out.phase is Phase.FAILED
    assert out.error


async def test_events_are_written_and_published(repo):
    store, pipe, _ = build(repo)
    got: list[str] = []

    async def collect():
        async for ev in pipe.bus.subscribe(store.task_id, replay=False):
            got.append(ev.kind)

    t = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await pipe.run(store)
    await asyncio.sleep(0.05)
    t.cancel()
    kinds = {e.kind for e in store.read_events()}
    assert {"state", "phase_start", "phase_end", "gate", "verdict"} <= kinds
    assert "phase_start" in got
