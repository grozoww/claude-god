"""M5 acceptance: two lanes work in parallel worktrees and merge cleanly."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from god.bus import EventBus
from god.config import LaneConfig, ProjectConfig, ServerConfig, save_project_config, god_home
from god.models import Phase, TaskState
from god.pipeline import Pipeline, worktrees_root
from god.runner import PhaseRequest, PhaseResult
from god.store import TaskStore

SPEC = """# Spec: two lanes

## Acceptance criteria

- [ ] AC-1: api file exists
      check: cmd `test -f api/handler.py`
      lane: be
- [ ] AC-2: web file exists
      check: cmd `test -f web/view.ts`
      lane: fe
"""


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("GOD_HOME", str(tmp_path / "godhome"))
    r = tmp_path / "mono"
    (r / "api").mkdir(parents=True)
    (r / "web").mkdir(parents=True)
    (r / "api" / "__init__.py").write_text("")
    (r / "web" / "index.ts").write_text("export {}\n")
    git("init", "-q", "-b", "main", cwd=r)
    git("config", "user.email", "t@t", cwd=r)
    git("config", "user.name", "t", cwd=r)
    save_project_config(
        r,
        ProjectConfig(
            name="mono", base_branch="main",
            lanes={
                "be": LaneConfig(paths=["api/**"], gates=["test -f api/handler.py"]),
                "fe": LaneConfig(paths=["web/**"], gates=["test -f web/view.ts"]),
            },
        ),
    )
    git("add", "-A", cwd=r)
    git("commit", "-qm", "init", cwd=r)
    return r


class TwoLaneRunner:
    def __init__(self, task_dir: Path) -> None:
        self.task_dir = task_dir
        self.cwds: dict[str, Path] = {}
        self.calls: list[tuple[str, str | None]] = []

    async def run(self, req: PhaseRequest) -> PhaseResult:
        from god.models import Usage

        self.calls.append((req.phase, req.lane))
        if req.phase == "spec":
            (self.task_dir / "spec.md").write_text(SPEC)
        elif req.phase == "plan":
            (self.task_dir / "plan.md").write_text("# Plan\n")
            lanes = self.task_dir / "lanes"
            lanes.mkdir(exist_ok=True)
            (lanes / "be.md").write_text("# Lane: be\n")
            (lanes / "fe.md").write_text("# Lane: fe\n")
            (self.task_dir / "contract").mkdir(exist_ok=True)
            (self.task_dir / "contract" / "order.json").write_text('{"id": "string"}')
        elif req.phase == "implement":
            cwd = Path(req.cwd)
            self.cwds[req.lane] = cwd
            if req.lane == "be":
                (cwd / "api" / "handler.py").write_text("def handle():\n    return 1\n")
            else:
                (cwd / "web" / "view.ts").write_text("export const view = 1\n")
        elif req.phase == "review":
            state = TaskState.model_validate_json((self.task_dir / "state.json").read_text())
            p = self.task_dir / "reviews" / f"round-{state.round}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({
                "verdict": "pass", "summary": "ok",
                "criteria_status": {"AC-1": "pass", "AC-2": "pass"}, "findings": [],
            }))
        return PhaseResult(ok=True, text="done", session_id=f"s-{req.phase}-{req.lane}",
                           usage=Usage(cost_usd=0.01))


async def test_two_lanes_run_in_parallel_worktrees_and_merge(repo):
    state = TaskState(title="two lanes", repo=str(repo), auto_approve_spec=True)
    store = TaskStore(repo, state.id)
    store.init_dirs()
    store.save(state)
    pipe = Pipeline(ServerConfig(max_parallel=4), EventBus())
    runner = TwoLaneRunner(store.dir)
    pipe.runner = runner

    final = await pipe.run(store)
    assert final.phase is Phase.DONE, final.error

    # Each lane got its own worktree, and neither was the main repo.
    assert set(runner.cwds) == {"be", "fe"}
    assert runner.cwds["be"] != runner.cwds["fe"]
    assert runner.cwds["be"] != repo
    assert str(worktrees_root()) in str(runner.cwds["be"])

    # Both lanes' work survived the merge onto the task branch.
    assert (repo / "api" / "handler.py").exists()
    assert (repo / "web" / "view.ts").exists()

    # The integrate phase ran (it only exists for multi-lane tasks).
    assert ("integrate", None) in runner.calls
    assert final.lanes["be"].branch != final.lanes["fe"].branch

    # The contract was frozen at plan time, before either lane started.
    assert (store.dir / "contract" / "order.json").exists()
    plan_idx = runner.calls.index(("plan", None))
    first_impl = next(i for i, c in enumerate(runner.calls) if c[0] == "implement")
    assert plan_idx < first_impl


async def test_lane_worktrees_are_cleaned_up(repo):
    state = TaskState(title="two lanes", repo=str(repo), auto_approve_spec=True)
    store = TaskStore(repo, state.id)
    store.init_dirs()
    store.save(state)
    pipe = Pipeline(ServerConfig(max_parallel=4), EventBus())
    pipe.runner = TwoLaneRunner(store.dir)
    await pipe.run(store)

    paths = [Path(l.worktree) for l in store.load().lanes.values() if l.worktree]
    assert paths and all(p.exists() for p in paths)
    await pipe.cleanup_worktrees(store)
    assert all(not p.exists() for p in paths)
    assert all(l.worktree is None for l in store.load().lanes.values())


async def test_lane_criteria_are_routed_to_the_right_lane(repo):
    from god.models import LaneState
    from god.specparse import parse_criteria

    state = TaskState(title="x", repo=str(repo))
    state.criteria = parse_criteria(SPEC)
    state.lanes = {"be": LaneState(name="be"), "fe": LaneState(name="fe")}
    pipe = Pipeline(ServerConfig(), EventBus())
    assert [c.id for c in pipe._lane_criteria(state, "be")] == ["AC-1"]
    assert [c.id for c in pipe._lane_criteria(state, "fe")] == ["AC-2"]


async def test_single_lane_owns_every_criterion(repo):
    """Criteria tagged with lanes that do not exist must not vanish."""
    from god.models import LaneState
    from god.specparse import parse_criteria

    state = TaskState(title="x", repo=str(repo))
    state.criteria = parse_criteria(SPEC)          # tagged be/fe
    state.lanes = {"main": LaneState(name="main")}  # but the repo has one lane
    pipe = Pipeline(ServerConfig(), EventBus())
    assert [c.id for c in pipe._lane_criteria(state, "main")] == ["AC-1", "AC-2"]


async def test_lane_with_no_matching_criteria_gets_them_all(repo):
    from god.models import LaneState
    from god.specparse import parse_criteria

    state = TaskState(title="x", repo=str(repo))
    state.criteria = parse_criteria(SPEC)
    state.lanes = {"api": LaneState(name="api"), "web": LaneState(name="web")}
    pipe = Pipeline(ServerConfig(), EventBus())
    assert [c.id for c in pipe._lane_criteria(state, "api")] == ["AC-1", "AC-2"]
