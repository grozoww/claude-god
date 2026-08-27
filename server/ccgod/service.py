"""Task lifecycle operations shared by the CLI and the HTTP API."""

from __future__ import annotations

import re
from pathlib import Path

from . import retention
from .bus import BUS, EventBus
from .config import ServerConfig, load_project_config
from .models import (
    Event,
    Finding,
    Phase,
    Status,
    TaskState,
    Usage,
    new_task_id,
)
from .pipeline import Pipeline
from .prompts import onboard_prompt
from .runner import DEFAULT_TOOLS, PhaseRequest, make_runner
from .store import TaskStore, iter_tasks


class TaskNotFound(LookupError):
    pass


class Service:
    def __init__(self, cfg: ServerConfig | None = None, bus: EventBus | None = None) -> None:
        self.cfg = cfg or ServerConfig.load()
        self.bus = bus or BUS
        self.pipeline = Pipeline(self.cfg, self.bus)

    # ---- repos -------------------------------------------------------
    def repo_paths(self) -> list[Path]:
        return [Path(r.path).expanduser().resolve() for r in self.cfg.repos]

    def add_repo(self, path: str | Path, name: str | None = None) -> None:
        from .config import Repo

        p = Path(path).expanduser().resolve()
        nm = name or p.name
        self.cfg.repos = [r for r in self.cfg.repos if r.name != nm and r.path != str(p)]
        self.cfg.repos.append(Repo(name=nm, path=str(p)))
        self.cfg.save()

    def remove_repo(self, name_or_path: str) -> None:
        self.cfg.repos = [
            r for r in self.cfg.repos if r.name != name_or_path and r.path != name_or_path
        ]
        self.cfg.save()

    # ---- lookup ------------------------------------------------------
    def store(self, task_id: str) -> TaskStore:
        for repo in self.repo_paths():
            st = TaskStore(repo, task_id)
            if st.exists():
                return st
        raise TaskNotFound(task_id)

    def list_tasks(
        self,
        repo: str | Path | None = None,
        *,
        archived: bool | None = False,
    ) -> list[TaskState]:
        """`archived=False` hides archived tasks, `True` shows only those,
        `None` shows everything."""
        repos = [Path(repo).expanduser().resolve()] if repo else self.repo_paths()
        out: list[TaskState] = []
        for r in repos:
            out.extend(t for t in iter_tasks(r)
                       if archived is None or t.archived == archived)
        out.sort(key=lambda t: t.created_at, reverse=True)
        for t in out:
            if self.pipeline.is_running(t.id) and t.status is not Status.RUNNING:
                t.status = Status.RUNNING
        return out

    # ---- create ------------------------------------------------------
    def create(
        self,
        repo: str | Path,
        title: str,
        description: str = "",
        *,
        auto_approve_spec: bool = False,
        max_rounds: int | None = None,
        budget_usd: float | None = None,
    ) -> TaskStore:
        repo_path = self.cfg.resolve_repo(str(repo))
        pcfg = load_project_config(repo_path)
        state = TaskState(
            id=new_task_id(),
            title=title,
            description=description,
            repo=str(repo_path),
            max_rounds=max_rounds if max_rounds is not None else pcfg.max_rounds,
            budget_usd=budget_usd if budget_usd is not None else pcfg.budget_usd,
            auto_approve_spec=auto_approve_spec or pcfg.auto_approve_spec,
        )
        store = TaskStore(repo_path, state.id)
        store.init_dirs()
        store.save(state)
        self._ensure_gitignore(repo_path)
        self.pipeline.emit(store, "state", f"task created: {title}", phase=Phase.NEW)
        return store

    @staticmethod
    def _ensure_gitignore(repo: Path) -> None:
        """Keep task folders out of the user's git history."""
        gi = repo / ".gitignore"
        line = ".agents/tasks/"
        try:
            existing = gi.read_text() if gi.exists() else ""
            if line not in existing:
                sep = "" if existing.endswith("\n") or not existing else "\n"
                gi.write_text(f"{existing}{sep}{line}\n")
        except OSError:
            pass

    # ---- control -----------------------------------------------------
    def start(self, task_id: str) -> TaskState:
        store = self.store(task_id)
        self.pipeline.start(store)
        return store.load()

    async def run_sync(self, task_id: str) -> TaskState:
        store = self.store(task_id)
        return await self.pipeline.run(store)

    def approve_spec(self, task_id: str, *, start: bool = True) -> TaskState:
        store = self.store(task_id)
        state = store.load()
        if state.phase is not Phase.AWAITING_SPEC_APPROVAL:
            raise ValueError(f"task is in phase {state.phase.value}, not awaiting approval")
        # Re-read the spec: you may have edited it before approving.
        from .specparse import parse_criteria

        if store.spec_path.exists():
            state.criteria = parse_criteria(store.spec_path.read_text())
        state.phase, state.status = Phase.PLAN, Status.IDLE
        store.save(state)
        self.pipeline.emit(store, "state", "spec approved", phase=Phase.PLAN)
        if start:
            self.pipeline.start(store)
        return store.load()

    def redo(
        self, task_id: str, note: str, *, lane: str | None = None, start: bool = True
    ) -> TaskState:
        """Human feedback enters the same loop as a critic finding."""
        store = self.store(task_id)
        state = store.load()
        n = store.next_feedback_index()
        store.write_text(f"feedback/{n}.md", note.rstrip() + "\n")

        finding = Finding(
            problem=note.strip(),
            required_change=note.strip(),
            lane=lane,
            source="human",
        )
        if state.phase is Phase.AWAITING_SPEC_APPROVAL:
            # Redo before approval means: rewrite the spec.
            state.phase, state.status = Phase.SPEC, Status.IDLE
            store.save(state)
            self.pipeline.emit(store, "state", "spec sent back for rework", phase=Phase.SPEC)
            if start:
                self.pipeline.start(store)
            return store.load()

        state.pending_findings = [*state.pending_findings, finding]
        if state.phase in {Phase.DONE, Phase.ESCALATED, Phase.FAILED}:
            # Reopening: give the loop fresh rounds to work with.
            state.max_rounds = max(state.max_rounds, state.round + 2)
            state.phase, state.status = Phase.REVISE, Status.IDLE
        elif state.phase is not Phase.REVISE:
            state.status = Status.IDLE
        store.save(state)
        self.pipeline.emit(store, "log", f"human feedback #{n}", level="warn", note=note)
        if start:
            self.pipeline.start(store)
        return store.load()

    async def cancel(self, task_id: str) -> TaskState:
        return await self.pipeline.cancel(self.store(task_id))

    async def cleanup(self, task_id: str) -> TaskState:
        store = self.store(task_id)
        await self.pipeline.cleanup_worktrees(store)
        return store.load()

    # ---- onboarding --------------------------------------------------
    async def onboard(self, repo: str | Path, hint: str = "") -> str:
        """`ccgod init` — draft `.agents/project.md`, lane docs and config.json."""
        repo_path = self.cfg.resolve_repo(str(repo))
        (repo_path / ".agents" / "lanes").mkdir(parents=True, exist_ok=True)
        pcfg = load_project_config(repo_path)
        runner = make_runner(self.cfg.runner, self.cfg.claude_bin)
        ev = Event(task_id=f"onboard:{repo_path.name}", kind="phase_start",
                   message=f"onboarding {repo_path}")
        self.bus.publish(ev)
        res = await runner.run(
            PhaseRequest(
                prompt=onboard_prompt(repo_path, hint),
                cwd=repo_path,
                phase="onboard",
                task_id=f"onboard:{repo_path.name}",
                model=pcfg.models.get("onboard"),
                allowed_tools=list(DEFAULT_TOOLS),
                timeout_s=self.cfg.phase_timeout_s,
            )
        )
        self.bus.publish(
            Event(task_id=f"onboard:{repo_path.name}", kind="phase_end",
                  message="onboarding finished" if res.ok else f"onboarding failed: {res.error}",
                  level="info" if res.ok else "error",
                  data={"usage": res.usage.model_dump()})
        )
        if not res.ok:
            raise RuntimeError(res.error or "onboarding failed")
        self._ensure_gitignore(repo_path)
        return res.text

    # ---- removal -----------------------------------------------------
    async def delete_task(self, task_id: str, *, branch: bool = False) -> dict:
        """Delete a task folder. Its git branch is kept unless asked otherwise."""
        import shutil

        store = self.store(task_id)
        state = store.load()
        if self.pipeline.is_running(task_id):
            await self.pipeline.cancel(store)
        await self.pipeline.cleanup_worktrees(store)

        removed_branch = None
        if branch and state.branch:
            from . import worktree as wt

            for name in [state.branch, *[ln.branch for ln in state.lanes.values() if ln.branch]]:
                try:
                    await wt.git("branch", "-D", name, cwd=store.repo)
                    removed_branch = state.branch
                except wt.GitError:
                    pass  # already gone, or checked out somewhere

        shutil.rmtree(store.dir, ignore_errors=True)
        self.bus.publish(Event(
            task_id=task_id, kind="state", message="task deleted", level="warn",
            data={"deleted": True},
        ))
        return {"id": task_id, "deleted": True, "branch_deleted": removed_branch}

    def set_archived(self, task_id: str, archived: bool = True) -> TaskState:
        """Hide a task from the default list without deleting it."""
        store = self.store(task_id)
        state = store.load()
        state.archived = archived
        store.save(state)
        self.pipeline.emit(
            store, "state", "archived" if archived else "unarchived", archived=archived
        )
        return state

    def prune_plan(self, *, include_unfinished: bool = False) -> list[retention.Pruned]:
        out: list[retention.Pruned] = []
        for repo in self.repo_paths():
            out.extend(retention.plan(
                repo, self.cfg.retention,
                is_running=self.pipeline.is_running,
                include_unfinished=include_unfinished,
            ))
        return out

    async def prune(self, *, include_unfinished: bool = False) -> list[retention.Pruned]:
        """Apply the retention policy across every registered repo."""
        out: list[retention.Pruned] = []
        for repo in self.repo_paths():
            out.extend(await retention.apply(
                repo, self.cfg.retention,
                is_running=self.pipeline.is_running,
                include_unfinished=include_unfinished,
                remove_worktrees=self.pipeline.cleanup_worktrees,
            ))
        if out:
            self.bus.publish(Event(
                task_id="maintenance", kind="log",
                message=f"pruned {len(out)} task(s)",
                data={"pruned": [p.__dict__ for p in out]},
            ))
        return out

    # ---- clone -------------------------------------------------------
    async def clone(self, url: str, into: str | Path | None = None,
                    name: str | None = None) -> dict:
        """git clone a repo, then register it. Does not run onboarding."""
        from . import worktree as wt

        url = url.strip()
        if not url or url.startswith("-"):
            raise ValueError("a git URL is required")

        derived = re.sub(r"[/:]+$", "", url).rsplit("/", 1)[-1].removesuffix(".git")
        if not derived:
            raise ValueError(f"could not work out a directory name from {url!r}")

        if into:
            target = Path(into).expanduser().resolve()
            # An existing directory means "clone into here", a new one is the repo.
            if target.is_dir():
                target = target / derived
        else:
            target = Path(self.cfg.clone_root).expanduser().resolve() / derived

        if target.exists():
            raise FileExistsError(f"{target} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)

        self.bus.publish(Event(task_id="clone", kind="phase_start",
                               message=f"cloning {url} -> {target}"))
        try:
            await wt.git("clone", "--", url, str(target), cwd=target.parent)
        except wt.GitError as exc:
            self.bus.publish(Event(task_id="clone", kind="error", level="error",
                                   message=str(exc)[-500:]))
            raise

        self.add_repo(target, name)
        onboarded = (target / ".agents" / "project.md").exists()
        self.bus.publish(Event(task_id="clone", kind="phase_end",
                               message=f"cloned into {target}"))
        return {
            "path": str(target),
            "name": name or target.name,
            "onboarded": onboarded,
            "next": None if onboarded else f"ccgod init {target}",
        }

    # ---- rollups -----------------------------------------------------
    def usage_summary(self) -> dict:
        tasks = self.list_tasks()
        total = Usage()
        for t in tasks:
            total = total.add(t.usage)
        return {
            "tasks": len(tasks),
            "total": total.model_dump(),
            "by_task": [
                {"id": t.id, "title": t.title, "usage": t.usage.model_dump()} for t in tasks
            ],
        }

    async def resume_parked(self) -> int:
        """Restart tasks whose rate-limit park has expired. Called on a timer."""
        from .models import utcnow

        n = 0
        for t in self.list_tasks():
            if t.status is Status.BLOCKED and t.parked_until and t.parked_until <= utcnow():
                if t.phase in {Phase.AWAITING_SPEC_APPROVAL, Phase.ESCALATED}:
                    continue
                store = self.store(t.id)
                st = store.load()
                st.parked_until, st.status, st.error = None, Status.IDLE, None
                store.save(st)
                self.pipeline.start(store)
                n += 1
        return n
