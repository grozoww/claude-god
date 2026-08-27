"""M4 acceptance: the dashboard can do everything the CLI can."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from ccgod.api import create_app
from ccgod.config import LaneConfig, ProjectConfig, Repo, ServerConfig, save_project_config
from ccgod.models import Phase
from fastapi.testclient import TestClient


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("CCGOD_HOME", str(tmp_path / "ccgodhome"))
    r = tmp_path / "repo"
    r.mkdir()
    (r / "README.md").write_text("x\n")
    git("init", "-q", "-b", "main", cwd=r)
    git("config", "user.email", "t@t", cwd=r)
    git("config", "user.name", "t", cwd=r)
    git("add", "-A", cwd=r)
    git("commit", "-qm", "init", cwd=r)
    save_project_config(r, ProjectConfig(name="repo", lanes={"main": LaneConfig(paths=["**"])}))
    return r


@pytest.fixture
def client(repo, tmp_path):
    cfg = ServerConfig(repos=[Repo(name="repo", path=str(repo))], max_parallel=1)
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def test_health_and_repos(client, repo):
    h = client.get("/api/health").json()
    assert h["ok"] is True
    assert h["repos"] == 1
    repos = client.get("/api/repos").json()
    assert repos[0]["name"] == "repo"
    assert repos[0]["lanes"] == ["main"]
    assert repos[0]["onboarded"] is False


def test_create_list_and_get_task(client, repo):
    r = client.post("/api/tasks", json={
        "repo": "repo", "title": "do a thing", "description": "details", "start": False,
    })
    assert r.status_code == 200
    task = r.json()
    assert task["phase"] == "new"
    assert task["title"] == "do a thing"

    listed = client.get("/api/tasks").json()
    assert [t["id"] for t in listed] == [task["id"]]

    got = client.get(f"/api/tasks/{task['id']}").json()
    assert got["spec"] is None
    assert got["reviews"] == []
    assert got["running"] is False


def test_unknown_task_is_404(client):
    assert client.get("/api/tasks/nope").status_code == 404
    assert client.post("/api/tasks/nope/start").status_code == 404


def test_create_in_unregistered_repo_is_400(client):
    r = client.post("/api/tasks", json={"repo": "/nope/nope", "title": "x", "start": False})
    assert r.status_code == 400


def test_spec_edit_then_approve(client, repo):
    task = client.post("/api/tasks", json={"repo": "repo", "title": "t", "start": False}).json()
    tid = task["id"]

    # Simulate the spec phase having finished.
    from ccgod.store import TaskStore

    store = TaskStore(repo, tid)
    state = store.load()
    state.phase = Phase.AWAITING_SPEC_APPROVAL
    store.save(state)

    spec = "# Spec\n\n## Acceptance criteria\n\n- [ ] AC-1: works\n      check: review\n"
    r = client.put(f"/api/tasks/{tid}/spec", json={"text": spec})
    assert r.status_code == 200
    assert [c["id"] for c in r.json()["criteria"]] == ["AC-1"]
    assert store.spec_path.read_text() == spec

    # Approving moves it on and kicks the pipeline.
    r = client.post(f"/api/tasks/{tid}/approve")
    assert r.status_code == 200
    assert r.json()["phase"] in {"plan", "failed", "implement"}


def test_approve_in_wrong_phase_is_400(client, repo):
    task = client.post("/api/tasks", json={"repo": "repo", "title": "t", "start": False}).json()
    assert client.post(f"/api/tasks/{task['id']}/approve").status_code == 400


def test_redo_records_feedback_as_a_finding(client, repo):
    from ccgod.store import TaskStore

    task = client.post("/api/tasks", json={"repo": "repo", "title": "t", "start": False}).json()
    tid = task["id"]
    store = TaskStore(repo, tid)
    state = store.load()
    state.phase, state.round = Phase.DONE, 1
    store.save(state)

    r = client.post(f"/api/tasks/{tid}/redo", json={"note": "wrong error message"})
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "revise"
    assert body["pending_findings"][-1]["source"] == "human"
    assert (store.dir / "feedback" / "1.md").read_text().strip() == "wrong error message"

    detail = client.get(f"/api/tasks/{tid}").json()
    assert detail["feedback"][0]["text"].strip() == "wrong error message"


def test_redo_requires_a_note(client, repo):
    task = client.post("/api/tasks", json={"repo": "repo", "title": "t", "start": False}).json()
    assert client.post(f"/api/tasks/{task['id']}/redo", json={"note": "  "}).status_code == 400


def test_file_endpoint_cannot_escape_the_task_folder(client, repo):
    task = client.post("/api/tasks", json={"repo": "repo", "title": "t", "start": False}).json()
    tid = task["id"]
    assert client.get(f"/api/tasks/{tid}/file", params={"path": "../../../etc/passwd"}).status_code == 400
    assert client.get(f"/api/tasks/{tid}/file", params={"path": "nope.md"}).status_code == 404

    from ccgod.store import TaskStore
    TaskStore(repo, tid).write_text("plan.md", "# Plan\n")
    r = client.get(f"/api/tasks/{tid}/file", params={"path": "plan.md"})
    assert r.json()["text"] == "# Plan\n"


def test_cancel(client, repo):
    task = client.post("/api/tasks", json={"repo": "repo", "title": "t", "start": False}).json()
    r = client.post(f"/api/tasks/{task['id']}/cancel")
    assert r.json()["phase"] == "cancelled"


def test_usage_rollup(client, repo):
    from ccgod.store import TaskStore

    task = client.post("/api/tasks", json={"repo": "repo", "title": "t", "start": False}).json()
    store = TaskStore(repo, task["id"])
    st = store.load()
    st.usage.cost_usd, st.usage.input_tokens = 1.25, 500
    store.save(st)
    data = client.get("/api/usage").json()
    assert data["tasks"] == 1
    assert data["total"]["cost_usd"] == 1.25
    assert data["total"]["input_tokens"] == 500


def test_project_config_read_write(client, repo):
    cfg = client.get("/api/repos/repo/config").json()
    assert cfg["lanes"]["main"]["paths"] == ["**"]
    cfg["max_rounds"] = 5
    r = client.put("/api/repos/repo/config", json=cfg)
    assert r.json()["max_rounds"] == 5
    assert json.loads((repo / ".agents" / "config.json").read_text())["max_rounds"] == 5


def test_gitignore_gets_the_task_folder(client, repo):
    client.post("/api/tasks", json={"repo": "repo", "title": "t", "start": False})
    assert ".agents/tasks/" in (repo / ".gitignore").read_text()
