"""In-process pub/sub event bus for SSE streaming (Section 10.3)."""

from __future__ import annotations

import asyncio
import time
from typing import Any


class EventBus:
    """Thread-safe / async-safe pub/sub event bus for Server-Sent Events."""

    def __init__(self, max_history: int = 500) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: list[dict[str, Any]] = []
        self._max_history = max_history
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()

    def subscribe(self, replay_history: bool = False) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=2000)
        if replay_history:
            for evt in self._history:
                try:
                    q.put_nowait(evt)
                except asyncio.QueueFull:
                    break
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def emit(self, event_type: str, **payload: Any) -> dict[str, Any]:
        """Emit an event to all subscribers and record non-tick events in history."""
        evt: dict[str, Any] = {"type": event_type, "ts": round(time.time(), 4)}
        evt.update(payload)

        if event_type != "tick":
            self._history.append(evt)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]

        def _deliver() -> None:
            dead: list[asyncio.Queue[dict[str, Any]]] = []
            for q in list(self._subscribers):
                try:
                    if q.full():
                        # Drop oldest item if subscriber is slow so producer never blocks
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    q.put_nowait(evt)
                except Exception:  # noqa: BLE001
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)

        try:
            running_loop = asyncio.get_running_loop()
            if self._loop is None:
                self._loop = running_loop
            _deliver()
        except RuntimeError:
            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(_deliver)
            else:
                _deliver()

        return evt


event_bus = EventBus()
