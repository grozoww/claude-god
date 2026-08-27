"""Git branches and worktrees.

Each lane implements in its own worktree (a second checkout of the same repo in
another folder) so parallel agents never touch the same files. The integrator
merges lane branches back into the task branch.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


class GitError(RuntimeError):
    pass


async def git(*args: str, cwd: Path, check: bool = True) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    text = out.decode(errors="replace").strip()
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed:\n{text}")
    return text


async def is_repo(path: Path) -> bool:
    try:
        await git("rev-parse", "--git-dir", cwd=path)
        return True
    except (GitError, FileNotFoundError, NotADirectoryError):
        return False


async def has_commits(repo: Path) -> bool:
    try:
        await git("rev-parse", "HEAD", cwd=repo)
        return True
    except GitError:
        return False


async def current_branch(repo: Path) -> str:
    return await git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo)


async def ensure_branch(repo: Path, branch: str, base: str | None = None) -> str:
    """Create `branch` off `base` if missing, then check it out in the main repo."""
    existing = await git("branch", "--list", branch, cwd=repo)
    if existing.strip():
        await git("checkout", branch, cwd=repo)
    else:
        args = ["checkout", "-b", branch]
        if base:
            args.append(base)
        await git(*args, cwd=repo)
    return branch


async def worktree_add(repo: Path, path: Path, branch: str, base: str) -> Path:
    """Create a worktree at `path` on a fresh `branch` cut from `base`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        await worktree_remove(repo, path)
    exists = (await git("branch", "--list", branch, cwd=repo)).strip()
    if exists:
        await git("worktree", "add", str(path), branch, cwd=repo)
    else:
        await git("worktree", "add", "-b", branch, str(path), base, cwd=repo)
    return path


async def worktree_remove(repo: Path, path: Path) -> None:
    try:
        await git("worktree", "remove", "--force", str(path), cwd=repo)
    except GitError:
        shutil.rmtree(path, ignore_errors=True)
        await git("worktree", "prune", cwd=repo, check=False)


async def commit_all(cwd: Path, message: str) -> bool:
    """Commit everything in `cwd`. Returns False when there was nothing to commit."""
    await git("add", "-A", cwd=cwd)
    status = await git("status", "--porcelain", cwd=cwd)
    if not status.strip():
        return False
    await git("-c", "user.name=claude-god", "-c", "user.email=god@local",
              "commit", "-m", message, cwd=cwd)
    return True


async def merge_branch(repo: Path, branch: str) -> tuple[bool, str]:
    """Merge `branch` into the current branch. Returns (ok, output)."""
    try:
        out = await git("merge", "--no-ff", "-m", f"merge lane {branch}", branch, cwd=repo)
        return True, out
    except GitError as exc:
        conflicts = await git("diff", "--name-only", "--diff-filter=U", cwd=repo, check=False)
        return False, f"{exc}\n\nconflicting files:\n{conflicts}"


async def diff_against(repo: Path, base: str, max_chars: int = 60000) -> str:
    """Unified diff of the current branch against `base`, for the critic."""
    try:
        text = await git("diff", f"{base}...HEAD", cwd=repo)
    except GitError:
        text = await git("diff", "HEAD", cwd=repo, check=False)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n[... diff truncated at {max_chars} chars ...]"
    return text


async def changed_files(repo: Path, base: str) -> list[str]:
    out = await git("diff", "--name-only", f"{base}...HEAD", cwd=repo, check=False)
    return [line for line in out.splitlines() if line.strip()]
