"""`ccgod` — command line for the orchestrator."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import daemon
from .bus import BUS
from .config import ServerConfig, ccgod_home, load_project_config
from .models import CritStatus, Phase
from .service import Service, TaskNotFound

console = Console()
app = typer.Typer(help="claude-god — run dev tasks end to end with multiple agents.",
                  no_args_is_help=True)
repos_app = typer.Typer(help="Manage the repo registry.", no_args_is_help=True)
app.add_typer(repos_app, name="repos")

PHASE_STYLE = {
    Phase.DONE: "bold green",
    Phase.FAILED: "bold red",
    Phase.ESCALATED: "bold yellow",
    Phase.AWAITING_SPEC_APPROVAL: "bold cyan",
    Phase.CANCELLED: "dim",
}


def _svc() -> Service:
    return Service()


def _resolve_repo_arg(svc: Service, repo: str) -> Path:
    """Accept a registered name or a path. Registering a path is implicit.

    A bare name must hit the registry — never be resolved against the cwd, which
    would silently point a task at a directory that does not exist.
    """
    path = Path(repo).expanduser()
    if path.is_dir():
        resolved = path.resolve()
        svc.add_repo(resolved)
        return resolved
    return svc.cfg.resolve_repo(repo)


def _fmt_phase(state) -> str:
    style = PHASE_STYLE.get(state.phase, "white")
    return f"[{style}]{state.phase.value}[/]"


async def _watch(task_id: str, service: Service) -> None:
    """Print events until the task stops moving."""
    async def printer():
        async for ev in BUS.subscribe(task_id, replay=False):
            colour = {"warn": "yellow", "error": "red"}.get(ev.level, "cyan")
            lane = f"[{ev.lane}] " if ev.lane else ""
            console.print(f"[dim]{ev.ts:%H:%M:%S}[/] [{colour}]{ev.kind:11}[/] {lane}{ev.message}")

    pr = asyncio.create_task(printer())
    try:
        state = await service.run_sync(task_id)
    finally:
        await asyncio.sleep(0.1)
        pr.cancel()
    console.print()
    console.print(Panel.fit(_summary(state), title=f"task {task_id}"))


def _summary(state) -> str:
    lines = [
        f"phase   : {state.phase.value}",
        f"status  : {state.status.value}",
        f"round   : {state.round}/{state.max_rounds}",
        f"branch  : {state.branch or '-'}",
        f"usage   : {state.usage.input_tokens}in / {state.usage.output_tokens}out"
        f" / ${state.usage.cost_usd:.3f} / {state.usage.turns} turns",
    ]
    if state.error:
        lines.append(f"error   : {state.error[:300]}")
    if state.criteria:
        lines.append("")
        for c in state.criteria:
            mark = {CritStatus.PASS: "[green]✔[/]", CritStatus.FAIL: "[red]✘[/]"}.get(
                c.status, "[dim]·[/]"
            )
            lines.append(f"{mark} {c.id}: {c.text[:80]}")
    return "\n".join(lines)


# ---- repos -----------------------------------------------------------
@repos_app.command("add")
def repos_add(path: str, name: str = typer.Option(None, "--name", "-n")):
    """Register a repo so tasks and the dashboard can see it."""
    svc = _svc()
    svc.add_repo(path, name)
    console.print(f"[green]added[/] {Path(path).expanduser().resolve()}")


@repos_app.command("list")
def repos_list():
    """List registered repos."""
    svc = _svc()
    t = Table("name", "path", "onboarded", "lanes")
    for r in svc.cfg.repos:
        p = Path(r.path)
        cfg = load_project_config(p)
        t.add_row(
            r.name, r.path,
            "yes" if (p / ".agents" / "project.md").exists() else "[yellow]no[/]",
            ", ".join(cfg.lane_names) or "-",
        )
    console.print(t if svc.cfg.repos else "[dim]no repos registered — `ccgod repos add <path>`[/]")


@repos_app.command("rm")
def repos_rm(name: str):
    """Unregister a repo."""
    _svc().remove_repo(name)
    console.print(f"[green]removed[/] {name}")


# ---- lifecycle -------------------------------------------------------
@app.command()
def init(
    repo: str = typer.Argument(".", help="Repo path or registered name"),
    hint: str = typer.Option("", "--hint", help="Extra guidance for the onboarding agent"),
):
    """Draft .agents/project.md, lane docs and config.json for a repo."""
    svc = _svc()
    try:
        path = _resolve_repo_arg(svc, repo)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/] — try `ccgod repos list`")
        raise typer.Exit(1) from exc
    console.print(f"[cyan]onboarding[/] {path} — this runs one Claude session…")
    text = asyncio.run(svc.onboard(str(path), hint))
    console.print(Panel(text or "(no summary)", title="onboarding"))
    console.print(f"[green]wrote[/] {path / '.agents'}/ — review it, then `ccgod new`")


@app.command()
def new(
    title: str = typer.Argument(..., help="What to build"),
    repo: str = typer.Option(".", "--repo", "-r"),
    desc: str = typer.Option("", "--desc", "-d", help="Longer description"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the spec approval gate"),
    rounds: int = typer.Option(None, "--rounds", help="Override max revision rounds"),
    budget: float = typer.Option(None, "--budget", help="Stop above this USD cost"),
    watch: bool = typer.Option(True, "--watch/--no-watch", help="Run now and stream events"),
):
    """Create a task and run it."""
    svc = _svc()
    try:
        repo_path = _resolve_repo_arg(svc, repo)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/] — try `ccgod repos list`")
        raise typer.Exit(1) from exc
    store = svc.create(
        str(repo_path), title, desc,
        auto_approve_spec=yes, max_rounds=rounds, budget_usd=budget,
    )
    console.print(f"[green]created[/] {store.task_id}  [dim]{store.dir}[/]")
    if watch:
        asyncio.run(_watch(store.task_id, svc))
    else:
        console.print("run it with: [bold]ccgod run " + store.task_id + "[/]")


@app.command("list")
def list_tasks(
    repo: str = typer.Option(None, "--repo", "-r"),
    archived: bool = typer.Option(False, "--archived", help="Only archived tasks"),
    all_: bool = typer.Option(False, "--all", "-a", help="Archived ones too"),
):
    """List tasks across registered repos."""
    svc = _svc()
    tasks = svc.list_tasks(repo, archived=None if all_ else archived)
    if not tasks:
        console.print("[dim]no tasks[/]")
        return
    t = Table("id", "phase", "rnd", "title", "repo", "cost")
    for s in tasks:
        t.add_row(
            s.id, _fmt_phase(s), f"{s.round}/{s.max_rounds}",
            s.title[:48], Path(s.repo).name, f"${s.usage.cost_usd:.2f}",
        )
    console.print(t)


@app.command()
def show(task_id: str, events: int = typer.Option(0, "--events", "-e", help="Tail N events")):
    """Show one task."""
    svc = _svc()
    try:
        store = svc.store(task_id)
    except TaskNotFound:
        console.print(f"[red]no such task:[/] {task_id}")
        raise typer.Exit(1) from None
    state = store.load()
    console.print(Panel(_summary(state), title=f"{state.id} — {state.title}"))
    if state.verdict:
        console.print(Panel(state.verdict.summary or "-",
                            title=f"verdict round {state.verdict.round}: {state.verdict.verdict}"))
        for f in state.verdict.findings:
            loc = f" {f.file}:{f.line}" if f.file else ""
            console.print(f"  [yellow]{f.criterion or '-'}[/]{loc} — {f.problem[:200]}")
    if events:
        for ev in store.read_events(events):
            console.print(f"[dim]{ev.ts:%H:%M:%S}[/] {ev.kind:11} {ev.message}")
    console.print(f"[dim]{store.dir}[/]")


@app.command()
def run(task_id: str):
    """Advance a task until it finishes or needs you."""
    svc = _svc()
    asyncio.run(_watch(task_id, svc))


@app.command()
def approve(task_id: str):
    """Approve the spec and continue."""
    svc = _svc()
    try:
        svc.approve_spec(task_id, start=False)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    console.print("[green]approved[/] — continuing")
    asyncio.run(_watch(task_id, svc))


@app.command()
def redo(
    task_id: str,
    note: str = typer.Argument(..., help="What is wrong / what to change"),
    lane: str = typer.Option(None, "--lane", "-l"),
):
    """Send work back with a note. Enters the loop as a finding."""
    svc = _svc()
    svc.redo(task_id, note, lane=lane, start=False)
    console.print("[green]sent back[/] — continuing")
    asyncio.run(_watch(task_id, svc))


@app.command()
def cancel(task_id: str):
    """Stop a task."""
    asyncio.run(_svc().cancel(task_id))
    console.print("[yellow]cancelled[/]")


@app.command()
def cleanup(task_id: str):
    """Remove the lane worktrees for a task."""
    asyncio.run(_svc().cleanup(task_id))
    console.print("[green]worktrees removed[/]")


@app.command("rm")
def remove(
    task_id: str,
    branch: bool = typer.Option(False, "--branch", help="Delete its git branch too"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Delete a task: its folder, its worktrees, optionally its branch."""
    svc = _svc()
    try:
        state = svc.store(task_id).load()
    except TaskNotFound:
        console.print(f"[red]no such task:[/] {task_id}")
        raise typer.Exit(1) from None
    if not yes:
        console.print(f"Delete [bold]{state.title}[/] ({state.phase.value})?")
        if branch and state.branch:
            console.print(f"  and the branch [bold]{state.branch}[/]")
        if not typer.confirm("Continue?"):
            console.print("Cancelled.")
            raise typer.Exit(0)
    res = asyncio.run(svc.delete_task(task_id, branch=branch))
    console.print(f"[green]deleted[/] {task_id}"
                  + (f" and branch {res['branch_deleted']}" if res.get("branch_deleted") else ""))


@app.command()
def archive(task_id: str):
    """Hide a task from the default list. It stays on disk."""
    _svc().set_archived(task_id, True)
    console.print(f"[green]archived[/] {task_id}  [dim]see it with `ccgod list --all`[/]")


@app.command()
def unarchive(task_id: str):
    """Bring an archived task back into the list."""
    _svc().set_archived(task_id, False)
    console.print(f"[green]unarchived[/] {task_id}")


@app.command()
def prune(
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show, do not delete"),
    unfinished: bool = typer.Option(False, "--unfinished",
                                    help="Include tasks that never reached a terminal phase"),
    yes: bool = typer.Option(False, "--yes", "-y"),
):
    """Apply the retention policy: keep the newest N, drop anything too old."""
    svc = _svc()
    pol = svc.cfg.retention
    console.print(
        f"[dim]policy:[/] keep newest {pol.max_tasks} per repo, "
        f"drop older than {pol.max_age_days} days"
        + ("" if pol.enabled else "  [yellow](disabled)[/]")
    )
    doomed = svc.prune_plan(include_unfinished=unfinished)
    if not doomed:
        console.print("[green]nothing to prune[/]")
        return
    t = Table("task", "reason", "age", "title")
    for d in doomed:
        t.add_row(d.id, d.reason, f"{d.age_days}d", d.title[:44])
    console.print(t)
    if dry_run:
        console.print(f"[dim]{len(doomed)} would be removed — run without --dry-run[/]")
        return
    if not yes and not typer.confirm(f"Delete {len(doomed)} task(s)?"):
        console.print("Cancelled.")
        raise typer.Exit(0)
    removed = asyncio.run(svc.prune(include_unfinished=unfinished))
    console.print(f"[green]pruned[/] {len(removed)} task(s)")


@app.command()
def clone(
    url: str,
    into: str = typer.Option(None, "--into", "-i", help="Where to put it"),
    name: str = typer.Option(None, "--name", "-n", help="Registry name"),
    init_after: bool = typer.Option(False, "--init", help="Run onboarding after cloning"),
):
    """Clone a git repo and register it."""
    svc = _svc()
    console.print(f"[cyan]cloning[/] {url}")
    try:
        res = asyncio.run(svc.clone(url, into, name))
    except Exception as exc:
        console.print(f"[red]clone failed:[/] {exc}")
        raise typer.Exit(1) from exc
    console.print(f"[green]cloned[/] {res['path']}  registered as [bold]{res['name']}[/]")
    if init_after:
        console.print("[cyan]onboarding[/] — this runs one Claude session…")
        text = asyncio.run(svc.onboard(res["path"]))
        console.print(Panel(text or "(no summary)", title="onboarding"))
    elif res["next"]:
        console.print(f"next: [bold]{res['next']}[/]")


@app.command()
def spec(task_id: str):
    """Print the spec."""
    svc = _svc()
    text = svc.store(task_id).read_text("spec.md")
    console.print(text or "[dim]no spec yet[/]")


@app.command()
def usage():
    """Token and cost rollup."""
    data = _svc().usage_summary()
    tot = data["total"]
    console.print(Panel(
        f"tasks: {data['tasks']}\n"
        f"in: {tot['input_tokens']:,}  out: {tot['output_tokens']:,}\n"
        f"cache read: {tot['cache_read_tokens']:,}\n"
        f"cost: ${tot['cost_usd']:.2f}  turns: {tot['turns']}",
        title="usage",
    ))
    t = Table("task", "title", "cost", "in", "out")
    for row in data["by_task"][:25]:
        u = row["usage"]
        t.add_row(row["id"], row["title"][:40], f"${u['cost_usd']:.2f}",
                  f"{u['input_tokens']:,}", f"{u['output_tokens']:,}")
    console.print(t)


@app.command()
def doctor():
    """Check the environment."""
    cfg = ServerConfig.load()
    claude = shutil.which(cfg.claude_bin)
    git = shutil.which("git")
    t = Table("check", "result")
    t.add_row("claude binary", f"[green]{claude}[/]" if claude else "[red]NOT FOUND[/]")
    t.add_row("git", f"[green]{git}[/]" if git else "[red]NOT FOUND[/]")
    t.add_row("runner", cfg.runner)
    t.add_row("max_parallel", str(cfg.max_parallel))
    t.add_row("repos", str(len(cfg.repos)))
    from .api import STATIC_DIR
    t.add_row("dashboard built",
              "[green]yes[/]" if (STATIC_DIR / "index.html").exists()
              else "[yellow]no — build ui/[/]")
    from .prompts import plugin_dir
    t.add_row("plugin dir",
              f"[green]{plugin_dir()}[/]" if (plugin_dir() / "skills").exists()
              else f"[red]{plugin_dir()} missing[/]")
    console.print(t)


@app.command()
def demo(
    path: str = typer.Option("~/.ccgod/demo/toy-store", "--path", help="Where to put it"),
):
    """Copy the bundled toy repo somewhere, git-init it, and register it.

    Gives you a safe sandbox to watch a full run before pointing this at real code.
    """
    import shutil as _shutil
    import subprocess

    src = Path(__file__).resolve().parents[2] / "examples" / "toy"
    if not src.exists():
        console.print(f"[red]bundled example not found at[/] {src}")
        raise typer.Exit(1)
    dst = Path(path).expanduser().resolve()
    if dst.exists():
        console.print(f"[yellow]already exists:[/] {dst}")
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        _shutil.copytree(src, dst)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=dst, check=True)
        subprocess.run(["git", "add", "-A"], cwd=dst, check=True)
        subprocess.run(
            ["git", "-c", "user.name=claude-god", "-c", "user.email=god@local",
             "commit", "-qm", "toy store"],
            cwd=dst, check=True,
        )
        console.print(f"[green]created[/] {dst}")
    _svc().add_repo(dst, "toy-store")
    console.print("registered as [bold]toy-store[/]")
    console.print('try: [bold]ccgod new "reject orders with more than 50 lines" -r toy-store[/]')


@app.command()
def serve(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port", "-p"),
    reload: bool = typer.Option(False, "--reload", help="Restart on code changes"),
    foreground: bool = typer.Option(
        False, "--foreground", "-f",
        help="Stay attached and log to this terminal (ctrl-c stops it)",
    ),
):
    """Start the API + dashboard. Detaches by default; stop it with `ccgod stop`."""
    import uvicorn

    cfg = ServerConfig.load()
    h, p = host or cfg.host, port or cfg.port

    running, state = daemon.status()
    if running and state == "running":
        console.print(f"[yellow]already serving[/] {running.url} (pid {running.pid})")
        console.print("stop it first: [bold]ccgod stop[/]")
        raise typer.Exit(1)

    # uvicorn's reloader has to own the process, so it implies staying attached.
    if reload and not foreground:
        foreground = True
        console.print("[dim]--reload implies --foreground[/]")

    if not foreground:
        srv = daemon.spawn(h, p)
        console.print(f"[green]serving[/] {srv.url} (pid {srv.pid})")
        console.print(f"[dim]logs: {ccgod_home() / 'serve.log'}   stop: ccgod stop[/]")
        return

    console.print(f"[green]claude-god[/] http://{h}:{p}   [dim]ctrl-c to stop[/]")
    daemon.write(h, p)
    try:
        uvicorn.run("ccgod.api:app", host=h, port=p, reload=reload, log_level="info")
    finally:
        daemon.clear()


@app.command()
def stop():
    """Stop the dashboard."""
    ok, msg = daemon.stop()
    console.print(f"[green]{msg}[/]" if ok else f"[yellow]{msg}[/]")


@app.command()
def restart(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port", "-p"),
):
    """Stop and start again in the background. Picks up code changes."""
    cfg = ServerConfig.load()
    prev, _ = daemon.status()
    _, msg = daemon.stop()
    console.print(f"[dim]{msg}[/]")
    h = host or (prev.host if prev else cfg.host)
    p = port or (prev.port if prev else cfg.port)
    srv = daemon.spawn(h, p)
    console.print(f"[green]serving[/] {srv.url} (pid {srv.pid})")


@app.command()
def status():
    """Is the dashboard running?"""
    srv, state = daemon.status()
    if state == "running" and srv:
        up = int(time.time() - srv.started_at)
        console.print(f"[green]running[/] {srv.url}  pid {srv.pid}  up {up // 60}m {up % 60}s")
    elif state == "stale" and srv:
        console.print(f"[yellow]not responding[/] — pidfile says {srv.url} (pid {srv.pid})")
        console.print("clear it with: [bold]ccgod stop[/]")
    else:
        console.print("[dim]not running[/]  start with: [bold]ccgod serve[/]")


if __name__ == "__main__":
    app()
