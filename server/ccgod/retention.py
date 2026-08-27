"""Housekeeping for finished tasks.

Two rules, applied per repo, whichever bites first:
  * keep only the newest `max_tasks`
  * drop anything older than `max_age_days`

A task that is currently running is never pruned, however old it is. Archiving
hides a task from the default list without deleting it; retention still applies
to archived tasks, which is the point of archiving rather than keeping forever.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from .config import Retention
from .models import Phase, TaskState, utcnow
from .store import TaskStore, iter_tasks

#: Phases that are safe to remove without losing work in progress.
PRUNABLE = {Phase.DONE, Phase.FAILED, Phase.CANCELLED, Phase.ESCALATED}


@dataclass
class Pruned:
    id: str
    repo: str
    title: str
    reason: str            # "age" | "count"
    age_days: float


def _age_days(task: TaskState) -> float:
    return (utcnow() - task.updated_at).total_seconds() / 86400


def plan(
    repo: str | Path,
    policy: Retention,
    *,
    is_running: Callable[[str], bool] | None = None,
    include_unfinished: bool = False,
) -> list[Pruned]:
    """Decide what would be removed from one repo. Pure — deletes nothing."""
    if not policy.enabled:
        return []

    tasks = sorted(iter_tasks(repo), key=lambda t: t.updated_at, reverse=True)
    out: list[Pruned] = []
    kept = 0
    for task in tasks:
        if is_running and is_running(task.id):
            kept += 1
            continue
        if not include_unfinished and task.phase not in PRUNABLE:
            kept += 1          # still occupies a slot against max_tasks
            continue

        age = _age_days(task)
        if policy.max_age_days and age > policy.max_age_days:
            reason = "age"
        elif policy.max_tasks and kept >= policy.max_tasks:
            reason = "count"
        else:
            kept += 1
            continue
        out.append(Pruned(id=task.id, repo=str(repo), title=task.title,
                          reason=reason, age_days=round(age, 1)))
    return out


async def apply(
    repo: str | Path,
    policy: Retention,
    *,
    is_running: Callable[[str], bool] | None = None,
    include_unfinished: bool = False,
    remove_worktrees: Callable[[TaskStore], object] | None = None,
) -> list[Pruned]:
    """Remove what `plan` selects. Returns what was actually removed."""
    doomed = plan(repo, policy, is_running=is_running, include_unfinished=include_unfinished)
    for item in doomed:
        store = TaskStore(repo, item.id)
        if remove_worktrees is not None:
            # Detach lane worktrees before the folder holding their state goes.
            await remove_worktrees(store)
        shutil.rmtree(store.dir, ignore_errors=True)
    return doomed


def next_run_delay(hours: int = 24) -> float:
    return timedelta(hours=hours).total_seconds()
