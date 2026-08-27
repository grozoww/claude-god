"""HTTP API + SSE + static dashboard.

One process: it owns the queue, spawns `claude -p`, and serves the UI.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .bus import BUS
from .config import ServerConfig, load_project_config, save_project_config
from .models import Phase, Status
from .service import Service, TaskNotFound
from .store import TaskStore

STATIC_DIR = Path(__file__).parent / "static"


class CreateTaskBody(BaseModel):
    repo: str
    title: str
    description: str = ""
    auto_approve_spec: bool = False
    max_rounds: int | None = None
    budget_usd: float | None = None
    start: bool = True


class RedoBody(BaseModel):
    note: str
    lane: str | None = None


class AddRepoBody(BaseModel):
    path: str
    name: str | None = None


class OnboardBody(BaseModel):
    repo: str
    hint: str = ""


def create_app(cfg: ServerConfig | None = None) -> FastAPI:
    service = Service(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def park_watcher():
            while True:
                try:
                    await service.resume_parked()
                except Exception:  # noqa: BLE001
                    pass
                await asyncio.sleep(60)

        watcher = asyncio.create_task(park_watcher())
        try:
            yield
        finally:
            watcher.cancel()

    app = FastAPI(title="claude-god", version="0.1.0", lifespan=lifespan)
    app.state.service = service

    # ---- meta --------------------------------------------------------
    @app.get("/api/health")
    async def health():
        from .runner import CliRunner

        cli = CliRunner(service.cfg.claude_bin)
        return {
            "ok": True,
            "claude_available": cli.available(),
            "runner": service.cfg.runner,
            "max_parallel": service.cfg.max_parallel,
            "repos": len(service.cfg.repos),
        }

    @app.get("/api/config")
    async def get_config():
        return service.cfg.model_dump()

    # ---- repos -------------------------------------------------------
    @app.get("/api/repos")
    async def list_repos():
        out = []
        for r in service.cfg.repos:
            p = Path(r.path)
            pcfg = load_project_config(p)
            out.append({
                "name": r.name,
                "path": r.path,
                "exists": p.exists(),
                "onboarded": (p / ".agents" / "project.md").exists(),
                "lanes": pcfg.lane_names,
                "gates": pcfg.gates,
            })
        return out

    @app.post("/api/repos")
    async def add_repo(body: AddRepoBody):
        p = Path(body.path).expanduser().resolve()
        if not p.exists():
            raise HTTPException(400, f"path does not exist: {p}")
        service.add_repo(p, body.name)
        return {"ok": True}

    @app.delete("/api/repos/{name}")
    async def remove_repo(name: str):
        service.remove_repo(name)
        return {"ok": True}

    @app.get("/api/repos/{name}/config")
    async def repo_config(name: str):
        return load_project_config(service.cfg.resolve_repo(name)).model_dump()

    @app.put("/api/repos/{name}/config")
    async def put_repo_config(name: str, body: dict = Body(...)):
        from .config import ProjectConfig

        repo = service.cfg.resolve_repo(name)
        cfg = ProjectConfig.model_validate(body)
        save_project_config(repo, cfg)
        return cfg.model_dump()

    @app.post("/api/onboard")
    async def onboard(body: OnboardBody):
        try:
            text = await service.onboard(body.repo, body.hint)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, str(exc)) from exc
        return {"ok": True, "summary": text}

    # ---- tasks -------------------------------------------------------
    def _store(task_id: str) -> TaskStore:
        try:
            return service.store(task_id)
        except TaskNotFound as exc:
            raise HTTPException(404, f"no such task: {task_id}") from exc

    @app.get("/api/tasks")
    async def list_tasks(repo: str | None = None):
        return [t.model_dump(mode="json") for t in service.list_tasks(repo)]

    @app.post("/api/tasks")
    async def create_task(body: CreateTaskBody):
        try:
            store = service.create(
                body.repo, body.title, body.description,
                auto_approve_spec=body.auto_approve_spec,
                max_rounds=body.max_rounds,
                budget_usd=body.budget_usd,
            )
        except FileNotFoundError as exc:
            raise HTTPException(400, str(exc)) from exc
        if body.start:
            service.pipeline.start(store)
        return store.load().model_dump(mode="json")

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        store = _store(task_id)
        state = store.load()
        data = state.model_dump(mode="json")
        data["running"] = service.pipeline.is_running(task_id)
        data["spec"] = store.read_text("spec.md")
        data["plan"] = store.read_text("plan.md")
        data["reviews"] = [
            json.loads(p.read_text())
            for p in sorted((store.dir / "reviews").glob("round-*.json"))
        ]
        data["feedback"] = [
            {"n": p.stem, "text": p.read_text()}
            for p in sorted((store.dir / "feedback").glob("*.md"))
        ]
        data["lane_docs"] = {
            p.stem: p.read_text() for p in sorted((store.dir / "lanes").glob("*.md"))
        }
        return data

    @app.get("/api/tasks/{task_id}/events")
    async def task_events(task_id: str, limit: int = 500):
        return [e.model_dump(mode="json") for e in _store(task_id).read_events(limit)]

    @app.get("/api/tasks/{task_id}/file")
    async def task_file(task_id: str, path: str = Query(...)):
        store = _store(task_id)
        target = (store.dir / path).resolve()
        if not str(target).startswith(str(store.dir.resolve())):
            raise HTTPException(400, "path escapes the task folder")
        if not target.exists():
            raise HTTPException(404, "no such file")
        return {"path": path, "text": target.read_text()}

    @app.get("/api/tasks/{task_id}/diff")
    async def task_diff(task_id: str):
        from . import worktree as wt

        store = _store(task_id)
        state = store.load()
        if not (state.branch and state.base_branch):
            return {"diff": ""}
        try:
            cur = await wt.current_branch(store.repo)
            if cur != state.branch:
                return {
                    "diff": "",
                    "note": f"repo is on '{cur}', task branch is '{state.branch}'",
                }
            return {"diff": await wt.diff_against(store.repo, state.base_branch)}
        except Exception as exc:  # noqa: BLE001
            return {"diff": "", "note": str(exc)}

    @app.post("/api/tasks/{task_id}/start")
    async def start_task(task_id: str):
        _store(task_id)  # 404 rather than a 500 for an unknown id
        return service.start(task_id).model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/approve")
    async def approve_task(task_id: str):
        _store(task_id)
        try:
            return service.approve_spec(task_id).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/tasks/{task_id}/redo")
    async def redo_task(task_id: str, body: RedoBody):
        _store(task_id)
        if not body.note.strip():
            raise HTTPException(400, "note is required")
        return service.redo(task_id, body.note, lane=body.lane).model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str):
        _store(task_id)
        return (await service.cancel(task_id)).model_dump(mode="json")

    @app.post("/api/tasks/{task_id}/cleanup")
    async def cleanup_task(task_id: str):
        _store(task_id)
        return (await service.cleanup(task_id)).model_dump(mode="json")

    @app.put("/api/tasks/{task_id}/spec")
    async def put_spec(task_id: str, body: dict = Body(...)):
        """Edit the spec before approving it — the criteria are re-parsed."""
        from .specparse import parse_criteria

        store = _store(task_id)
        state = store.load()
        if state.phase not in {Phase.AWAITING_SPEC_APPROVAL, Phase.SPEC}:
            raise HTTPException(400, "spec can only be edited before approval")
        store.write_text("spec.md", body.get("text", ""))
        state.criteria = parse_criteria(body.get("text", ""))
        store.save(state)
        return state.model_dump(mode="json")

    @app.get("/api/usage")
    async def usage():
        return service.usage_summary()

    # ---- live stream -------------------------------------------------
    @app.get("/api/stream")
    async def stream(task_id: str | None = None):
        async def gen():
            yield ": connected\n\n"
            try:
                async for ev in BUS.subscribe(task_id):
                    yield f"data: {ev.model_dump_json()}\n\n"
            except asyncio.CancelledError:
                return

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- static UI ---------------------------------------------------
    if (STATIC_DIR / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            candidate = (STATIC_DIR / full_path).resolve()
            if full_path and candidate.is_file() and str(candidate).startswith(str(STATIC_DIR)):
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")
    else:
        @app.get("/")
        async def no_ui():
            return {
                "ok": True,
                "note": "UI not built. Run `pnpm install && pnpm build` in ui/.",
                "api": "/api/health",
            }

    return app


app = create_app()
