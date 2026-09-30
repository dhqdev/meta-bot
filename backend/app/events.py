"""Barramento de eventos em tempo real (WebSocket do escritório) e registro de atividade."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("metabot.events")


class EventBus:
    """Pub/sub simples: cada cliente WebSocket recebe uma fila."""

    def __init__(self, max_queue: int = 500):
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._max_queue = max_queue
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        with self._lock:
            subs = list(self._subscribers)
        if not subs:
            return
        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        for q in subs:
            if running is not None and (loop is None or running is loop):
                self._put(q, event)
            elif loop is not None:
                loop.call_soon_threadsafe(self._put, q, event)

    @staticmethod
    def _put(q: asyncio.Queue, event: dict) -> None:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Cliente lento: descarta o mais antigo para não travar ninguém.
            try:
                q.get_nowait()
                q.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass


bus = EventBus()


def record_activity(
    agent: str,
    text: str,
    kind: str = "info",
    level: str = "info",
    data: dict | None = None,
    publish: bool = True,
) -> None:
    """Grava uma linha no histórico de atividade e avisa as telas abertas."""
    from app.db import session_scope
    from app.models import Activity

    ts = datetime.now(timezone.utc)
    row_id = None
    try:
        with session_scope() as s:
            row = Activity(ts=ts, agent=agent, kind=kind, level=level, text=text[:2000], data=data or {})
            s.add(row)
            s.flush()
            row_id = row.id
    except Exception:  # o log nunca pode derrubar um agente
        log.exception("falha ao gravar atividade")
    if publish:
        bus.publish(
            {
                "type": "activity",
                "id": row_id,
                "agent": agent,
                "kind": kind,
                "level": level,
                "text": text,
                "data": data or {},
                "ts": ts.isoformat(),
            }
        )
