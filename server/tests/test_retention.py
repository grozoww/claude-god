"""Retention, archiving and deletion."""

from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from ccgod import retention
from ccgod.config import Repo, Retention, ServerConfig
from ccgod.models import Phase, TaskState, utcnow
from ccgod.service import Service
from ccgod.store import TaskStore, iter_tasks


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "home"))
    r = tmp_path / "repo"
    r.mkdir()
    (r / "README.md").write_text("x\n")
    git("init", "-q", "-b", "main", cwd=r)
    git("config", "user.email", "t@t", cwd=r)
    git("config", "user.name", "t", cwd=r)
    git("add", "-A", cwd=r)
    git("commit", "-qm", "init", cwd=r)
    return r


def make(repo: Path, *, days_old: float = 0, phase: Phase = Phase.DONE,
         title: str = "t", archived: bool = False) -> TaskStore:
    state = TaskState(title=title, repo=str(repo), phase=phase, archived=archived)
    state.updated_at = utcnow() - timedelta(days=days_old)
    store = TaskStore(repo, state.id)
    store.init_dirs()
    # save() re-stamps updated_at, so write the file directly to control age.
    store.state_path.write_text(state.model_dump_json(indent=2))
    return store


POLICY = Retention(max_tasks=3, max_age_days=30)


def test_keeps_everything_under_both_limits(repo):
    for i in range(3):
        make(repo, days_old=i, title=f"t{i}")
    assert retention.plan(repo, POLICY) == []


def test_drops_beyond_the_newest_n(repo):
    for i in range(6):
        make(repo, days_old=i, title=f"t{i}")
    doomed = retention.plan(repo, POLICY)
    assert len(doomed) == 3
    assert {d.reason for d in doomed} == {"count"}
    # The three newest survive.
    assert {d.title for d in doomed} == {"t3", "t4", "t5"}


def test_drops_by_age_even_when_under_the_count(repo):
    make(repo, days_old=1, title="fresh")
    make(repo, days_old=45, title="ancient")
    doomed = retention.plan(repo, POLICY)
    assert [d.title for d in doomed] == ["ancient"]
    assert doomed[0].reason == "age"
    assert doomed[0].age_days == pytest.approx(45, abs=0.1)


def test_unfinished_tasks_are_spared_by_default(repo):
    make(repo, days_old=99, phase=Phase.IMPLEMENT, title="in flight")
    assert retention.plan(repo, POLICY) == []
    forced = retention.plan(repo, POLICY, include_unfinished=True)
    assert [d.title for d in forced] == ["in flight"]


def test_escalated_is_prunable_but_running_never_is(repo):
    make(repo, days_old=99, phase=Phase.ESCALATED, title="stuck")
    assert [d.title for d in retention.plan(repo, POLICY)] == ["stuck"]

    live = make(repo, days_old=99, phase=Phase.DONE, title="live")
    doomed = retention.plan(repo, POLICY, is_running=lambda tid: tid == live.task_id)
    assert "live" not in {d.title for d in doomed}


def test_archived_tasks_are_still_pruned(repo):
    make(repo, days_old=99, archived=True, title="old archive")
    assert [d.title for d in retention.plan(repo, POLICY)] == ["old archive"]


def test_disabled_policy_does_nothing(repo):
    for i in range(10):
        make(repo, days_old=i * 20, title=f"t{i}")
    assert retention.plan(repo, Retention(enabled=False)) == []


def test_zero_limits_disable_each_rule(repo):
    for i in range(5):
        make(repo, days_old=i * 40, title=f"t{i}")
    assert retention.plan(repo, Retention(max_tasks=0, max_age_days=0)) == []


async def test_apply_removes_the_folders(repo):
    keep = make(repo, days_old=1, title="keep")
    drop = make(repo, days_old=99, title="drop")
    removed = await retention.apply(repo, POLICY)
    assert [r.title for r in removed] == ["drop"]
    assert not drop.dir.exists()
    assert keep.dir.exists()
    assert [t.title for t in iter_tasks(repo)] == ["keep"]


# ---- service level ---------------------------------------------------
def svc_for(repo: Path) -> Service:
    cfg = ServerConfig(repos=[Repo(name="repo", path=str(repo))], retention=POLICY)
    return Service(cfg)


def test_archive_hides_from_the_default_list(repo):
    svc = svc_for(repo)
    a = make(repo, title="visible")
    b = make(repo, title="hidden")
    svc.set_archived(b.task_id, True)

    assert [t.title for t in svc.list_tasks()] == ["visible"]
    assert [t.title for t in svc.list_tasks(archived=True)] == ["hidden"]
    assert {t.title for t in svc.list_tasks(archived=None)} == {"visible", "hidden"}
    assert svc.store(a.task_id).load().archived is False


async def test_delete_task_removes_the_folder(repo):
    svc = svc_for(repo)
    store = make(repo, title="doomed")
    res = await svc.delete_task(store.task_id)
    assert res["deleted"] is True
    assert not store.dir.exists()
    assert svc.list_tasks() == []


async def test_delete_can_take_the_branch_with_it(repo):
    svc = svc_for(repo)
    git("branch", "ccgod/x", cwd=repo)
    state = TaskState(title="b", repo=str(repo), phase=Phase.DONE, branch="ccgod/x")
    store = TaskStore(repo, state.id)
    store.init_dirs()
    store.save(state)

    branches = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                              cwd=repo, capture_output=True, text=True).stdout
    assert "ccgod/x" in branches

    res = await svc.delete_task(store.task_id, branch=True)
    assert res["branch_deleted"] == "ccgod/x"
    branches = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                              cwd=repo, capture_output=True, text=True).stdout
    assert "ccgod/x" not in branches


async def test_prune_across_repos_reports_what_it_removed(repo):
    svc = svc_for(repo)
    make(repo, days_old=1, title="keep")
    make(repo, days_old=90, title="old")
    plan = svc.prune_plan()
    assert [p.title for p in plan] == ["old"]
    removed = await svc.prune()
    assert [r.title for r in removed] == ["old"]
    assert [t.title for t in svc.list_tasks()] == ["keep"]
    assert svc.prune_plan() == []
