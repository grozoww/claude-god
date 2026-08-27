"""M7: the notify hook fires on phases that need a human, and never breaks a run."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ccgod.bus import EventBus
from ccgod.config import ServerConfig
from ccgod.models import Phase, Status, TaskState
from ccgod.pipeline import NOTIFY_PHASES, Pipeline
from ccgod.store import TaskStore


def make(tmp_path: Path, **cfg_kw):
    state = TaskState(title="notify me", repo=str(tmp_path))
    store = TaskStore(tmp_path, state.id)
    store.init_dirs()
    store.save(state)
    return store, Pipeline(ServerConfig(**cfg_kw), EventBus())


async def test_notify_runs_on_terminal_phases(tmp_path):
    marker = tmp_path / "fired.txt"
    store, pipe = make(
        tmp_path,
        notify_cmd=f'printf "%s|%s" "$CCGOD_PHASE" "$CCGOD_TASK_TITLE" > {marker}',
    )
    state = store.load()
    state.phase = Phase.REVIEW
    store.save(state)

    pipe._set_phase(store, state, Phase.DONE, Status.DONE)
    for _ in range(50):
        if marker.exists():
            break
        await asyncio.sleep(0.05)
    assert marker.read_text() == "done|notify me"


async def test_notify_is_silent_when_unset(tmp_path):
    store, pipe = make(tmp_path)
    state = store.load()
    state.phase = Phase.REVIEW
    store.save(state)
    pipe._set_phase(store, state, Phase.DONE, Status.DONE)   # must not raise
    assert store.load().phase is Phase.DONE


async def test_a_broken_notify_command_does_not_break_the_run(tmp_path):
    store, pipe = make(tmp_path, notify_cmd="this-command-does-not-exist-xyz")
    state = store.load()
    state.phase = Phase.REVIEW
    store.save(state)
    pipe._set_phase(store, state, Phase.DONE, Status.DONE)
    assert store.load().phase is Phase.DONE


async def test_ordinary_phases_do_not_notify(tmp_path):
    marker = tmp_path / "fired.txt"
    store, pipe = make(tmp_path, notify_cmd=f"touch {marker}")
    state = store.load()
    pipe._set_phase(store, state, Phase.SPEC, Status.RUNNING)
    await asyncio.sleep(0.2)
    assert not marker.exists()
    assert Phase.SPEC not in NOTIFY_PHASES
    assert {Phase.DONE, Phase.ESCALATED, Phase.FAILED,
            Phase.AWAITING_SPEC_APPROVAL} == NOTIFY_PHASES
