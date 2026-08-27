"""In-memory pub/sub so the dashboard sees events the moment they are written."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from .models import Event


class EventBus:
    def __init__(self, replay: int = 200) -> None:
        self._subs: set[asyncio.Queue[Event]] = set()
        self._recent: list[Event] = []
        self._replay = replay

    def publish(self, event: Event) -> None:
        self._recent.append(event)
        if len(self._recent) > self._replay:
            self._recent = self._recent[-self._replay :]
        for q in list(self._subs):
            # A slow client drops events rather than stalling the run.
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    async def subscribe(
        self, task_id: str | None = None, replay: bool = True
    ) -> AsyncIterator[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        try:
            if replay:
                for e in self._recent:
                    if task_id is None or e.task_id == task_id:
                        yield e
            while True:
                e = await q.get()
                if task_id is None or e.task_id == task_id:
                    yield e
        finally:
            self._subs.discard(q)


BUS = EventBus()
