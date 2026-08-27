"""Directory listing for the dashboard's folder picker.

A browser cannot hand back a real filesystem path — `webkitdirectory` gives
file names only. Since the server runs on the same machine, it does the walking
and the UI just renders what it finds, labelled with what matters here: is this
a git repo, is it already registered, has it been onboarded.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from .config import AGENTS_DIR

SKIP = {
    "node_modules", "__pycache__", ".venv", "venv", ".git", "dist", "build",
    ".next", ".pytest_cache", "target", ".tox", ".mypy_cache", ".ruff_cache",
}


class Entry(BaseModel):
    name: str
    path: str
    is_git: bool = False
    is_registered: bool = False
    is_onboarded: bool = False
    readable: bool = True


class Listing(BaseModel):
    path: str
    parent: str | None
    entries: list[Entry]
    is_git: bool = False          # the listed directory itself
    is_registered: bool = False
    is_onboarded: bool = False


def _is_git(p: Path) -> bool:
    # A worktree's .git is a file, not a directory.
    return (p / ".git").exists()


def describe(p: Path, registered: set[str]) -> tuple[bool, bool, bool]:
    return _is_git(p), str(p) in registered, (p / AGENTS_DIR / "project.md").exists()


def roots(registered: list[Path]) -> list[Entry]:
    """Sensible places to start: home, common dev folders, registered repos' parents."""
    home = Path.home()
    candidates = [home, home / "dev", home / "Projects", home / "src", home / "code",
                  Path.cwd()]
    for r in registered:
        candidates.append(r.parent)

    seen: set[str] = set()
    out: list[Entry] = []
    reg = {str(r) for r in registered}
    for c in candidates:
        try:
            c = c.expanduser().resolve()
        except OSError:
            continue
        if str(c) in seen or not c.is_dir():
            continue
        seen.add(str(c))
        git, is_reg, onb = describe(c, reg)
        out.append(Entry(name=str(c).replace(str(home), "~", 1), path=str(c),
                         is_git=git, is_registered=is_reg, is_onboarded=onb))
    return out


def listdir(path: str | Path, registered: list[Path], show_hidden: bool = False) -> Listing:
    p = Path(path).expanduser()
    try:
        p = p.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise FileNotFoundError(f"no such directory: {path}") from exc
    if not p.is_dir():
        raise NotADirectoryError(f"not a directory: {p}")

    reg = {str(r) for r in registered}
    entries: list[Entry] = []
    try:
        children = sorted(p.iterdir(), key=lambda c: c.name.lower())
    except PermissionError as exc:
        raise PermissionError(f"cannot read {p}") from exc

    for c in children:
        if c.name in SKIP:
            continue
        if not show_hidden and c.name.startswith("."):
            continue
        try:
            if not c.is_dir():
                continue
        except OSError:
            continue
        git, is_reg, onb = describe(c, reg)
        readable = True
        try:
            next(c.iterdir(), None)
        except (PermissionError, OSError):
            readable = False
        entries.append(Entry(name=c.name, path=str(c), is_git=git,
                             is_registered=is_reg, is_onboarded=onb, readable=readable))

    git, is_reg, onb = describe(p, reg)
    return Listing(
        path=str(p),
        parent=str(p.parent) if p.parent != p else None,
        entries=entries,
        is_git=git, is_registered=is_reg, is_onboarded=onb,
    )
