"""Task folders on disk. The only persistence layer — no database.

Layout:
    <repo>/.agents/tasks/<task-id>/
        state.json        TaskState
        spec.md           requirements + acceptance criteria
        plan.md           lane split, order, notes
        contract/         frozen interface, written by plan, read-only for lanes
        lanes/<name>.md   per-lane brief
        reviews/round-N.json
        feedback/N.md     human redo notes
        artifacts/        raw agent transcripts per phase
        events.jsonl      append-only log
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from .config import AGENTS_DIR, TASKS_DIR
from .models import Event, TaskState, Verdict


def tasks_root(repo: str | Path) -> Path:
    return Path(repo) / AGENTS_DIR / TASKS_DIR


def _atomic_write(path: Path, text: str) -> None:
    """Write via temp file + rename so a crash never leaves half a state.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class TaskStore:
    """Read/write helpers for one task folder."""

    def __init__(self, repo: str | Path, task_id: str) -> None:
        self.repo = Path(repo).resolve()
        self.task_id = task_id
        self.dir = tasks_root(self.repo) / task_id

    # ---- paths -------------------------------------------------------
    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def spec_path(self) -> Path:
        return self.dir / "spec.md"

    @property
    def plan_path(self) -> Path:
        return self.dir / "plan.md"

    @property
    def events_path(self) -> Path:
        return self.dir / "events.jsonl"

    @property
    def contract_dir(self) -> Path:
        return self.dir / "contract"

    def lane_path(self, lane: str) -> Path:
        return self.dir / "lanes" / f"{lane}.md"

    def review_path(self, round_: int) -> Path:
        return self.dir / "reviews" / f"round-{round_}.json"

    def feedback_path(self, n: int) -> Path:
        return self.dir / "feedback" / f"{n}.md"

    def artifact_path(self, name: str) -> Path:
        return self.dir / "artifacts" / name

    # ---- state -------------------------------------------------------
    def exists(self) -> bool:
        return self.state_path.exists()

    def load(self) -> TaskState:
        return TaskState.model_validate_json(self.state_path.read_text())

    def save(self, state: TaskState) -> None:
        state.touch()
        _atomic_write(self.state_path, state.model_dump_json(indent=2))

    def init_dirs(self) -> None:
        for sub in ("reviews", "feedback", "artifacts", "lanes", "contract"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)

    # ---- documents ---------------------------------------------------
    def read_text(self, rel: str) -> str | None:
        p = self.dir / rel
        return p.read_text() if p.exists() else None

    def write_text(self, rel: str, text: str) -> Path:
        p = self.dir / rel
        _atomic_write(p, text)
        return p

    def save_verdict(self, verdict: Verdict) -> Path:
        p = self.review_path(verdict.round)
        _atomic_write(p, verdict.model_dump_json(indent=2))
        return p

    def load_verdict(self, round_: int) -> Verdict | None:
        p = self.review_path(round_)
        return Verdict.model_validate_json(p.read_text()) if p.exists() else None

    def next_feedback_index(self) -> int:
        d = self.dir / "feedback"
        if not d.exists():
            return 1
        nums = [int(p.stem) for p in d.glob("*.md") if p.stem.isdigit()]
        return max(nums, default=0) + 1

    # ---- events ------------------------------------------------------
    def append_event(self, event: Event) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a") as fh:
            fh.write(event.model_dump_json() + "\n")

    def read_events(self, limit: int | None = None) -> list[Event]:
        if not self.events_path.exists():
            return []
        lines = self.events_path.read_text().splitlines()
        if limit:
            lines = lines[-limit:]
        out: list[Event] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Event.model_validate_json(line))
            except Exception:
                continue  # never let one bad line kill the log view
        return out


def iter_tasks(repo: str | Path) -> Iterator[TaskState]:
    root = tasks_root(repo)
    if not root.exists():
        return
    for d in sorted(root.iterdir(), reverse=True):
        sp = d / "state.json"
        if not sp.exists():
            continue
        try:
            yield TaskState.model_validate_json(sp.read_text())
        except Exception:
            continue


def find_task(repos: list[Path], task_id: str) -> TaskStore | None:
    """Locate a task across the registered repos by id."""
    for repo in repos:
        store = TaskStore(repo, task_id)
        if store.exists():
            return store
    return None


def read_project_doc(repo: str | Path, rel: str) -> str | None:
    """Read a knowledge file such as `.agents/project.md`."""
    p = Path(repo) / AGENTS_DIR / rel
    return p.read_text() if p.exists() else None
